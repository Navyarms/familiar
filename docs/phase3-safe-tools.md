# Phase 3 — Safe tools (registry, permissions, confirm cards, kill switch, first tool groups)

Status 2026-10-18: working from source. Exit criteria ("open X and turn the volume to 30", "find the
PDF I downloaded yesterday") pass by text and by voice through the virtual-cable loopback.

## What exists

```
brain/familiar_brain/tools/
  registry.py   @tool decorator -> ToolSpec (JSON schema, permission, risk, human description, timeout);
                ToolRegistry resolves permissions (settings overrides per tool or per group), hides blocked
                tools from the model, coerces argument types, runs tools in a thread with a timeout
  system.py     open_app (Start Menu shortcuts + aliases, verifies a window appears), close_app*, set/get_volume,
                set_mute, media_control, set_brightness, lock_pc, sleep_pc*, shutdown_pc*, cancel_shutdown,
                get_datetime, system_info
  windows.py    list/focus/minimize/maximize/close*/snap windows, minimize_all (user32 via ctypes)
  files.py      search_files (user folders + settings.file_roots, inclusive `days`), open_file, reveal_file,
                read_file, write_file*, move_file*  — all confined to the allowed roots
  web.py        web_search (DuckDuckGo HTML, no key), fetch_page (HTML -> text)
  weather.py    get_weather (Open-Meteo geocoding + forecast, no key)
  clipboard.py  clipboard_read / clipboard_write (user32)
  timers.py     set_timer, set_reminder ("in 20 minutes", "at 3:30 pm", "tomorrow at 9"), list_timers, cancel_timer;
                fire through Brain.announce (spoken, queued until the current turn ends)
  memory.py     remember / recall / forget* on SQLite facts; recent facts ride on each user turn
  shell.py      run_command — blocked by default
brain/familiar_brain/agent/turn.py   agent loop: stream -> speak content -> run tool calls (confirm when needed)
                                     -> feed results back -> final answer; max 6 model calls per turn
```
`*` = confirm by default. 39 tools. Permission levels: **auto**, **confirm** (card in the panel, spoken
"Should I …?", answered by button or by saying yes/no), **blocked** (hidden from the model). Overrides live
in `settings.json: tool_permissions` (`{"write_file": "auto", "group:web": "blocked"}`) and are editable
from the Settings window's permissions table (sent as `settings.update`, applied live, prefix re-warmed).

Protocol: `tool.request_confirm`, `tool.start`, `tool.result`, `tools.info` (brain → shell);
`tool.confirm`, `tool.deny`, `kill_switch` (shell → brain). States `executing` and `needs_confirm`.

**Kill switch** (Ctrl+Alt+Esc, tray "Stop speaking" is the softer interrupt): denies every pending
confirmation, cancels the running turn and its TTS, re-arms the registry's kill event, reports a
`tool.result` of `kill_switch`. Tools run in threads with per-tool timeouts (20–60 s), so a hung tool
cannot wedge the loop.

Panel: tool cards (amber = waiting for your OK with Allow/Deny, blue = running, green ✓ / red ✗ / grey –
denied, with the tool's one-line summary); the state dot pulses amber while working and red while
waiting for confirmation. Overlay: badge shows the action, avatar looks surprised when it needs an OK.

## How the 4B model was made to actually use tools

Qwen3-VL-4B at temperature 0.7 happily answered "what's on my clipboard" and "remember that…" from
thin air. What fixed it (each step measured with `scripts/ws_client.py` prompt sets):

1. The persona prompt said Wisp "cannot control apps" (left over from Phase 1). Removed.
2. Explicit rules in the tool instructions: *you know nothing about this PC unless a tool returns it;
   actions only happen through tools; never say done/remembered/copied without a result.*
3. Three worked examples (remember, close_app, set_mute) as real tool-call messages in the prefix.
   They sit in the cached prefix, so they are free per turn.
4. Temperature 0.4 when tools are offered.
5. The current time (and remembered facts) are appended to the **user** turn as a note, not the system
   prompt, so the ~3.8k-token prefix stays cached. Putting memories in the system prompt cost a 15 s
   re-prefill after every `remember`.
6. Context raised to 8192 (prefix ≈ 3,850 tokens with 39 schemas; 4096 overflowed on the second tool step).
7. `days` for search_files is inclusive of whole days (1 = today or yesterday) because the model sends 1
   for "yesterday" about half the time.
8. `open_app` waits up to 3 s for a window and says "launched but no window appeared" otherwise (the
   Calculator on the dev machine is disabled by policy; the launch call still "succeeds").

Prompt-set results after these changes (text, `--no-speak`): time, clipboard, remember, open+volume,
PDF-from-yesterday, weather (real Open-Meteo data), timer + spoken announcement, write_file with a
confirm (both deny and allow paths), memory recall, close_app with confirm — all called the right tool.
Remaining miss: "minimize notepad" once chose `minimize_all` (description tightened since).

## Measured (dev machine, CPU, T1)

| Step | Time |
|---|---|
| Prefix warm-up at boot (system + examples + 39 schemas, 3,848 tokens) | ~15 s once; cached afterwards |
| Text → first tool call (simple request) | 2.5–3.5 s |
| Tool execution (open_app with window check / set_volume / clipboard / search_files) | 0.3–3 s / <0.1 s / <0.1 s / 0.05–0.2 s |
| Whole turn "open notepad and set the volume to 30" (2 parallel tool calls + spoken reply) | ~5 s |
| web_search / get_weather round trip | 0.6–0.8 s |

## Safety notes and known gaps

- Everything runs on the user's live desktop: a wrong tool choice has real effects (the `minimize_all`
  miss hid every window during testing). Defaults err toward auto for reversible things and confirm for
  closing apps/windows, writing/moving files, sleep/shutdown, forgetting memories; shell is blocked.
- No interference guard yet (Phase 4): tools do not check whether the user is typing.
- Timers and reminders are in memory only; they vanish when the brain restarts.
- `set_brightness` depends on WMI support; desktops report "not supported".
- `close_app` falls back to `psutil.terminate()` for processes without a window; that is a soft kill.
- Voice yes/no only resolves the oldest pending confirmation; the wake word is not required for it.
- Kill switch cannot stop a tool already inside a blocking call; it waits for the tool's timeout.

## Phase 3 checklist (PLAN.md)

- [x] Tool registry, permission levels, confirmation cards, kill switch
- [x] system, windows, files, web, clipboard, timers, memory tools (+ weather, blocked shell)
- [x] Exit: "open Spotify and turn the volume to 30" and "find the PDF I downloaded yesterday" work by voice
      (verified with Notepad in place of Spotify, which is not installed on the dev machine)
