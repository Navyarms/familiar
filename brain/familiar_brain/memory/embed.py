"""Sentence embeddings with bge-small-en-v1.5 (int8 ONNX) through onnxruntime + tokenizers. ~5 ms per sentence on CPU."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class Embedder:
    dim = 384

    def __init__(self, model_dir: Path, *, threads: int = 2):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        t0 = time.monotonic()
        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(max_length=256)
        self.tok.enable_padding(pad_id=0, pad_token="[PAD]")
        so = ort.SessionOptions()
        so.intra_op_num_threads = threads
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(model_dir / "model_quantized.onnx"), so, providers=["CPUExecutionProvider"])
        self.inputs = {i.name for i in self.sess.get_inputs()}
        log.info("embedder loaded in %.2fs", time.monotonic() - t0)

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2-normalized float32 vectors, shape (n, 384). bge uses the CLS token."""
        if not texts:
            return np.zeros((0, self.dim), np.float32)
        enc = self.tok.encode_batch([t.strip() or "." for t in texts])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self.inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        out = self.sess.run(None, feed)[0]  # (n, seq, 384)
        cls = out[:, 0, :].astype(np.float32)
        norms = np.linalg.norm(cls, axis=1, keepdims=True) + 1e-9
        return cls / norms

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


def cosine(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a: (d,), b: (n, d) both normalized -> (n,)"""
    if b.size == 0:
        return np.zeros((0,), np.float32)
    return b @ a
