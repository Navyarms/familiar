"""Speech to text with faster-whisper (CTranslate2, int8 on CPU). One model, one worker thread."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Whisper's favourite outputs for silence / breaths. Dropped when the clip is short.
HALLUCINATIONS = {"you", "you.", "thank you.", "thank you", "thanks for watching.", "thanks for watching", ".", "bye.", "okay."}


def default_stt_threads() -> int:
    import psutil

    phys = psutil.cpu_count(logical=False) or 4
    return max(2, min(8, phys // 2))


class WhisperSTT:
    def __init__(self, model_dir: Path, *, threads: int = 4, device: str = "cpu", compute_type: str = "int8", language: str = "en"):
        self.model_dir, self.threads, self.device, self.compute_type, self.language = model_dir, threads, device, compute_type, language
        self._m = None
        self._lock = asyncio.Lock()
        self.busy = False

    async def load(self) -> None:
        def _load():
            from faster_whisper import WhisperModel

            return WhisperModel(str(self.model_dir), device=self.device, compute_type=self.compute_type, cpu_threads=self.threads)

        t0 = time.monotonic()
        self._m = await asyncio.to_thread(_load)
        # first call is slow (kernel init); do it on silence now
        await asyncio.to_thread(self._transcribe_sync, np.zeros(16000, np.float32), False)
        log.info("whisper loaded from %s in %.2fs", self.model_dir.name, time.monotonic() - t0)

    async def transcribe(self, audio: np.ndarray, *, partial: bool = False) -> str:
        """Returns text. `partial=True` uses the cheapest settings and may be skipped if a transcription is running."""
        if self._m is None:
            raise RuntimeError("STT not loaded")
        if partial and self._lock.locked():
            return ""
        async with self._lock:
            self.busy = True
            try:
                return await asyncio.to_thread(self._transcribe_sync, audio, partial)
            finally:
                self.busy = False

    def _transcribe_sync(self, audio: np.ndarray, partial: bool) -> str:
        if len(audio) < 1600:  # < 0.1 s
            return ""
        t0 = time.monotonic()
        segs, _info = self._m.transcribe(
            audio.astype(np.float32), language=self.language, beam_size=1, best_of=1, temperature=0.0,
            without_timestamps=True, condition_on_previous_text=False, vad_filter=False,
            no_speech_threshold=0.6, log_prob_threshold=-1.0,
        )
        segs = list(segs)
        text = " ".join(s.text.strip() for s in segs).strip()
        if segs and all(s.no_speech_prob > 0.6 for s in segs):
            log.debug("stt: no-speech (p=%.2f) -> dropped %r", segs[0].no_speech_prob, text)
            text = ""
        elif len(audio) < 16000 * 2.0 and text.lower() in HALLUCINATIONS:
            log.debug("stt: hallucination guard dropped %r", text)
            text = ""
        log.debug("stt %s: %.2fs audio -> %r in %.2fs", "partial" if partial else "final", len(audio) / 16000, text, time.monotonic() - t0)
        return text
