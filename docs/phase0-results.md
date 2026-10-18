# Phase 0 results (spikes)

Measured 2026-10-17/18 on the dev machine, **CPU paths forced everywhere** (no CUDA, `-ngl 0`,
onnxruntime CPU provider) because the app must work on CPU-only PCs.

Machine: AMD Ryzen 9 9950X3D (16 cores / 32 threads, Zen 5, AVX-512), 64 GB DDR5, Windows 11 Home
26200, 240 Hz primary display. The RTX 5090 was not used by any spike except that the Electron
overlay is GPU-composited like any window.

**Caveat on representativeness.** This CPU is far faster than the T0/T1 target hardware. LLM decode
is memory-bandwidth bound (the 4-thread run decodes as fast as 16 threads), so a dual-channel laptop
will likely land at roughly half the tokens/sec below. Prompt processing scales with cores, so the
cold-prefix numbers will be 2 to 4x worse on a 4-core laptop. Treat the table as an upper bound and
re-measure on a laptop in Phase 1.

Spike code: `spikes/<name>/` (see `spikes/README.md`). Raw numbers: each spike's `results*.json`.

## 1. Latency table (PLAN.md section 2.3, CPU tier)

| Stage | Target | Measured (16 threads unless noted) | Verdict |
|---|---|---|---|
| Wake word → listening state | < 300 ms | ~200 to 300 ms after the phrase ends (100 ms audio chunks, decode RTF 0.015) | Met |
| End of speech → transcript (5 s utterance) | < 700 ms | whisper **small** int8, 8 threads: **810 ms** (batch, whole clip). whisper **base** int8: **280 ms** | Base meets it; small misses by 110 ms in batch mode. Streaming STT (decode while the user talks, only the tail after end-of-speech) should bring small under target |
| Transcript → first token (Qwen3 4B Q4_K_M) | < 800 ms | **Prefix cached** (system prompt + tools already processed): **120 to 170 ms** (8 to 16 thr), 250 ms at 4 thr. **Cold prefix** (932-token system prompt processed from scratch): **2.0 to 2.9 s** at 16 thr, 2.6 to 2.7 s at 8 thr, 4.1 to 4.4 s at 4 thr | Met only with a warm prompt cache. Hard requirement: pre-warm the persona prompt + tool schema at startup and after every persona switch, and keep dynamic content (time, retrieved memories) at the *end* of the prefix |
| First sentence → first audio (Kokoro, CPU) | < 500 ms | 145 to 290 ms for short sentences (< 2 s of audio); 540 ms for a 4.9 s sentence; 970 ms for 8 s. RTF 0.11 to 0.14 | Met, provided the sentence chunker emits a short first sentence |
| **Wake word → first spoken word** | **< 2.5 s** | Sum after the user stops talking: KWS 0.3 + STT 0.8 + TTFT 0.15 + first sentence of ~10 tokens at 19 tok/s 0.5 + TTS 0.26 ≈ **2.0 s** (warm cache). ≈ 4 to 5 s with a cold prefix | Met on this CPU with a warm cache; at risk on laptops. Mitigations in section 3 |

LLM decode speed, Qwen3-4B-Q4_K_M, llama.cpp b11717 CPU build (`-fa on`, ctx 8192):

| Threads | Prompt processing (932 tok) | Decode | Cold-prefix TTFT | Cached-prefix TTFT |
|---|---|---|---|---|
| 16 | 325 to 458 tok/s | 17.5 to 21 tok/s | 2.0 to 2.9 s | 0.12 s |
| 8 | 347 to 364 tok/s | 19.4 to 21.4 tok/s | 2.6 to 2.7 s | 0.17 s |
| 4 | 211 to 227 tok/s | 19.2 to 19.6 tok/s | 4.1 to 4.4 s | 0.25 s |

Run-to-run variance was large when anything else used the CPU (a later re-run while the desktop was
busy showed 7.7 tok/s decode and 98 tok/s prompt processing on single runs). The Brain should
pin `-t` to physical cores and the UI should expect spikes.

## 2. Per-spike findings

### 2.1 Overlay: Electron + three.js + three-vrm (`spikes/01-overlay-vrm`)

- Transparent, frameless, always-on-top (`screen-saver` level), no taskbar entry, renders Seed-san
  (VRM 0.x) with idle sway, blink, expression and a fake lip-sync `aa` cycle.
- **FPS: 240 avg, 203 min** over 18 s = locked to the monitor's refresh. 60 fps is not a concern on
  a discrete GPU; an iGPU laptop is untested.
- **Click-through works and toggles correctly.** Verified programmatically: `WS_EX_TRANSPARENT` is set
  when the cursor is over empty pixels and cleared when it is over the avatar.
- Lesson: `setIgnoreMouseEvents(true, {forward: true})` did not deliver mousemove events reliably,
  so the hit test cannot live on renderer mouse events. Working design: the main process polls
  `screen.getCursorScreenPoint()` at 60 Hz, sends window-relative coords to the renderer, the
  renderer does a 1-pixel `gl.readPixels` alpha test and replies; the main process calls
  `setIgnoreMouseEvents` only on change.
- VRM 0.x models need `VRMUtils.rotateVRM0` to face the camera; arm bones must be rotated out of
  T-pose (left `rotation.z = -1.2`, right `+1.2`) or the idle pose looks wrong.
- `backgroundThrottling: false` is required or the overlay drops to a few fps when unfocused.
- License note: Seed-san is VRM Public License 1.0 (VirtualCast), fine for a spike, not CC0. The
  public `wisp` persona still needs an original model.

### 2.2 llama-server CPU benchmark (`spikes/02-llm-bench`)

Setup: `llama-server --jinja -fa on -c 8192 -ngl 0`, OpenAI-compatible `/v1/chat/completions`,
temperature 0, `chat_template_kwargs.enable_thinking` to switch Qwen3 thinking on/off.

**Tool-call reliability, 20 single-step desktop tasks, 12 tool schemas (~600 tokens):**

| Model / mode | Correct | Median latency per call | Failures |
|---|---|---|---|
| Qwen3-4B, thinking off | **15 / 20** | 1.1 s | "Skip this song" → `previous`; "Type 'see you at 5'" → `clipboard_write`; "Save this file" → no tool; "Show me the desktop" → `take_screenshot`; "weather in Seattle" → answered without `web_search` |
| Qwen3-4B, thinking on | 17 / 20 | **7.2 s** (5 to 27 s) | "Save this file", "Show me the desktop", "last World Cup" → no tool |
| Qwen3-VL-4B-Instruct, no thinking mode | **17 / 20** | 1.3 s | "Bring up the Chrome window" → `open_app`; "Save this file" and "weather" → no tool |

- Every tool call the models emitted was **well-formed JSON with the declared argument names**
  (llama.cpp's native Qwen3 tool parser). All failures were semantic (wrong tool or no tool), not
  format errors, so the GBNF fallback from the plan is not needed for 4B; prompt engineering is.
- Thinking mode is unusable on the CPU tier for tool use (5 to 27 s per call). Keep it off and
  recover accuracy with better tool descriptions (e.g. list hotkey examples in `press_hotkey`), a
  rule "for anything time-sensitive use web_search", and 3 to 5 few-shot examples.
- The first request after a model load processed the prompt at 63 tok/s (page-in from disk); warm
  up with the real persona prefix at startup, not with "hi".

**Qwen3-VL-4B on CPU (same run, with `--mmproj` Q8_0):**

| Input | Prompt tokens | Prompt processing | Total to 160 to 200 token answer |
|---|---|---|---|
| Text only (932 tok) | 928 | 232 to 359 tok/s, TTFT 2.6 to 4.1 s cold / 0.11 s cached | decode 17 to 18.5 tok/s |
| Settings window screenshot 1408x862 | 1219 | **18.1 s** | 26.5 s |
| Full-desktop screenshot 1920x1080 | 2074 | 14.7 s | 26.1 s |

The Settings description was accurate: it named the page, the Bluetooth toggle, "Add device",
"View all devices" and the recommended-settings tiles with correct relative positions. The desktop
screenshot (a game) was described correctly too. **Screen vision costs 15 to 20 s per screenshot on
this CPU** (vision encoder on CPU dominates), so it will be 30 to 60 s on a laptop.

Memory: text model 2.4 GB on disk; VL model 2.4 GB + 0.45 GB projector. `llama-server` with 4 slots
of 8192 unified context sat at 6.4 GB RSS for the VL model; use one slot and a 4096 context on T0/T1.

### 2.3 Kokoro TTS (`spikes/03-kokoro-tts`, kokoro-onnx 0.6.1, kokoro-v1.0.onnx fp32, voice af_heart)

| Sentence | Audio | Latency | RTF |
|---|---|---|---|
| "Done." | 0.66 s | 145 ms | 0.22 |
| "Sure, opening Spotify now." | 1.86 s | 263 ms | 0.14 |
| "Okay, I'll remind you in ten minutes to check the oven." | 3.11 s | 384 ms | 0.12 |
| 16-word explanation | 4.86 s | 539 ms | 0.11 |
| 27-word answer | 8.02 s | 971 ms | 0.12 |

- Model load 0.8 s. The int8 model (88 MB vs 310 MB) was not measured; try it for T0.
- `create_stream` yields whole sentences, not sub-sentence chunks (first chunk after 900 ms for the
  long sentence), so our own sentence chunker is the right streaming unit.
- **Phoneme timing:** the phoneme string is available (`tokenizer.phonemize` → IPA, e.g.
  `ʃˈʊɹ, ˈoʊpənɪŋ spˈɑːɾᵻfˌaɪ nˈaʊ.`), but the published ONNX graph has a single `audio` output and
  **no duration output**, so `create_timed()` returns zero timings. No "timed" model is published in
  the kokoro-onnx releases. Options, in order: (a) ship the amplitude envelope at 50 Hz for lip sync
  (already computed in the spike, good enough for `aa` mouth opening) plus phoneme-to-viseme mapping
  spread proportionally over the sentence duration for vowel shapes; (b) later, re-export Kokoro to
  ONNX with the duration predictor exposed (the PyTorch `kokoro` package returns per-token durations).
  Phase 1 goes with (a).

### 2.4 Wake word: sherpa-onnx keyword spotting (`spikes/04-wakeword`)

Model: `sherpa-onnx-kws-zipformer-gigaspeech-3.3M` (English, int8, 2 threads), keyword
`▁HE Y ▁W IS P :2.0 #0.25 @HeyWisp` built from the model's BPE.

- **Decode cost: RTF 0.015** (1.5% of one core), fine to run continuously.
- **Detection: 62 / 72 (86%)** synthetic "hey Wisp" utterances (8 Kokoro voices x 3 speeds x 3
  phrasings). All US voices detected at every speed; the misses were the two British voices
  (bf_emma, bm_george: 7 of 10 misses) plus 3 one-offs. 2 / 4 detected at 10 dB white-noise SNR.
  Lowering the threshold from 0.25 to 0.15 changed nothing, so the limit is the acoustic model's
  accent coverage, not the threshold.
- **False accepts: 0** over 19 adversarial phrases (57 s: "Hey Siri", "Hey Lisa", "Hey, is Wisconsin
  far?", "the wisps of smoke", "whisk", "lisp", ...) and **0 over 1 hour of synthetic room noise**
  (pink noise + 60 Hz hum + clicks). Real room noise was not recorded; `bench.py --mic 60` runs the
  live-mic test and `negatives/*.wav` are scanned if present.
- Format gotcha: the `@display` field must not contain spaces or the whole keyword fails to encode.
- Recommendation: good enough for Phase 2 with a sensitivity slider; add a VAD gate; if accent
  coverage becomes a complaint, evaluate openWakeWord (trainable on synthetic data) or sherpa's
  newer zh-en KWS model.

### 2.5 STT: faster-whisper (`spikes/05-stt`, CTranslate2 int8, 8 CPU threads, 5.0 s synthetic clip)

| Model | Load (incl. first download) | Latency, greedy | Latency, beam 5 | Transcript |
|---|---|---|---|---|
| small int8 | 8.5 s | **0.81 s** | 0.83 s | exact ("Hey Wisp, can you open my downloads folder and find the PDF I saved yesterday?") |
| base int8 | 3.7 s | **0.28 s** | 0.28 s | one spelling slip ("Whisp") |

Beam size barely matters on CPU; language pinned to `en` and `without_timestamps=True`. For T1 keep
small; use streaming decode over VAD segments so only the last chunk is paid after end-of-speech.
T0 uses base.

### 2.6 Windows UI Automation (`spikes/06-uia`, `uiautomation` 2.0.29)

| Step | Time |
|---|---|
| Launch Explorer and find its window | 0.14 to 0.94 s |
| Enumerate 57 to 60 named interactive elements (tree items, buttons) | 0.20 to 0.38 s |
| Find nav-pane item by name ("Downloads") | 55 to 95 ms |
| Navigate via `SelectionItemPattern.Select()` (no mouse) + settle | 1.5 s |
| "Back" via `InvokePattern` | 0.1 to 1.3 s |
| Launch Settings (WinUI 3) to a usable window | 2.3 to 2.5 s |
| Enumerate 49 to 50 interactive elements | 0.17 s |
| Find "Bluetooth & devices" by name | 21 to 28 ms |
| Click it + page settle | 2.1 s |
| Find the "Bluetooth" toggle and read `TogglePattern` state (not flipped) | 0.12 s |

- Both apps are fully driveable by element name. Element lookup is 20 to 100 ms, two orders of
  magnitude cheaper than a screenshot through the VL model, which confirms the plan's "UIA first,
  vision fallback" order.
- Gotchas: Explorer's pinned items are named "Downloads (pinned)" (match by substring); WinUI pages
  throw transient `COMError`s while loading (retry for ~2 s); use the typed wrappers
  (`ButtonControl`) to get pattern helpers; `Select()`/`Invoke()` avoid moving the user's mouse.
- Chrome was not tested (not installed on this machine's default profile path during the spike);
  Chrome exposes the same UIA tree when accessibility is enabled, test in Phase 4.

### 2.7 PyInstaller Brain stub + Electron supervisor (`spikes/07-pyinstaller-stub`)

- One-folder build of FastAPI + uvicorn + websockets stub: **45 s build, 38 MB, 389 files**
  (`--collect-all` for fastapi/uvicorn/starlette/anyio/websockets).
- Spawn → `READY <port>` on stdout: **1.9 s** first launch, **0.7 s** warm. Health over HTTP, then
  WebSocket with a random token: **round trip 1.5 ms**; wrong token is refused (close 4401).
- Crash path: forced `os._exit(3)`, supervisor saw the exit after **115 ms**, respawned, new
  instance ready in 0.7 s. Orphan guard: the stub exits when its stdin pipe closes, so a dead shell
  never leaves a Brain behind.
- Gotchas: print `READY` from the ASGI startup hook (printing before `uvicorn.run` races the first
  connection); uvicorn needs `websockets` installed explicitly or `/ws` fails with 1006.

## 3. Model strategy recommendation

**Use one multimodal model, Qwen3-VL-4B-Instruct (Q4_K_M + mmproj Q8_0), for T1 and above.**

Why:
- Same decode speed within 10% (17 to 18.5 vs 19 to 21 tok/s) and same disk/RAM footprint plus a
  0.45 GB projector that is only exercised when an image is in the prompt.
- Tool calling was **better** in this test (17/20 vs 15/20), with no thinking overhead because the
  Instruct variant has none (thinking is too slow on CPU anyway).
- One model to download, one llama-server process, no VRAM/RAM juggling and no second load when the
  user asks "what's on my screen", which keeps the "operate my PC" flow simple.
- Apache 2.0, same as Qwen3.

Conditions and follow-ups:
- Screen vision on CPU costs 15 to 20 s per screenshot here (30 to 60 s on laptops). Vision must stay
  a fallback behind UI Automation, screenshots should be cropped to the active window and downscaled
  to ~1280 px wide, and the UI must narrate "looking at the screen" while it runs.
- Chat quality was only sampled (short explanations read fine from both). Run a 20-prompt blind
  comparison in Phase 1 before locking the default tier table.
- T0 (8 GB RAM): evaluate Qwen3-VL-2B-Instruct as the single model so the code path stays one; if its
  tool calling is poor, fall back to Qwen3-1.7B text + VL-2B loaded on demand.
- For GPU tiers the same single-model choice scales (VL-8B at T3/T4), so the model manager needs one
  model slot per tier, not two.

## 4. Exit criteria check

| Criterion | Status |
|---|---|
| Latency table filled in | Done (section 1), on a fast desktop CPU; laptop numbers pending |
| Model strategy chosen | Single multimodal Qwen3-VL (section 3) |
| No unknowns left in the stack | Remaining: iGPU overlay fps, real-mic wake word over real room noise, Chrome UIA, laptop-class CPU timings, Kokoro phoneme timing (envelope workaround chosen) |

## 5. Decisions and design notes carried into Phase 1

1. Pre-warm the llama-server prompt cache with the persona prefix + tool schemas at startup and on
   persona switch; order the prompt as [persona][tools][memory][time][user] so the cached span is
   maximal. Pin `-t` to physical cores, one slot, 4096 ctx on T0/T1.
2. Thinking off for all CPU tiers. Tool descriptions carry hotkey examples; prompt carries a
   "time-sensitive → web_search" rule and few-shot examples; recovery = retry with the model told
   which tool it should have picked.
3. Lip sync = 50 Hz amplitude envelope + proportional phoneme-to-viseme hints.
4. Sentence chunker cuts the first sentence at the first clause boundary to keep first audio < 500 ms.
5. Overlay hit test = main-process cursor poll + renderer alpha readback.
6. UIA first, vision fallback; vision input cropped to the active window.
7. STT streams over VAD segments; small on T1, base on T0.
8. Brain prints READY from the ASGI startup hook; shell watches exit and stdin-EOF orphan guard.
