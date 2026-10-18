"""Silero VAD through sherpa-onnx. Streaming: feed 512-sample frames, read `is_speech`, collect finished segments."""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class SileroVAD:
    def __init__(self, model: Path, *, threshold: float = 0.5, min_silence_s: float = 0.55, min_speech_s: float = 0.2,
                 max_speech_s: float = 30.0, threads: int = 1):
        import sherpa_onnx

        if not model.exists():
            raise FileNotFoundError(f"VAD model missing: {model}")
        # Note: assigning fields on a default-constructed VadModelConfig does not propagate to the C++ side
        # (the detector then dereferences an empty model path and crashes), so build it via the constructors.
        silero = sherpa_onnx.SileroVadModelConfig(model=str(model), threshold=threshold, min_silence_duration=min_silence_s,
                                                  min_speech_duration=min_speech_s, window_size=512,
                                                  max_speech_duration=max_speech_s)
        cfg = sherpa_onnx.VadModelConfig(silero_vad=silero, sample_rate=16000, num_threads=threads, provider="cpu")
        self._cfg = cfg
        self._vad = sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=max_speech_s + 5)

    def feed(self, frame: np.ndarray) -> None:
        self._vad.accept_waveform(frame)

    @property
    def is_speech(self) -> bool:
        return bool(self._vad.is_speech_detected())

    def pop_segments(self) -> list[np.ndarray]:
        """Speech segments that ended since the last call (end detected after min_silence_s of silence)."""
        out = []
        while not self._vad.empty():
            seg = self._vad.front
            out.append(np.asarray(seg.samples, dtype=np.float32))
            self._vad.pop()
        return out

    def flush(self) -> list[np.ndarray]:
        self._vad.flush()
        return self.pop_segments()

    def reset(self) -> None:
        self._vad.reset()
        while not self._vad.empty():
            self._vad.pop()
