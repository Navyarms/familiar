# Phase 1 — Core loop (text in, voice + avatar out)

Status 2026-10-18: working end to end on the dev machine from source. Not yet packaged or tested on a
clean VM (that needs the installer work from Phase 6; the PyInstaller/electron-builder path is stubbed
in the supervisor but not wired to CI).

## What exists

```
apps/shell/        Electron 44 + electron-vite + React 19 + three.js / three-vrm
  src/main/        tray, global hotkeys, overlay/panel/settings windows, brain supervisor, familiar:// avatar protocol
  src/overlay/     VRM (or built-in orb) renderer, WebAudio speech player, lip sync, expression tags, click-through
  src/panel/       chat panel: streaming transcript, state dot, stop, first-run download prompt
  src/settings/    status, hardware tier, model list + download, hotkeys
brain/             Python 3.12 (uv), FastAPI + WebSocket
  familiar_brain/config.py         %LOCALAPPDATA%\Familiar paths + settings.json
  familiar_brain/models/           hardware probe -> tier, pinned manifest (URL/size/SHA256), resumable downloader, runtime unpack
  familiar_brain/llm/              llama-server supervisor (random port, log file), streaming OpenAI-compatible client, prefix warm-up
  familiar_brain/tts/kokoro.py     Kokoro CPU synth -> PCM16 + 50 Hz envelope + coarse visemes from the phoneme string
  familiar_brain/agent/textflow.py incremental [tag] stripper, first-sentence-early chunker, TTS text cleanup
  familiar_brain/agent/turn.py     one turn: LLM stream -> tags -> sentences -> TTS worker -> shell
  familiar_brain/server.py         Brain object: boot, download, engines, turns, interrupt, persona switch, WS dispatch
packages/protocol/ pydantic models (source of truth) -> schema.json -> ts/index.d.ts
personas/wisp/     persona.json, prompt.md, expressions.json (no VRM in the repo; orb fallback)
```

## Run from source

```bash
npm install                 # root workspace: shell + protocol
uv sync --project brain     # brain venv (Python 3.12 via uv)
npm run dev                 # electron-vite dev --watch; the shell spawns the brain with `uv run`
```

First run: the brain probes hardware, picks a tier, and reports what is missing; the panel and the
settings window show a **Download** button (T1 ≈ 3.3 GB: Qwen3-VL-4B Q4_K_M + mmproj + Kokoro +
llama.cpp CPU build). Files go to `%LOCALAPPDATA%\Familiar\models` and `...\runtimes`, resume on
restart, and are SHA256-checked.

Dev overrides in `%LOCALAPPDATA%\Familiar\settings.json`: `tier_override` ("T0".."T4"),
`avatar_override` (absolute VRM path), `custom_chat_gguf`, `llm_threads`, `llm_ctx`, `thinking`.

Debugging: `FAMILIAR_CDP=9222 npm run dev` exposes DevTools protocol; `node apps/shell/scripts/cdp.mjs
panel "<js>"` evaluates in a window. `uv run --project brain python brain/scripts/ws_client.py "text"`
drives the brain without the shell and can save the spoken audio to a wav.

## Measured on the dev machine (T1 forced, CPU only, Ryzen 9 9950X3D)

| Step | Time |
|---|---|
| Brain spawn -> WebSocket connected | ~0.5 s |
| llama-server start to healthy (4B VL + mmproj, 14 threads, ctx 4096) | 2.5–2.8 s |
| Kokoro load + first synth warm-up | ~1.1 s + ~2 s |
| Prefix warm-up (326-token persona prompt) | 0.8 s |
| Boot -> idle, total | ~5–6 s |
| Text sent -> first token (prefix cached) | 0.11 s |
| Text sent -> first audio chunk at the shell | 1.5 s (first sentence ≈ 3 s of speech, synth 0.55 s) |
| Decode | 15–20 tok/s |

Lip sync: the shell schedules each sentence on the AudioContext clock and samples the envelope +
viseme on every frame, so mouth and audio cannot drift. Expression tags (`[happy]`, `[wink]`…) are
stripped from text and applied when their sentence starts playing.

## Decisions made while building

- **Audio plays in the shell** (WebAudio), not the brain. The brain sends PCM16 + envelope per
  sentence over the WebSocket (base64 in JSON, ~200 KB per 3 s sentence on localhost). Exact sync and
  instant barge-in (stop the AudioContext) for free; no sounddevice in the brain.
- **READY is announced only once uvicorn is accepting connections** (`server.started`), and the shell
  retries the WebSocket for a few seconds. Printing READY from the ASGI startup hook raced the shell.
- **Avatar served over `familiar://avatar/current.vrm`** from the main process (privileged scheme with
  CORS), because renderers cannot fetch `file://` from the dev server origin and the overlay should
  not know absolute paths.
- **Hit testing** = main-process cursor poll at 60 Hz + renderer alpha readback (from Phase 0).
- **One model per tier**, Qwen3-VL Instruct (Phase 0 recommendation). Vision is not used yet.
- **Thinking off**, 400 max tokens, temperature 0.7 for chat; tools come in Phase 3.
- Model manager prefers an installer-bundled `runtimes/<cpu|vulkan>/llama-server.exe` and only
  downloads the llama.cpp zip when none is bundled.

## Phase 1 checklist (PLAN.md)

- [x] Monorepo scaffold, protocol package, CI lint/test (`.github/workflows/ci.yml`)
- [x] Brain server, Shell supervisor, token auth
- [x] Model manager: hardware probe, tier selection, first-run download UI
- [x] Hotkey summon → chat panel → LLM stream → Kokoro → playback → lip sync
- [x] Persona pack loader; `wisp` persona; expression tags
- [ ] Exit: install on a clean Windows VM — **not done**; needs the Phase 6 installer. Verified from
      source on the dev machine only.

## Known gaps / follow-ups

- No original Wisp VRM yet; the orb is the fallback. The dev machine uses Seed-san via `avatar_override`.
- Settings UI is read-only except the download button (tier/persona/voice editing is Phase 6).
- Panel auto-hides on blur; no pin yet. No conversation persistence (Phase 5).
- The T0 model (Qwen3-VL-2B) and GPU tiers (Vulkan build, `-ngl 99`) are in the manifest but untested.
- `uiautomation`, STT, wake word are not in the brain's dependencies yet (Phases 2–4).
- The Brain is started with `uv run` in dev; the PyInstaller spec for the real Brain is not written
  (spike 7 proved the approach).
