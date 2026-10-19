# Familiar

An always-on-top AI companion for Windows that runs entirely on your machine.

Summon it with a hotkey or your voice. Ask it anything, or have it operate your PC. It answers out
loud through a 3D VRM avatar you choose. Character, voice, and personality are **persona packs** you
can swap or share.

- **On-device:** local LLM (llama.cpp), speech-to-text (whisper), text-to-speech (Kokoro), wake word. No cloud, no keys.
- **Runs on any modern PC:** CPU-only works; a GPU unlocks bigger models automatically.
- **Does things:** opens apps, controls windows, types, clicks, reads your screen, finds files, searches the web.
- **Remembers:** persistent local memory you can inspect and delete.
- **Yours to customize:** VRM avatar, voice, wake phrase, hotkeys, system prompt, tool permissions.

Status: **v0.1.0 released** ([download the installer](https://github.com/Navyarms/familiar/releases/latest)) — all six phases built; Windows installer, auto-update and docs are in place. The installer is unsigned for now (SmartScreen: *More info → Run anyway*). Say “hey Wisp” (or press Ctrl+Shift+M, or type), ask questions or tell it to do things: open apps, click and type in them by element name, read windows, volume, files, clipboard, timers, weather, web, Bluetooth. It remembers what you tell it across restarts (see and delete everything in Settings). Risky actions ask first; it pauses while you use the mouse; Ctrl+Alt+Esc stops everything. Docs: [user guide](docs/user-guide.md), [persona packs](docs/persona-pack-spec.md), [tool safety](docs/tool-safety.md), [PLAN.md](PLAN.md).

## Run from source (Windows)

```bash
npm install
uv sync --project brain
npm run dev
```

Needs Node 24 and [uv](https://docs.astral.sh/uv/). Models (~3.3 GB on the CPU tier) download on first run into `%LOCALAPPDATA%\Familiar`.

## License

MIT, see [LICENSE](LICENSE).
