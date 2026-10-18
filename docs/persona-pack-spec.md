# Persona pack specification (v1)

A persona is a folder (or a .zip of that folder). Put it in `%LOCALAPPDATA%\Familiar\personas\<id>\`
or import it from *Settings → Persona*. Bundled packs live next to the app and are read-only;
duplicate one to edit it.

```
<id>/
  persona.json       required
  prompt.md          required  system prompt: personality, speaking style, rules
  expressions.json   optional  [tag] -> VRM expression mapping
  avatar.vrm         optional  VRM 0.x or 1.0; without it the shell renders the orb
  thumbnail.png      optional
  idle/*.vrma        reserved  idle animations (not used yet)
  voice/reference.wav reserved for the voice-cloning add-on (not used yet)
```

## persona.json

```json
{
  "id": "wisp",
  "name": "Wisp",
  "version": "0.1.0",
  "wake_phrase": "hey wisp",
  "avatar": "avatar.vrm",
  "prompt": "prompt.md",
  "expressions": "expressions.json",
  "voice": { "engine": "kokoro", "voice": "af_heart", "speed": 1.0, "pitch_shift": 0 },
  "license": "MIT (original character)"
}
```

- `id`: lowercase letters, digits, `-`/`_`; must match the folder name.
- `wake_phrase`: English words the keyword spotter can build from its vocabulary; two or three
  syllables work best ("hey wisp", "hey nova"). Changing it re-trains nothing: the phrase is turned into
  sub-word pieces at startup.
- `voice.voice`: a Kokoro voice id (`af_heart`, `am_adam`, `bf_emma`, …; Settings → Voice lists them).
  `speed` 0.7–1.3. `pitch_shift` is reserved.
- `license`: free text shown in the UI. Never include a model you may not redistribute.

## prompt.md

Plain text. It becomes the system prompt. Keep it about personality and speaking style; the app
appends its own rules for expression tags and tool use, and attaches the current time and remembered
facts to each turn. Keep it under ~500 words so the cached prefix stays small on CPU machines. Do not
tell the persona it "cannot control apps": it can, through tools.

## expressions.json

```json
{
  "happy":     { "vrm": "happy",     "weight": 1.0, "decay_s": 3.0 },
  "surprised": { "vrm": "surprised", "weight": 1.0, "decay_s": 2.0 },
  "wink":      { "vrm": "blinkLeft", "weight": 1.0, "decay_s": 0.5 }
}
```

Keys are the tags the model may emit (`[happy]`); `vrm` is the expression name in your model (VRM 1.0
presets: happy, angry, sad, relaxed, surprised, neutral, blink, blinkLeft, blinkRight, aa, ih, ou, ee,
oh; custom names work too). Tags not listed are ignored. Lip sync uses `aa/ih/ou/ee/oh` automatically.

## Sharing

Zip the folder (the zip may contain the folder itself or its contents). The importer reads
`persona.json`, derives the id, and copies everything into the user's personas folder. Mention the
avatar's licence in `license` and in a README inside the pack.
