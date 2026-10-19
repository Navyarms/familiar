"""Kokoro TTS (kokoro-onnx, CPU). Produces PCM16 + a 50 Hz amplitude envelope + coarse viseme hints.

Phase 0 showed the published ONNX exposes no phoneme durations, so visemes are approximated by spreading
the phoneme string over the audio proportionally. The envelope drives mouth opening; the hints pick the shape.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

ENVELOPE_HZ = 50
# IPA vowel -> VRM viseme
_VOWELS = {
    "aa": "ɑaæʌɐɒ", "ih": "ɪiᵻɨ", "ou": "uʊwɯ", "ee": "eɛɜɝɚ", "oh": "oɔɵ",
}
_VOWEL_MAP = {ch: shape for shape, chars in _VOWELS.items() for ch in chars}
_SILENT = set(",.;:!?…\"'()[] ")


@dataclass
class Synth:
    pcm16: bytes
    sample_rate: int
    duration_s: float
    envelope: list[float]
    visemes: list[dict] = field(default_factory=list)
    synth_s: float = 0.0

    def pcm16_b64(self) -> str:
        return base64.b64encode(self.pcm16).decode("ascii")


def respell(text: str, pronunciations: dict[str, str]) -> str:
    """Replace whole words with their spoken respelling (case-insensitive). Used for names the G2P gets wrong."""
    for word, spoken in pronunciations.items():
        if word and spoken and word.strip() and spoken.strip():
            text = re.sub(rf"(?<!\w){re.escape(word.strip())}(?!\w)", spoken.strip(), text, flags=re.IGNORECASE)
    return text


class KokoroTTS:
    def __init__(self, model: Path, voices: Path):
        self.model_path, self.voices_path = model, voices
        self._k = None
        self._lock = asyncio.Lock()
        self.pronunciations: dict[str, str] = {}  # written form -> how to say it; applied to every synth

    async def load(self) -> None:
        def _load():
            from kokoro_onnx import Kokoro

            return Kokoro(str(self.model_path), str(self.voices_path))

        t0 = time.monotonic()
        self._k = await asyncio.to_thread(_load)
        log.info("kokoro loaded in %.2fs", time.monotonic() - t0)

    def voices(self) -> list[str]:
        return list(self._k.get_voices()) if self._k else []

    async def synth(self, text: str, voice: str, speed: float = 1.0, lang: str = "en-us") -> Synth:
        if self._k is None:
            raise RuntimeError("TTS not loaded")
        async with self._lock:  # one synthesis at a time keeps latency predictable on CPU
            return await asyncio.to_thread(self._synth_sync, text, voice, speed, lang)

    def _synth_sync(self, text: str, voice: str, speed: float, lang: str) -> Synth:
        t0 = time.monotonic()
        if self.pronunciations:
            text = respell(text, self.pronunciations)
        audio, sr = self._k.create(text, voice=voice, speed=speed, lang=lang)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak > 1.0:
            audio = audio / peak
        pcm16 = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
        dur = len(audio) / sr
        hop = max(1, sr // ENVELOPE_HZ)
        n = (len(audio) + hop - 1) // hop
        env = np.sqrt(np.array([np.mean(audio[i * hop:(i + 1) * hop] ** 2) if len(audio[i * hop:(i + 1) * hop]) else 0.0 for i in range(n)]))
        if env.size and env.max() > 0:
            env = np.clip(env / (np.percentile(env, 97) + 1e-6), 0, 1)
        visemes = self._visemes(text, dur, lang)
        return Synth(pcm16=pcm16, sample_rate=sr, duration_s=dur, envelope=[round(float(x), 3) for x in env],
                     visemes=visemes, synth_s=time.monotonic() - t0)

    def _visemes(self, text: str, dur: float, lang: str) -> list[dict]:
        try:
            ph = self._k.tokenizer.phonemize(text, lang=lang)
        except Exception:
            return []
        units = [c for c in ph if c not in "ˈˌː"]  # drop stress / length marks
        if not units:
            return []
        weights = [0.4 if c in _SILENT else (1.0 if c in _VOWEL_MAP else 0.7) for c in units]
        total = sum(weights) or 1.0
        t = 0.0
        out: list[dict] = []
        last_shape = None
        for c, w in zip(units, weights, strict=True):
            shape = _VOWEL_MAP.get(c, "sil" if c in _SILENT else None)
            if shape and shape != last_shape:
                out.append({"t": round(t * dur / total, 3), "shape": shape})
                last_shape = shape
            t += w
        return out[:200]
