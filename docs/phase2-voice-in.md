# Phase 2 — Voice in (mic, VAD, wake word, streaming STT, push-to-talk, barge-in)

Status 2026-10-18: working end to end from source. Verified without a human at the mic by playing
synthesized speech into a VB-Audio virtual cable that the brain captures as its microphone
(`brain/scripts/voice_loopback.py`), plus a file-fed pipeline test in `brain/tests/test_voice.py`.
The 10-minute quiet-room hands-free session from the PLAN exit criterion still needs a person.

## What exists

```
brain/familiar_brain/audio/
  capture.py   MicSource (sounddevice, 16 kHz mono, 512-sample frames; WASAPI auto-convert, else resample)
               FileSource (wav as a fake mic, real-time or fast) · list_input_devices()
  vad.py       Silero VAD via sherpa-onnx (speech flag + finished segments after 0.55 s silence)
  wakeword.py  sherpa-onnx transducer keyword spotter; persona wake phrase -> BPE pieces at startup
  stt.py       faster-whisper (CTranslate2 int8), greedy, no-speech / hallucination guard
  pipeline.py  VoicePipeline state machine: off | idle | wake | listening | processing
```

Models (added to the manifest, downloaded on first run, SHA256-pinned): `silero_vad.onnx` (0.6 MB),
`sherpa-onnx-kws-zipformer-gigaspeech-3.3M` (17 MB, unpacked), faster-whisper **small** int8 (T1+)
or **base** (T0), ~490 / 150 MB.

Protocol additions: brain → shell `mic.state {mode, level, reason}`, `transcript.partial`;
shell → brain `ptt.start`, `ptt.stop`, `playback {speaking}`.

Shell: talk hotkey **Ctrl+Shift+M** (toggle; Electron cannot see key-up for a true hold), tray item,
mic button in the panel with a live level ring, partial transcript shown in italics while you speak,
mic indicator on the overlay (dim breathing ring while waiting for the wake word, green with level
while capturing, amber while transcribing) and three soft earcons (wake / start / end). The overlay
reports playback start/stop to the brain so it knows when Wisp is speaking.

## How a hands-free turn flows

1. `wake` mode: every 32 ms frame goes to the keyword spotter (RTF ≈ 0.015).
2. "hey wisp" → `listening`: 0.5 s pre-roll from a ring buffer (the first word after the wake phrase
   is often already under way), VAD reset, earcon, avatar turns green. Push-to-talk enters the same
   state with a 0.6 s pre-roll and no wake word.
3. While listening: VAD tracks speech; after 2 s of speech a partial transcription runs every 1.5 s
   (skipped if one is still running); no speech for 6 s → back to `wake`; 30 s cap.
4. VAD reports end of speech (0.55 s of silence) → a **speculative** final decode starts at once while
   the mic stays open for a 0.45 s grace window. Speech resuming inside the window (a pause between
   sentences) discards that decode and keeps the utterance going; otherwise its result is used →
   `processing` → leading wake phrase stripped, empty / hallucinated results dropped → `on_utterance`
   → the normal text turn (LLM → TTS → avatar). The grace costs no latency because the decode runs
   during it.
5. Barge-in: the wake phrase or the talk hotkey while Wisp is thinking or speaking cancels the turn
   (`reply.done interrupted=true`, shell stops audio) and opens the mic. Barge-in on *any* speech
   (`barge_in_vad: true`) is opt-in for headset users, because speakers feed Wisp's own voice back
   into the mic.

## Measured (dev machine, CPU only, T1, virtual-cable loopback)

| Step | Time |
|---|---|
| Voice stack load (VAD + KWS + whisper small warm-up) | ~1.7 s after the LLM is up |
| Wake phrase end → `listening` | ~0.1–0.2 s |
| End of speech → final transcript (2–2.5 s utterance) | VAD 0.55 s + STT 0.73–0.77 s ≈ **1.3 s** (target 0.7 s: not met yet) |
| Partial transcript (only for utterances > 2 s) | 1.4 s per pass on the audio so far |
| Wake phrase during playback → turn cancelled + mic open | ~0.3 s |
| Detection in the loopback runs | 8 / 8 wake phrases across runs, 0 false wakes; a 10 s two-sentence question captured whole (5 partials) |

Ways to close the end-of-speech gap, in order: run the final decode on the last VAD segment only
(the pre-roll and silence are decoded today), drop `min_silence` to 0.4 s once the barge-in path is
tuned, try whisper **base** for the final on T1 when the utterance is short, and give STT more
threads when the LLM is idle (it is now `min(8, physical/2)`).

## Gotchas learned

- `sherpa-onnx` on Windows needs **`sherpa-onnx-core`** as well; without it the extension binds to the
  ancient `onnxruntime.dll` in System32 and the VAD constructor crashes with an access violation.
- Build sherpa configs with the keyword constructors; setting fields on a default-constructed
  `VadModelConfig` does not reach the C++ side.
- WASAPI shared mode will not resample. Ask for 16 kHz with `WasapiSettings(auto_convert=True)`; the
  same device also shows up under MME/DirectSound/WDM-KS, so pick by name *and* host API.
- Whisper returns "you" / "Thank you." for silence. `no_speech_prob > 0.6` on every segment, or a
  short clip whose whole text is in a small blacklist, is treated as nothing heard.
- Keyword display names (`@HeyWisp`) must not contain spaces.
- A partial decode in flight delays the final (same lock); only start partials once the utterance
  is clearly long.

## Dev / test knobs

- `FAMILIAR_MIC_DEVICE="CABLE Output"` or `settings.json: mic_device` (index or name substring).
- `FAMILIAR_MIC_FILE=path.wav` or `familiar-brain --mic-file` feeds a wav instead of a device.
- `settings.json`: `voice_enabled`, `wake_word`, `barge_in_vad`, `vad_threshold`, `kws_threshold`, `stt_threads`.
- `uv run --project brain python brain/scripts/voice_loopback.py` runs the acoustic test (needs the
  VB-Audio cable or any output→input loop; `--out/--in` select devices by name).

## Phase 2 checklist (PLAN.md)

- [x] Mic capture, Silero VAD, wake word, push-to-talk
- [x] Streaming STT with end-of-speech detection (partials for long utterances, final on VAD end)
- [x] Barge-in and interrupt (wake phrase / hotkey always; voice-activity barge-in opt-in)
- [x] Mic-active indicator on the avatar
- [ ] Exit: 10 minutes hands-free in a quiet room without a false or missed wake — needs a person;
      synthetic evidence so far: Phase 0 (0 false accepts / 1 h synthetic noise, 86 % detection across
      voices) and the loopback runs above.
