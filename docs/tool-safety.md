# Tool safety

Familiar can operate your PC. This page says what it can do, what it asks before doing, and how to stop it.

## Permission levels

Every tool has one of three levels, editable in *Settings → Tools*:

- **auto** — runs immediately.
- **confirm** — a card appears in the panel and Wisp asks aloud ("Should I close Notepad?"). Say yes/no
  or click Allow/Deny. Nothing happens until you answer; unanswered requests expire after 90 s as a no.
- **blocked** — the tool is hidden from the model entirely.

Defaults:

| Group | Tools | Default |
|---|---|---|
| system | open_app, set_volume, get_volume, set_mute, media_control, get_datetime, system_info, set_bluetooth | auto |
| system | close_app, power (sleep / shutdown / restart) | **confirm**; power lock and cancel are auto |
| windows | list_windows, focus_window, window_action (minimize / maximize / restore / snap) | auto |
| windows | window_action close | **confirm** |
| files | search_files, open_file, read_file (inside your user folders only) | auto |
| files | write_file, move_file | **confirm** |
| web | web_search, fetch_page, get_weather | auto; the whole group can be turned off in *Privacy* |
| clipboard | clipboard_read, clipboard_write | auto |
| timers | set_timer, set_reminder, timers | auto |
| memory | remember, recall | auto; forget is **confirm** |
| input | type_text, press_keys, click, scroll | auto, guarded (see below) |
| screen | get_foreground_window, find_elements, click_element, set_element_text, read_window_text, look_at_screen | auto |
| shell | run_command | **blocked** |

File tools are confined to Downloads, Documents, Desktop, Pictures, Videos, Music and any folders you
add to `file_roots` in `settings.json`; paths outside are refused.

## Guards that are always on

- **Kill switch, Ctrl+Alt+Esc**: cancels the current turn, denies pending confirmations, stops speech,
  and releases any key or mouse button Familiar is holding.
- **Interference guard**: before typing or clicking, Familiar waits until your mouse and keyboard have
  been idle for a second; if you keep using them for 8 s it gives up and says so. Turn it off in
  *Privacy* if you prefer.
- **Timeouts**: every tool call has a time limit (20–120 s); a hung tool cannot wedge the assistant.
- **Verification**: open_app waits for a window; set_bluetooth reads the switch before flipping and
  verifies after; results that found nothing are reported as failures, not success.
- **No invention rule**: the model is instructed never to claim something is open, done, or copied
  unless a tool result says so, and an action it announces without calling is retried with a forced
  tool call once.

## What leaves the machine

Only `web_search` (DuckDuckGo), `fetch_page` (the URL you asked about) and `get_weather`
(Open-Meteo) make network requests, plus the model downloads on first run. Screenshots for
`look_at_screen` go to the local model only. Turn off *Web access* in *Privacy* to make Familiar fully
offline.

## Limits to know about

- A wrong tool choice has real effects (it once minimized every window when asked to minimize one app).
  Keep risky actions on confirm; the kill switch is always armed.
- `close_app` falls back to terminating a process without a window.
- Vision (`look_at_screen`) is approximate and slow on CPU; Familiar prefers accessibility tools.
- Timers and reminders live in memory and are lost when Familiar restarts.
