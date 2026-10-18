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

Status: **Phases 0–2 done from source** — say “hey Wisp” (or press Ctrl+Shift+M, or type) and Wisp answers aloud through the avatar with lip sync; you can interrupt by saying the wake phrase again. See [PLAN.md](PLAN.md) and the phase notes in [docs/](docs/).

## Run from source (Windows)

```bash
npm install
uv sync --project brain
npm run dev
```

Needs Node 24 and [uv](https://docs.astral.sh/uv/). Models (~3.3 GB on the CPU tier) download on first run into `%LOCALAPPDATA%\Familiar`.

## License

MIT, see [LICENSE](LICENSE).
