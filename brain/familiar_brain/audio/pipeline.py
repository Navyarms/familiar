"""Voice pipeline: mic -> wake word / VAD -> streaming STT -> utterance callback. Also push-to-talk and barge-in.

Modes
  off         no audio source
  wake        listening for the wake phrase only (VAD only runs here when barge-in-by-voice is enabled and we are speaking)
  listening   capturing an utterance: VAD decides end of speech, partial transcripts stream, final on end
  processing  final transcription running

Echo note: when Wisp speaks through speakers the mic hears it. Barge-in by plain VAD is therefore opt-in
(`barge_in_vad`, for headset users); barge-in by wake phrase or push-to-talk is always on.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import numpy as np

from .capture import FRAME, SR, AudioSource
from .stt import WhisperSTT
from .vad import SileroVAD
from .wakeword import WakeWord

log = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]


@dataclass
class PipelineConfig:
    wake_phrases: list[str]
    wake_enabled: bool = True
    barge_in_vad: bool = False
    no_speech_timeout_s: float = 6.0
    max_utterance_s: float = 30.0
    partial_every_s: float = 1.5
    partial_min_speech_s: float = 2.0  # short utterances finish before a partial would be useful
    preroll_wake_s: float = 0.5
    preroll_ptt_s: float = 0.6
    level_hz: float = 8.0
    barge_in_speech_s: float = 0.45
    end_grace_s: float = 0.45  # after VAD says "end", keep listening this long; the final decode already runs speculatively


class VoicePipeline:
    def __init__(self, *, source: AudioSource, vad: SileroVAD, kws: WakeWord | None, stt: WhisperSTT,
                 send: Send, on_utterance: Callable[[str, str], Awaitable[None]], on_barge_in: Callable[[], Awaitable[None]],
                 config: PipelineConfig):
        self.source, self.vad, self.kws, self.stt = source, vad, kws, stt
        self.send, self.on_utterance, self.on_barge_in, self.cfg = send, on_utterance, on_barge_in, config
        self.mode = "off"
        self.speaking = False  # shell playback state
        self.busy = False  # brain is thinking/speaking a turn
        self._task: asyncio.Task | None = None
        self._ring: deque[np.ndarray] = deque(maxlen=int(1.0 * SR / FRAME))  # 1 s pre-roll ring
        self._buf: list[np.ndarray] = []
        self._listen_t0 = 0.0
        self._speech_t0 = 0.0
        self._had_speech = False
        self._last_partial_t = 0.0
        self._last_level_t = 0.0
        self._partial_task: asyncio.Task | None = None
        self._final_task: asyncio.Task | None = None  # speculative final decode started at VAD end-of-speech
        self._final_t = 0.0
        self._reason = ""
        self._speech_run_s = 0.0
        self._wake_re = re.compile(r"^\W*(?:" + "|".join(re.escape(p) for p in config.wake_phrases) + r"|" +
                                   "|".join(re.escape(p.split()[-1]) for p in config.wake_phrases) + r")\b[\s,.!?]*", re.I)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.source.start()
        self.mode = "wake" if (self.kws and self.cfg.wake_enabled) else "idle"
        self._task = asyncio.create_task(self._run(), name="voice-pipeline")
        await self._emit_mode()

    async def stop(self) -> None:
        self.source.stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self.mode = "off"
        await self._emit_mode()

    def set_speaking(self, speaking: bool) -> None:
        self.speaking = speaking
        self._speech_run_s = 0.0

    # ------------------------------------------------------------------ controls
    async def ptt_start(self) -> None:
        if self.mode == "listening":
            return
        if self.speaking or self.busy:
            await self.on_barge_in()
        await self._begin_listening("ptt")

    async def ptt_stop(self) -> None:
        if self.mode == "listening":
            await self._finalize()

    async def cancel_listening(self) -> None:
        if self.mode == "listening":
            self._buf.clear()
            await self._back_to_wake("cancelled")

    # ------------------------------------------------------------------ main loop
    async def _run(self) -> None:
        q = self.source.queue
        while True:
            frame = await q.get()
            if frame is None:  # file source ended
                if self.mode == "listening":
                    await self._finalize()
                log.info("audio source ended")
                return
            self._ring.append(frame)
            now = time.monotonic()
            try:
                if self.mode in ("wake", "idle") and self.kws and self.cfg.wake_enabled:
                    hit = self.kws.feed(frame)
                    if hit:
                        log.info("wake word: %s", hit)
                        if self.speaking or self.busy:
                            await self.on_barge_in()
                        await self._begin_listening("wake")
                        continue
                if self.mode in ("wake", "idle") and self.cfg.barge_in_vad and self.speaking:
                    self.vad.feed(frame)
                    self._speech_run_s = self._speech_run_s + FRAME / SR if self.vad.is_speech else 0.0
                    if self._speech_run_s >= self.cfg.barge_in_speech_s:
                        log.info("barge-in by voice")
                        await self.on_barge_in()
                        await self._begin_listening("voice")
                        continue
                if self.mode == "listening":
                    await self._listening_frame(frame, now)
            except Exception:
                log.exception("pipeline frame error")
            if self.mode == "listening" and now - self._last_level_t >= 1.0 / self.cfg.level_hz:
                self._last_level_t = now
                await self.send({"type": "mic.state", "mode": self.mode, "level": round(min(1.0, self.source.level * 6), 3), "reason": self._reason})

    async def _listening_frame(self, frame: np.ndarray, now: float) -> None:
        self._buf.append(frame)
        self.vad.feed(frame)
        speech = self.vad.is_speech
        if speech and not self._had_speech:
            self._had_speech = True
            self._speech_t0 = now
            self._last_partial_t = now + self.cfg.partial_min_speech_s - self.cfg.partial_every_s
        elapsed = now - self._listen_t0
        if not self._had_speech and elapsed > self.cfg.no_speech_timeout_s:
            await self._back_to_wake("no speech")
            return
        if elapsed > self.cfg.max_utterance_s:
            await self._finalize()
            return
        segments = self.vad.pop_segments()
        if self._final_task is not None:
            # VAD ended the utterance a moment ago and a final decode is running. If the speaker resumes within the
            # grace window, it was just a pause between sentences: drop the speculative result and keep going.
            if speech and now - self._final_t < self.cfg.end_grace_s:
                self._final_task.cancel()
                self._final_task = None
            elif now - self._final_t >= self.cfg.end_grace_s:
                task, self._final_task = self._final_task, None
                try:
                    text = await task
                except (asyncio.CancelledError, Exception):
                    text = None
                await self._finalize(text)
                return
        elif segments and self._had_speech:
            self._final_t = now
            self._final_task = asyncio.create_task(self._decode_final(np.concatenate(self._buf)))
            return
        if self._had_speech and speech and now - self._last_partial_t >= self.cfg.partial_every_s and not self.stt.busy:
            self._last_partial_t = now
            audio = np.concatenate(self._buf)
            self._partial_task = asyncio.create_task(self._partial(audio))

    async def _partial(self, audio: np.ndarray) -> None:
        try:
            text = await self.stt.transcribe(audio, partial=True)
            if text and self.mode == "listening":
                await self.send({"type": "transcript.partial", "text": self._strip_wake(text)})
        except Exception as e:
            log.debug("partial failed: %s", e)

    # ------------------------------------------------------------------ transitions
    async def _begin_listening(self, reason: str) -> None:
        pre = self.cfg.preroll_ptt_s if reason == "ptt" else self.cfg.preroll_wake_s
        n = int(pre * SR / FRAME)
        self._buf = list(self._ring)[-n:] if n else []
        self.vad.reset()
        self._final_task = None
        self._had_speech = False
        self._listen_t0 = time.monotonic()
        self._last_partial_t = self._listen_t0
        self._reason = reason
        self.mode = "listening"
        await self._emit_mode()

    async def _decode_final(self, audio: np.ndarray) -> str:
        if len(audio) < SR * 0.3:
            return ""
        return self._strip_wake(await self.stt.transcribe(audio))

    async def _finalize(self, text: str | None = None) -> None:
        """End the utterance. `text` is the speculative result when VAD ended it; None = decode now (PTT stop, timeouts)."""
        self.mode = "processing"
        await self._emit_mode()
        audio = np.concatenate(self._buf) if self._buf else np.zeros(0, np.float32)
        self._buf = []
        if self._partial_task and not self._partial_task.done():
            self._partial_task.cancel()
        if self._final_task is not None:
            self._final_task.cancel()
            self._final_task = None
        if text is None:
            text = ""
            try:
                text = await self._decode_final(audio)
            except Exception:
                log.exception("final transcription failed")
        if self.kws:
            self.kws.reset()
        if text:
            log.info("utterance (%s, %.1fs): %r", self._reason, len(audio) / SR, text)
            self.mode = "wake" if (self.kws and self.cfg.wake_enabled) else "idle"
            await self._emit_mode()
            await self.on_utterance(text, self._reason)
        else:
            await self._back_to_wake("nothing heard")

    async def _back_to_wake(self, why: str) -> None:
        self.mode = "wake" if (self.kws and self.cfg.wake_enabled) else "idle"
        if self.kws:
            self.kws.reset()
        self._reason = why
        await self._emit_mode()

    async def _emit_mode(self) -> None:
        await self.send({"type": "mic.state", "mode": self.mode, "level": 0.0, "reason": self._reason})

    def _strip_wake(self, text: str) -> str:
        t = self._wake_re.sub("", text, count=1).strip()
        return t if t else text.strip()
