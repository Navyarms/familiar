# Familiar user guide

Familiar is a small companion that lives on your Windows desktop. Say its name or press a hotkey, ask a
question or tell it to do something, and it answers out loud through a 3D avatar. Everything runs on
your own PC: the language model, speech recognition, the voice, and the memory. Nothing is sent
anywhere except the web searches you ask for and the one-time model downloads.

## Install

1. Download `Familiar-Setup-x.y.z.exe` from the Releases page and run it. Windows SmartScreen may warn
   that the publisher is unknown (the installer is not code-signed yet): choose *More info → Run anyway*.
2. Start Familiar. The first run checks your hardware, picks a model tier, and asks to download the
   models (about 3.3 GB on the CPU tier). They go to `%LOCALAPPDATA%\Familiar`.
3. When the orb (or your avatar) appears in the corner, it is ready.

Requirements: Windows 10/11 64-bit, 8 GB RAM (16 GB recommended), ~5 GB disk, a microphone if you
want to talk. A GPU is optional and unlocks bigger models automatically.

## Talking to Wisp

| How | What |
|---|---|
| Say **“hey Wisp”** | Opens the mic (you hear a soft tone). Speak; it answers when you pause. |
| **Ctrl+Shift+M** | Push to talk without the wake word; press again to stop early. |
| **Ctrl+Shift+Space** | Opens the chat panel to type instead. Enter sends, Esc hides. |
| Say **“hey Wisp”** while it is talking | Interrupts it and listens. |
| **Ctrl+Alt+Esc** | Kill switch: stops anything it is doing right now and releases keys. |
| Double-click the avatar | Push to talk. Right-click: settings. Drag: move it. |

Wisp can: answer questions, tell the time and weather, search the web, open and close apps, focus,
snap and close windows, set the volume or mute, control music, find and open your files, read and
write text files (asks first), copy to and read the clipboard, set timers and reminders, remember
things about you, check or toggle Bluetooth, type and press keys for you, click buttons and menus by
name in the active app, read what a window says, and look at the screen when nothing else works.

Things that change something or could lose work (closing apps, writing files, sleep, shutdown,
forgetting memories) show a card in the panel and are asked out loud: say **yes** or **no**, or click
Allow / Deny. You can change which actions ask in *Settings → Tools*.

While you are moving the mouse or typing, Wisp waits before clicking or typing itself, and gives up
with “you're using the mouse” if you keep going.

## Settings (tray icon → Settings…)

- **Persona**: switch personas, duplicate the built-in Wisp to edit its name, wake phrase, voice and
  system prompt, import packs (.zip), choose a VRM avatar.
- **Voice**: 50+ Kokoro voices with preview and speed.
- **Hotkeys & mic**: change the three hotkeys, start with Windows, pick the microphone, wake-word
  sensitivity, “interrupt on any speech” for headset users.
- **Models**: the detected tier, override it, point at your own GGUF, context size, re-download.
- **Privacy & memory**: turn web access or memory off, see and delete everything Wisp remembers.
- **Tools**: per-tool auto / confirm / blocked.

## Memory

Wisp keeps short facts you tell it (“my dog is Biscuit”) and a two-sentence summary of each
conversation, in `%LOCALAPPDATA%\Familiar\memory.sqlite`. It recalls them when relevant. Say “forget
what you know about …”, or delete items in Settings. Nothing is uploaded.

## Avatars

Any VRM 0.x or 1.0 model you have the rights to use works: VRoid Studio exports, models from VRoid
Hub or Booth with a licence that allows personal use. Put it in the persona folder as `avatar.vrm` or
pick it under *Settings → Persona → Avatar*. Lip sync and expressions (`[happy]`, `[surprised]`…) map
automatically; adjust the mapping in the pack's `expressions.json`.

## Troubleshooting

- **No voice input**: Settings → Hotkeys & mic → pick the right device. Virtual devices (Voicemod,
  Elgato) often sit as the Windows default.
- **It answered without doing anything**: the model skipped the tool. Say it again more directly
  (“open Notepad”, “click Save”); the log shows `nudging:` when it had to be pushed.
- **Slow first reply after startup**: the model is warming its prompt (10–20 s on CPU), then replies
  start in well under a second.
- **Logs**: Settings → About → Logs (`brain.log`, `llama-server.log`).
- **Reset**: quit Familiar and delete `%LOCALAPPDATA%\Familiar` (models included) or just
  `settings.json` / `memory.sqlite`.
