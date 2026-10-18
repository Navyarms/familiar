# Phase 6 — Polish and ship v0.1

Status 2026-10-18: settings UI, persona manager, voice preview, packaging (PyInstaller brain + NSIS installer +
auto-update wiring), startup-with-Windows and the user docs are done from source. The public repo and the first
Release are not created yet (they need the owner's GitHub account). Code signing: unsigned for v0.1 (see below).

## Agent reliability pass (done first, per the owner's priority: actions over vision, no improper tools)

`brain/scripts/tool_eval.py` runs 43 utterances against the live model in dry-run mode and reports which tool
the first step picks (and what the production nudge would do). Baseline 38/43 (88%). After the fixes below:
**42/43 (98%)**, median 1.7 s per step, no nudges needed.

| Problem seen | Fix |
|---|---|
| "Is Bluetooth on?" called `set_bluetooth(enabled=true)` | `set_bluetooth(action: check \| on \| off)`; a question can no longer flip the switch |
| "What's the volume?" picked the wrong tool after volume was folded into system_info | explicit `get_volume` is back |
| "What does this error dialog say?" went to vision | `look_at_screen` answers text-like questions through accessibility first (`read_window_text`), vision only if no readable text |
| Hallucinated window lists; "Mail", "Excel", "Thunderbird" leaked from examples | examples keep **neutral** tool results; rule: never state what is open without a tool in this conversation |
| Removing message examples made the model narrate and stop | message examples kept (they teach "words + call in one response"); textual examples added for state questions |
| "scroll down" sent a positive amount | `scroll(direction, notches)` |
| Announce-only steps | one forced retry with `tool_choice: required` (from Phase 4), now simulated in the eval |

The remaining miss is the error-dialog question, which the accessibility fast path handles in code.

## Settings and persona manager

Tabs: Persona (list, use, edit, duplicate built-in → user folder, delete, import .zip/folder, open folder, VRM
picker), Voice (Kokoro voices grouped by accent, preview with custom text and speed, apply to the active user
pack), Hotkeys & mic (editable accelerators persisted in `shell.json`, start with Windows, device picker, wake
word, barge-in, sensitivity), Models (tier, override, custom GGUF, context, download), Privacy & memory
(web, memory, interference guard, narration, memory viewer), Tools (permissions). Brain messages:
`personas.list/import/duplicate/update/delete`, `voices.list`, `voice.preview`, `devices.list`,
`settings.get` → `settings.items` (also echoed after every `settings.update`).

## Packaging

- `brain/familiar-brain.spec` + `brain/brain_entry.py`: one-folder PyInstaller build, **332 MB / 93 DLLs**,
  ~55 s. Verified end to end (LLM, Kokoro, embeddings, wake word, whisper, tools) with
  `scripts/ws_client.py --exe … ` and `FAMILIAR_BUNDLE=<repo>`.
- `apps/shell/package.json` → `build`: electron-builder, NSIS (per-user, choosable directory, desktop shortcut),
  extra resources `brain/` (the PyInstaller folder) and `personas/` (no .vrm files), icon from `build/icon.ico`
  (drawn in code, same orb as the tray), `signAndEditExecutable: false`.
- `bundle_root()` in the brain resolves to `resources/` when frozen so `resources/personas` and
  `resources/runtimes` are found; `FAMILIAR_BUNDLE` overrides for dev.
- Auto-update: `electron-updater` against GitHub Releases (`publish: github`), checks 20 s after start and every
  6 h, downloads in the background, installs on quit, tray "Check for updates…". Inactive until the repo exists;
  `OWNER/familiar` in package.json must be replaced.
- Start with Windows: `app.setLoginItemSettings` with `--hidden` (no panel pop at login).
- `.github/workflows/release.yml`: on a `v*` tag, builds the brain and the installer on `windows-latest` and
  publishes the Release with `latest.yml`.

## Measurements (dev machine)

| Item | Result |
|---|---|
| `Familiar-Setup-0.1.0.exe` | **207 MB** (NSIS, per-user, with `latest.yml` + blockmap for the updater) |
| Unpacked install (`%LOCALAPPDATA%\Programs\Familiar`) | 728 MB (brain 332 MB, Electron ~250 MB, personas without VRM) |
| Silent install `Familiar-Setup-0.1.0.exe /S` | 15 s |
| First launch of the installed app → brain idle with voice | ~25 s (llama-server 2.4 s, prefix warm 19 s at 5,065 tokens, whisper 3.5 s) |
| Memory while running (T1) | Familiar.exe processes ~760 MB total, familiar-brain.exe 480 MB, llama-server 5.9 GB |
| Hotkey summon on the installed build | panel opened; overlay rendered the VRM; tray icon present |

The install test ran on the dev machine, which already had the models and settings in `%LOCALAPPDATA%\Familiar`,
so it covers the installer, resource layout (`resources/brain`, `resources/personas`), the frozen brain and the
packaged shell, but not the first-run download path on a clean PC. Note for scripts: Git Bash rewrites `/S` into a
path; launch the installer from PowerShell (`Start-Process … -ArgumentList '/S' -Wait`).

## Code signing decision

v0.1 ships **unsigned**. SmartScreen will show "Windows protected your PC" until reputation builds; the user
guide explains "More info → Run anyway". When the project has a home, the cheap route is **Azure Trusted
Signing** (~$10/month, needs an Azure account with a verified identity) or a standard OV certificate
(~$200–400/year). electron-builder supports both through `win.signtoolOptions`/`azureSignOptions`; the release
workflow would get the secrets. Self-signing is pointless for SmartScreen.

## Not done / needs the owner

- Create the public GitHub repo (MIT) and push; set `publish.owner/repo` and `homepage` in
  `apps/shell/package.json`; tag `v0.1.0` to produce the first Release through the workflow.
- An original Wisp VRM (the orb ships as the fallback; the dev machine uses a third-party sample via override).
- Clean-VM install test (the exit criterion); the local install test covers the installer mechanics only.
- The 10-minute human quiet-room test (Phase 2) and the email flow (Phase 4) remain open items.
