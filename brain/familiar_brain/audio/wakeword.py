"""Wake word spotting with sherpa-onnx's transducer keyword spotter (English zipformer, 3.3M params).

The persona's wake phrase ("hey wisp") is turned into BPE pieces with the model's own sentencepiece
model at startup, so any phrase made of English-ish syllables works without retraining.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def phrase_to_keyword_line(bpe_model: Path, phrase: str, *, boost: float = 2.0, threshold: float = 0.25) -> str:
    import sentencepiece as spm

    sp = spm.SentencePieceProcessor()
    sp.load(str(bpe_model))
    words = re.sub(r"[^a-z' ]", " ", phrase.lower()).split()
    pieces = sp.encode(" ".join(w.upper() for w in words), out_type=str)
    display = "".join(w.title() for w in words)
    return " ".join(pieces) + f" :{boost} #{threshold} @{display}"


class WakeWord:
    def __init__(self, model_dir: Path, phrases: list[str], *, threshold: float = 0.25, threads: int = 1):
        import sherpa_onnx

        self.model_dir = model_dir
        self.phrases = phrases
        kw_file = model_dir / "keywords-familiar.txt"
        lines = [phrase_to_keyword_line(model_dir / "bpe.model", p, threshold=threshold) for p in phrases]
        kw_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("wake words: %s", lines)
        enc = next(model_dir.glob("encoder-*.int8.onnx"))
        dec = next(model_dir.glob("decoder-*.int8.onnx"))
        joi = next(model_dir.glob("joiner-*.int8.onnx"))
        self._kws = sherpa_onnx.KeywordSpotter(
            tokens=str(model_dir / "tokens.txt"), encoder=str(enc), decoder=str(dec), joiner=str(joi),
            num_threads=threads, max_active_paths=4, keywords_file=str(kw_file),
            keywords_score=2.0, keywords_threshold=threshold, num_trailing_blanks=1, provider="cpu",
        )
        self._stream = self._kws.create_stream()

    def feed(self, frame: np.ndarray) -> str | None:
        """Feed 16 kHz float32 samples; returns the display name of a detected keyword, else None."""
        self._stream.accept_waveform(16000, frame)
        hit = None
        while self._kws.is_ready(self._stream):
            self._kws.decode_stream(self._stream)
            r = self._kws.get_result(self._stream)
            if r:
                hit = r
                self._kws.reset_stream(self._stream)
        return hit

    def reset(self) -> None:
        self._stream = self._kws.create_stream()
