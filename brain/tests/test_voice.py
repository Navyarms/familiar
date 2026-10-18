"""Voice pipeline tests. Unit tests always run; the integration test needs the T1 models in the local store."""

from __future__ import annotations

import asyncio
import os
import re
import wave
from pathlib import Path

import numpy as np
import pytest

from familiar_brain.config import Paths, Settings
from familiar_brain.models import ModelManager


def _store() -> ModelManager | None:
    paths = Paths.default()
    s = Settings.load(paths.settings_file)
    mm = ModelManager(paths, s)
    missing = {i.kind for i in mm.missing()}
    return None if missing & {"vad", "kws", "stt", "tts", "tts_voices"} else mm


def test_wake_regex_strip():
    from familiar_brain.audio.pipeline import PipelineConfig, VoicePipeline

    cfg = PipelineConfig(wake_phrases=["hey wisp"])
    p = VoicePipeline.__new__(VoicePipeline)
    p.cfg = cfg
    phrases = [r"\W*".join(re.escape(w) for w in q.split()) for q in cfg.wake_phrases]
    p._wake_re = re.compile(r"^\W*(?:" + "|".join(phrases + ["wisp"]) + r")\b[\s,.!?]*", re.I)
    assert p._strip_wake("Hey Wisp, what time is it?") == "what time is it?"
    assert p._strip_wake("Hey, Wisp, find the PDF.") == "find the PDF."
    assert p._strip_wake("wisp open spotify") == "open spotify"
    assert p._strip_wake("what time is it") == "what time is it"
    assert p._strip_wake("Hey Wisp") == "Hey Wisp"  # never strip to empty


@pytest.mark.skipif(_store() is None, reason="T1 voice models not present in %LOCALAPPDATA%\\Familiar")
def test_keyword_line_from_phrase():
    from familiar_brain.audio.wakeword import phrase_to_keyword_line

    mm = _store()
    line = phrase_to_keyword_line(mm.kws_dir() / "bpe.model", "hey wisp")
    assert line.startswith("▁HE") and "@HeyWisp" in line and " #0.25" in line


@pytest.mark.skipif(_store() is None, reason="T1 voice models not present in %LOCALAPPDATA%\\Familiar")
async def test_pipeline_wake_then_transcribe(tmp_path: Path):
    """Synthesize 'Hey Wisp, what time is it?' with Kokoro, feed it as the mic, expect a wake + the utterance."""
    from kokoro_onnx import Kokoro

    from familiar_brain.audio import FileSource
    from familiar_brain.audio.pipeline import PipelineConfig, VoicePipeline
    from familiar_brain.audio.stt import WhisperSTT
    from familiar_brain.audio.vad import SileroVAD
    from familiar_brain.audio.wakeword import WakeWord

    mm = _store()
    model, voices = mm.kokoro_paths()
    k = Kokoro(str(model), str(voices))
    audio, sr = k.create("Hey Wisp, what time is it in Tokyo right now?", voice="am_adam", speed=1.0, lang="en-us")
    wav = tmp_path / "utt.wav"
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.concatenate([np.zeros(int(0.5 * sr), "<i2"), pcm]).tobytes())

    events: list[dict] = []
    utterances: list[tuple[str, str]] = []
    barge = []

    async def send(m):
        events.append(m)

    async def on_utt(text, reason):
        utterances.append((text, reason))

    async def on_barge():
        barge.append(1)

    loop = asyncio.get_running_loop()
    source = FileSource(loop, wav, realtime=False, then_silence_s=1.5)
    stt = WhisperSTT(mm.stt_dir(), threads=max(2, (os.cpu_count() or 4) // 2))
    await stt.load()
    pipe = VoicePipeline(source=source, vad=SileroVAD(mm.vad_path()), kws=WakeWord(mm.kws_dir(), ["hey wisp"]), stt=stt,
                         send=send, on_utterance=on_utt, on_barge_in=on_barge, config=PipelineConfig(wake_phrases=["hey wisp"]))
    await pipe.start()
    for _ in range(600):  # up to 60 s
        if utterances or (pipe._task and pipe._task.done()):
            break
        await asyncio.sleep(0.1)
    await pipe.stop()
    modes = [e["mode"] for e in events if e["type"] == "mic.state"]
    assert "listening" in modes, f"wake word never fired; modes={modes}"
    assert utterances, f"no utterance; events={events[-5:]}"
    text, reason = utterances[0]
    assert reason == "wake"
    assert "tokyo" in text.lower() and "time" in text.lower(), text
    assert not text.lower().startswith("hey wisp"), text
    assert not barge
