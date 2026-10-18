# Familiar — Project Plan

An always-on-top AI companion for Windows. Summon it with a hotkey or by voice, talk to it, have it
answer questions or operate your PC, and hear it reply through a locally rendered 3D avatar.
Everything runs on-device. The app ships as a single installer anyone can use, and the character,
voice, and personality live in swappable **persona packs**.

> Status: Phases 0–5 done; Phase 6 built from source (settings, persona manager, installer, updater, docs) and waiting on the public repo + first release. Notes per phase in docs/.
> Next step is Phase 1 (core loop). Nothing committed yet.

---

## 1. Decisions locked (2026-10-17)

| Area | Decision | Why |
|---|---|---|
| Avatar | **3D VRM models** rendered with three.js + `@pixiv/three-vrm` | Open format, thousands of free models, anyone can make one in VRoid Studio, lip sync and expressions are standard |
| Hardware floor | **Any modern PC, CPU only**, with GPU tiers that unlock bigger models | Maximizes who can install it |
| PC control | **Full desktop automation**: apps, windows, keyboard, mouse, screen vision | The assistant should be able to operate any app, not just a fixed list |
| Voice | **Kokoro** (CPU, built-in voices) by default, **voice cloning optional** as a GPU add-on | Safe to share; cloning is opt-in for users who want it |
| Stack | **Electron shell + Python brain** | Most proven libraries for every piece |
| Distribution | **Open source on GitHub**, MIT license, installer via GitHub Releases, auto-update | Community persona packs, contributions |
| Memory | **Persistent local memory** across sessions | Persona feels like it knows you; stored on disk, user can view and delete |
| Platform | **Windows first.** macOS/Linux later | Desktop automation is OS-specific; UI Automation is Windows |
| Name | App = **Familiar**. Default persona = **Wisp** (original character). Toga = the author's private pack | App name stays neutral; public build never ships copyrighted characters |
| Models | Downloaded on first run, not bundled in the installer | Small installer, clean licensing, hardware-matched tier |
| External deps | **None.** The app bundles its own LLM runtime, STT, TTS, and wake-word engine | A user installs one thing and it works, offline |

---

## 2. Architecture

### 2.1 Process model

```
┌─────────────────────────────── Electron (TypeScript) ───────────────────────────────┐
│  Main process                                                                      │
│   • tray icon, global hotkeys, window management, auto-update                      │
│   • spawns and supervises the Brain, holds the auth token                           │
│                                                                                    │
│  Renderer: Overlay window      Renderer: Chat panel       Renderer: Settings        │
│   transparent, frameless,       text input, transcript,     personas, models,       │
│   always-on-top, click-through  tool-confirm cards          hotkeys, permissions    │
│   three.js + three-vrm avatar                                                       │
└──────────────────────────────────────┬─────────────────────────────────────────────┘
                                       │ WebSocket (localhost, random port, token auth)
┌──────────────────────────────────────┴─────────────────────────────────────────────┐
│  Brain (Python, packaged with PyInstaller)                                          │
│   FastAPI + WebSocket server                                                        │
│   ├─ Audio I/O (sounddevice)  → VAD (Silero) → Wake word (sherpa-onnx KWS)          │
│   ├─ STT (faster-whisper, CTranslate2, int8 on CPU / fp16 on GPU)                   │
│   ├─ LLM client → llama-server (bundled llama.cpp binary: CUDA / Vulkan / CPU)      │
│   ├─ Agent loop: tool calling, confirmation gates, kill switch                      │
│   ├─ Tools: system, windows, input, screen+vision, files, web, clipboard, timers     │
│   ├─ TTS (kokoro-onnx) → sentence-level streaming → audio chunks + viseme timing     │
│   ├─ Memory (SQLite + local embeddings)                                             │
│   └─ Model manager (hardware probe, tiers, download with resume, SHA256)             │
└────────────────────────────────────────────────────────────────────────────────────┘
```

Why a separate Brain process: every AI library we need is best in Python, and a crash in the
model stack must not take down the overlay. The shell restarts the Brain if it dies.

### 2.2 Shell ↔ Brain protocol

JSON messages over one WebSocket. The schema lives in `packages/protocol` and is shared by both sides.

Brain → Shell:
- `state` — `idle | listening | thinking | speaking | executing | needs_confirm`
- `transcript.partial` / `transcript.final` — what the user said
- `reply.token` — streamed text
- `reply.audio` — PCM chunk + sentence id + viseme/amplitude timeline for lip sync
- `expression` — tag from the persona's expression map (`happy`, `smug`, `thinking`, …)
- `tool.request_confirm` — tool name, args, human-readable description, risk level
- `tool.result` — success/failure summary for the transcript
- `model.download_progress`, `model.ready`, `error`

Shell → Brain:
- `summon` (hotkey pressed), `ptt.start` / `ptt.stop`, `text.send`, `interrupt` (barge-in / stop)
- `tool.confirm` / `tool.deny`
- `persona.switch`, `settings.update`, `kill_switch`

### 2.3 Voice loop

```
mic → VAD → wake word "hey <persona>" ──┐
hotkey summon ──────────────────────────┼──► LISTENING → STT (stream) → end-of-speech
push-to-talk hotkey ────────────────────┘
      → LLM stream → sentence chunker → TTS per sentence → playback queue → avatar lip sync
      → barge-in: user speech during playback cancels TTS and LLM
```

Latency budget on the CPU tier (target, measured in Phase 0):

| Stage | Target |
|---|---|
| Wake word → listening state | < 300 ms |
| End of speech → transcript | < 700 ms |
| Transcript → first token | < 800 ms |
| First sentence → first audio | < 500 ms |
| **Wake word → first spoken word** | **< 2.5 s** |

### 2.4 Avatar

- Renderer: three.js + `@pixiv/three-vrm`, supports VRM 0.x and 1.0.
- Lip sync: amplitude envelope of the TTS audio drives `aa/ih/ou/ee/oh` blendshapes; phoneme timing
  from Kokoro when available for sharper visemes.
- Idle: breathing, blink, subtle head sway, optional VRMA idle animations from the pack.
- Expressions: the LLM emits inline tags like `[happy]` or `[smug]`. The Brain strips them from
  the spoken text and sends `expression` events; the pack's `expressions.json` maps tags to VRM
  expression names so any model works.
- Overlay behavior: click-through everywhere except the avatar's bounding box; drag to reposition;
  size/opacity in settings; auto-hide while a fullscreen game is focused (optional).

### 2.5 Persona pack format

A pack is one folder (or zip) the user can share:

```
personas/<id>/
  persona.json       # name, wake phrase, hotkey override, voice settings, expression map ref
  prompt.md          # system prompt: personality, speech style, rules
  avatar.vrm         # the model (or a URL/instructions if license forbids redistribution)
  expressions.json   # tag → VRM expression name, intensity, decay
  voice/
    kokoro.json      # voice id, speed, pitch shift         (default path)
    reference.wav    # optional, for the cloning add-on
  idle/*.vrma        # optional idle animations
  thumbnail.png
```

The repo ships `personas/wisp` (original). The author's `personas/toga` is private and lives
outside the public repo.

### 2.6 LLM runtime and model tiers

The Brain bundles `llama-server` from llama.cpp in three builds and picks at startup:
CUDA (NVIDIA), Vulkan (AMD / Intel / NVIDIA fallback), CPU (AVX2). Models are GGUF files from
Hugging Face, downloaded on first run with resume + SHA256, stored in `%LOCALAPPDATA%\Familiar\models`.

Hardware probe: VRAM (nvidia-smi / DXGI adapter memory), system RAM, CPU cores + AVX2/AVX-512.

| Tier | Hardware | Chat model (candidate) | STT | TTS | Vision |
|---|---|---|---|---|---|
| T0 | CPU, 8 GB RAM | Qwen3 1.7B Q4 | whisper base int8 | Kokoro | on-demand small VL |
| T1 | CPU, 16 GB+ RAM | Qwen3 4B Q4 | whisper small int8 | Kokoro | on-demand small VL |
| T2 | GPU 6–8 GB | Qwen3 8B Q4 | whisper small | Kokoro | small VL |
| T3 | GPU 12–16 GB | Qwen3 14B Q4 | whisper medium / large-v3-turbo | Kokoro | VL 4B |
| T4 | GPU 24 GB+ | Qwen3 32B or 30B-A3B Q4 | large-v3-turbo | Kokoro | VL 8B |

**Decided in Phase 0: one multimodal model per tier, Qwen3-VL-Instruct** (4B Q4_K_M + mmproj Q8_0 on
T1/T2, 8B on T3/T4, evaluate 2B for T0). Same decode speed and footprint as the text-only Qwen3,
better tool calling in the spike (17/20 vs 15/20), no second model to juggle. Thinking mode stays off
on CPU tiers (5 to 27 s per tool call). Screen vision costs 15 to 20 s per screenshot on a fast CPU,
so it is a fallback behind UI Automation and inputs are cropped to the active window. Details and
the 20-prompt chat-quality comparison still owed in Phase 1: `docs/phase0-results.md` section 3.

Users can override the tier and point at any GGUF.

### 2.7 Agent loop and tools

Tool calling uses llama-server's OpenAI-compatible API with native function calling for Qwen3.
Fallback for small models that call tools unreliably: constrained JSON via GBNF grammar.

Tool groups (v1):

| Group | Tools | Default permission |
|---|---|---|
| system | open/close app, volume, mute, media keys, brightness, lock, sleep, shutdown | auto, except sleep/shutdown = confirm |
| windows | list, focus, move, resize, minimize, maximize, snap | auto |
| input | type text, press hotkey, click (x,y or UI element), scroll, drag | auto, with kill switch always armed |
| screen | screenshot → vision model description; find UI element by text via Windows UI Automation | auto |
| files | search, open, read, write, move within user-approved roots | write/move = confirm |
| web | search (DuckDuckGo HTML, no key), fetch page text | auto, togglable (only network use in the app) |
| clipboard | read, write | auto |
| timers | reminders, alarms | auto |
| memory | remember, recall, forget | auto |
| shell | run command | **confirm**, off by default |

Safety model:
- Three permission levels per tool: `auto`, `confirm`, `blocked`. Defaults above, editable in Settings.
- Confirmation card in the chat panel shows exactly what will happen before it happens.
- **Kill switch** hotkey (default `Ctrl+Alt+Esc`) aborts any running tool chain and releases keys/mouse.
- Automation is paused while the user is actively typing/moving the mouse (interference guard).
- Screen vision for operating apps: screenshot → vision model → plan → UI Automation element lookup
  first, raw coordinate click only as fallback.

### 2.8 Memory

- SQLite database per user profile: `facts` (atomic statements with timestamps and source),
  `episodes` (conversation summaries), `preferences`.
- Embeddings from a small local model (bge-small or nomic-embed via fastembed/ONNX).
- Each turn: retrieve top-k facts + recent episode summary into the system prompt.
- After a conversation goes idle: summarize it into an episode and extract new facts.
- Settings → Memory shows everything stored, with search and delete. Nothing leaves the machine.

### 2.9 Customization surface (Settings UI)

Persona manager (import zip, switch, duplicate, edit prompt/voice/expressions, create from template) ·
hotkeys (summon, push-to-talk, kill switch) · voice (Kokoro voice picker with preview, speed, pitch;
cloning add-on toggle) · models (tier override, custom GGUF, re-download) · tool permissions ·
avatar (size, position, opacity, click-through, hide-in-game) · privacy (web search on/off, mic
indicator, memory on/off) · startup with Windows.

---

## 3. Repository layout

```
familiar/
  apps/shell/          Electron + Vite + React + TypeScript
    src/main/          tray, hotkeys, windows, brain supervisor, updater
    src/overlay/       three.js VRM renderer, lip sync, expressions
    src/panel/         chat panel, confirm cards
    src/settings/      settings UI
  brain/               Python 3.12, managed with uv
    familiar_brain/
      server.py        FastAPI + WebSocket
      audio/           capture, VAD, wake word, playback
      stt/  llm/  tts/ agent/  tools/  memory/  models/
    pyinstaller.spec
  packages/protocol/   JSON schema for Shell↔Brain messages, generated TS + Python types
  personas/wisp/       default original persona
  runtimes/            downloaded at build time: llama-server (cuda/vulkan/cpu), onnxruntime
  installer/           electron-builder config (NSIS), icons
  docs/                user guide, persona pack spec, tool safety, contributing
  .github/workflows/   CI: lint + test; release: build brain (PyInstaller) + shell, publish
```

Toolchain present on the dev machine: Node 24, Python 3.10 (Brain will target 3.12 via uv), uv 0.11, git.

---

## 4. Phases

Phase-gate rule: don't start a phase until the previous one is boringly reliable.

### Phase 0 — Spikes (de-risk, ~1 week)
Throwaway prototypes that answer the questions the architecture depends on.
- [x] Electron transparent always-on-top window with a VRM at 60 fps, click-through outside the avatar, on Windows 11 (240 fps; hit test = cursor poll + alpha readback)
- [x] llama-server CPU: Qwen3 4B Q4 tokens/sec, time-to-first-token, and tool-call reliability on 20 sample tasks (19 tok/s; TTFT 0.15 s warm / 2 to 4 s cold; 15/20)
- [x] Qwen3-VL single-model vs Qwen3 + separate VL: chat quality and screenshot understanding, VRAM/RAM use (single VL chosen)
- [x] Kokoro latency per sentence on CPU; viseme/phoneme timing availability (0.15 to 0.4 s per short sentence; no duration output, use amplitude envelope)
- [x] sherpa-onnx keyword spotting: false accepts over 1 hour of room noise, detection rate for "hey Wisp" (0 false accepts synthetic; 86% detection, misses on British voices)
- [x] faster-whisper small int8 on CPU: latency for a 5 s utterance (0.81 s small, 0.28 s base)
- [x] Windows UI Automation: list and click elements of Explorer, Settings by name (20 to 100 ms lookups; Chrome untested)
- [x] PyInstaller one-folder build of a Brain stub + Electron spawn/supervise it (38 MB, 0.7 s warm start, respawn after crash works)
- **Exit:** latency table filled on a fast desktop CPU, model strategy chosen. Still open and carried into Phase 1: laptop-class CPU timings, iGPU overlay fps, real-mic wake word in real room noise, Chrome UIA
- **Design notes carried forward:** pre-warm the prompt cache with persona + tool schemas (the cold-prefix TTFT of 2 to 4 s breaks the latency budget otherwise); prompt order [persona][tools][memory][time][user]; first sentence cut at first clause boundary; STT streams over VAD segments; Brain prints READY from the ASGI startup hook with a stdin-EOF orphan guard

### Phase 1 — Core loop (text in, voice + avatar out)
- [x] Monorepo scaffold, protocol package, CI lint/test
- [x] Brain server, Shell supervisor, token auth
- [x] Model manager: hardware probe, tier selection, first-run download UI
- [x] Hotkey summon → chat panel → LLM stream → Kokoro → playback → lip sync
- [x] Persona pack loader; `wisp` persona; expression tags
- **Exit:** install on a clean Windows VM, download models, type a question, Wisp answers aloud with lip sync — *works from source on the dev machine; clean-VM install waits for the Phase 6 installer*

### Phase 2 — Voice in
- [x] Mic capture, Silero VAD, wake word, push-to-talk
- [x] Streaming STT with end-of-speech detection
- [x] Barge-in and interrupt (wake phrase / hotkey; voice-activity barge-in opt-in because of speaker echo)
- [x] Mic-active indicator on the avatar
- **Exit:** hands-free conversation for 10 minutes without a false wake or a missed wake in a quiet room — *verified synthetically (virtual-cable loopback, see docs/phase2-voice-in.md); the human 10-minute session is still to do*

### Phase 3 — Safe tools
- [x] Tool registry, permission levels, confirmation cards, kill switch
- [x] system, windows, files, web, clipboard, timers, memory tools (+ weather; shell blocked by default)
- **Exit:** "open Spotify and turn the volume to 30" and "find the PDF I downloaded yesterday" work by voice — *verified through the virtual-cable loopback with Notepad standing in for Spotify (docs/phase3-safe-tools.md)*

### Phase 4 — Full desktop automation
- [x] input tools (type, hotkeys, click, scroll; drag implemented, not exposed) with interference guard
- [x] screen tool: screenshot → vision model; UI Automation element lookup (find/click/set/read by label)
- [x] Multi-step agent loop with progress narration ("opening settings… clicking Bluetooth…")
- **Exit:** "turn on Bluetooth" and "reply to the open email saying I'll be late" complete without help on a clean machine — *Bluetooth passes by voice (loopback); the email chain is untested against a real mail client (docs/phase4-desktop-automation.md)*

### Phase 5 — Memory
- [x] SQLite schema, embedding model download, retrieval into prompt
- [x] Idle-time summarization and fact extraction
- [x] Settings → Memory viewer with delete
- **Exit:** tell it your dog's name, restart the PC, it remembers — *verified across two brain processes (docs/phase5-memory.md)*

### Phase 6 — Polish and ship v0.1
- [x] Settings UI complete, persona manager, voice preview
- [x] NSIS installer, auto-update via GitHub Releases (wired; activates once the repo exists), startup-with-Windows
- [x] Docs: user guide, persona pack spec, tool safety
- [x] Code signing decision: unsigned for v0.1, Azure Trusted Signing when there is a home (docs/phase6-ship.md)
- [ ] Public GitHub repo, MIT license, first release — *needs the owner's GitHub account; release workflow is ready*
- **Exit:** a stranger installs it from the Releases page and has a working Wisp in under 10 minutes — *pending the release and a clean-VM test*

### Phase 7 — Optional add-ons
- [ ] Voice cloning add-on (evaluate Chatterbox, F5-TTS, GPT-SoVITS), GPU only, opt-in download
- [ ] macOS / Linux shells (automation layer per OS)
- [ ] Community persona pack gallery

---

## 5. The author's Toga pack (private, not in the repo)

- Avatar: a Toga VRM from VRoid Hub or Booth. Check the model's license allows personal use and
  modification. Never redistribute it.
- Prompt: playful, unhinged-but-helpful voice layer. Persona governs tone only; task execution stays accurate.
- Voice: closest Kokoro voice with pitch/speed tuned; cloning add-on later if desired, for personal use.
- Wake phrase: "hey Toga". Expression map tuned to the model's available blendshapes.

---

## 6. Risks and mitigations

| Risk | Mitigation |
|---|---|
| CPU tier too slow to feel conversational | Sentence-level TTS streaming, short-answer prompt on T0/T1, show "thinking" animation, let users pick tier |
| Small models call tools unreliably | GBNF-constrained JSON fallback, few-shot tool examples in prompt, retry with repair |
| VRAM contention between LLM, VL, STT, TTS on GPU tiers | STT/TTS stay on CPU on T2; single multimodal model if Phase 0 favors it; load VL on demand |
| Automation does something harmful | Permission levels, confirm cards, kill switch, interference guard, shell tool off by default |
| Wake word false accepts | Tune KWS threshold, require VAD speech, show mic indicator, easy sensitivity slider |
| PyInstaller builds flagged by antivirus / SmartScreen | Code signing (Azure Trusted Signing), reproducible CI builds, published hashes |
| VRM licenses vary | App never bundles third-party models; pack spec records license; default persona is original |
| Live2D users want 2D | Out of scope for v1; sprite renderer could be added as a second avatar type later |
| Electron bundle size | Accepted trade-off (~250 MB before models); Tauri migration possible later since the Brain is separate |

---

## 7. Immediate next step

Start Phase 0. Suggested order: overlay + VRM spike first (it's the thing people see), then the
llama-server CPU benchmark (it decides the tier table), then Kokoro and wake word.
