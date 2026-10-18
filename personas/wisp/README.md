# Wisp (default persona)

An original character: a small bright spirit. This pack ships with no `avatar.vrm` yet, so the shell
renders Wisp as a glowing orb. Drop any VRM you have the rights to use into this folder as
`avatar.vrm` (or set `avatar_override` in `%LOCALAPPDATA%\Familiar\settings.json`) and the overlay
will use it, with lip sync and the expression map in `expressions.json`.

Files:

- `persona.json` — name, wake phrase, voice (Kokoro voice id, speed, pitch)
- `prompt.md` — the system prompt (personality and speaking style)
- `expressions.json` — `[tag]` → VRM expression name, weight, decay. Tags not listed here are ignored.
- `avatar.vrm` — optional model (VRM 0.x or 1.0)

License for this pack's text and config: MIT. Do not add third-party models to the public repo.
