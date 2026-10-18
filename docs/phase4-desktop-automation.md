# Phase 4 — Full desktop automation (input, screen, multi-step agent loop)

Status 2026-10-18: working from source. "Turn on Bluetooth" and "open Notepad and type …" complete by
voice through the virtual-cable loopback; the email reply criterion is covered by the same tool chain
(focus → read_window_text → click_element "Reply" → type_text → click "Send") but was **not** run against
a real mail client, because none is set up on the dev machine.

## What exists

```
brain/familiar_brain/tools/
  input.py    type_text, press_keys ('ctrl+s', 'win+d', 'alt+tab'…), click (x,y), scroll — all through SendInput
              (Unicode typing, absolute virtual-desktop mouse coords); drag() kept as a function, not exposed
  guard.py    InterferenceGuard: before any injected input, wait until the user has been idle ≥1 s (cursor still,
              no keys/buttons down, our own cursor moves excluded); gives up after 8 s with
              "paused: you're using the mouse…"; settings.interference_guard toggles it
  screen.py   UI Automation (uiautomation): get_foreground_window, find_elements (label/type/toggle state/center),
              click_element (Invoke/Toggle/Select/Expand patterns, real click as last resort), set_element_text
              (ValuePattern, else click + select-all + type), read_window_text (labels + document text);
              look_at_screen: screenshot of the active window (or screen) downscaled to 1024 px → Qwen3-VL via
              llama-server (10–25 s on CPU); set_bluetooth: opens ms-settings:bluetooth, reads the toggle,
              flips only when needed, verifies
brain/familiar_brain/agent/turn.py   max 10 model calls per turn; `narrate=True` tools speak their description
                                     when the model gave no words; one-time "nudge" when a step ends with a short
                                     announcement ("Clicking File.") and no tool call
```

Kill switch now also releases any key or mouse button we are holding (`input.release_all`).

Tool count went **52 → 38** to keep the cached prefix small enough for an 8k context: minimize /
maximize / close / snap merged into `window_action`, lock / sleep / shutdown / cancel into `power`,
list / cancel timers into `timers`, volume read folded into `system_info`; brightness, reveal_file,
minimize_all, mouse_position and the drag tool were dropped. Descriptions were shortened. Prefix:
system prompt + 3 examples + 38 schemas ≈ **4,830 tokens** (was 5,636 with 52 tools).

## How a multi-step request runs

"Open notepad and type: I'll be late, see you at 6."

```
model:  "Opening Notepad."            -> open_app(name=Notepad)         0.3 s (waits for the window)
model:  "Typing your message."        -> type_text(text=...)            guard ok, 1.8 s at 60 cps
model:  "Message typed."
```
Total 8 s including three model steps. Each step's words are spoken while the tool runs; the panel
shows a card per tool. With the interference guard, moving the mouse during a step makes the brain
wait; after 8 s it reports "paused: you're using the mouse" and the model tells the user.

## Measured (dev machine, CPU, T1)

| Step | Time |
|---|---|
| find_elements / click_element / read_window_text on Notepad or Settings | 0.05–0.4 s |
| type_text 27 chars (60 cps) | 1.8 s |
| look_at_screen (active window, 1024 px wide) | 10–20 s |
| set_bluetooth (open Settings page, read toggle) | ~2 s when Settings is already open, 4–6 s cold |
| "open notepad and type …" by voice, end of speech → done | ~9.5 s |
| Prefix warm-up at boot | 15–16 s (4,827 tokens) |

## Model behaviour (Qwen3-VL-4B) and mitigations

- Worked reliably: open + type, read_window_text, set_bluetooth, get_foreground_window, narration lines
  paired with calls, honest reporting when a tool says "already on" / "no window".
- Still flaky: the model sometimes **announces and stops** ("Clicking File, then Escape.") or answers a
  "look at the screen" request from imagination. The nudge re-prompts once; narration phrasing in the
  instructions makes this more likely, so it is kept to "two to four words, in the same response as the
  call". The 8B model on GPU tiers should behave better; measure in Phase 6 tier tests.
- `look_at_screen` is deliberately last resort: 10–25 s on CPU and approximate. Element tools cover the
  Settings app, Explorer, Notepad, and anything with an accessibility tree.

## Safety

- Input only after the interference guard; kill switch releases held keys; drag not exposed.
- `window_action close` and `close_app` confirm; `power` sleep/shutdown/restart confirm; lock and cancel auto.
- set_bluetooth never toggles blindly: it reads the state first and flips only if different.
- Screenshots go only to the local llama-server; nothing leaves the machine.

## Phase 4 checklist (PLAN.md)

- [x] input tools (type, hotkeys, click, scroll) with interference guard (drag implemented, not exposed)
- [x] screen tool: screenshot → vision model; UI Automation element lookup (find/click/set/read)
- [x] Multi-step agent loop with progress narration
- [ ] Exit: "turn on Bluetooth" ✔ by voice; "reply to the open email saying I'll be late" — chain exists,
      untested against a real mail client

## Follow-ups

- Email flow test with Outlook/Mail/Gmail in a browser; Chrome accessibility tree check.
- Per-app hints (e.g. Settings page names) to cut steps for the 4B model.
- Interference guard: also pause the whole multi-step turn (not just the next action) when the user takes over.
- Optional screenshot thumbnail in the panel for look_at_screen results.
