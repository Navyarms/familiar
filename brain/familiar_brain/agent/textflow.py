"""Turn a token stream into (a) clean display text, (b) expression events, (c) sentences for TTS.

Expression tags look like `[happy]` and are stripped from both the display text and the spoken text.
The chunker emits the first sentence early (at the first clause boundary once it is long enough) so the
first audio arrives fast, then whole sentences afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TAG_RE = re.compile(r"\[([a-zA-Z_]+)\]")
SENTENCE_END = re.compile(r"([.!?…]+[\"')\]]*)(\s+|$)")
CLAUSE_END = re.compile(r"([,;:—-]+)(\s+)")
FIRST_SENTENCE_MIN_CHARS = 24
FIRST_SENTENCE_MAX_CHARS = 90
MAX_SENTENCE_CHARS = 260


@dataclass
class TagStripper:
    """Incremental tag stripper that survives tags split across tokens."""

    known_tags: set[str]
    _buf: str = ""
    tags: list[tuple[int, str]] = field(default_factory=list)  # (char offset in clean text, tag)
    clean_len: int = 0

    def feed(self, token: str) -> str:
        self._buf += token
        out = ""
        while self._buf:
            i = self._buf.find("[")
            if i < 0:
                out += self._buf
                self._buf = ""
                break
            out += self._buf[:i]
            rest = self._buf[i:]
            m = TAG_RE.match(rest)
            if m:
                tag = m.group(1).lower()
                if tag in self.known_tags:
                    self.tags.append((self.clean_len + len(out), tag))
                else:  # unknown bracket text: keep it
                    out += m.group(0)
                self._buf = rest[m.end():]
            elif "]" in rest or len(rest) > 24:  # not a tag after all
                out += rest[0]
                self._buf = rest[1:]
            else:  # possibly a tag still arriving
                self._buf = rest
                break
        self.clean_len += len(out)
        return out

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        self.clean_len += len(out)
        return out


@dataclass
class SentenceChunker:
    _buf: str = ""
    emitted: int = 0

    def feed(self, text: str) -> list[str]:
        self._buf += text
        out: list[str] = []
        while True:
            s = self._take_sentence()
            if s is None:
                break
            out.append(s)
        return out

    def _take_sentence(self) -> str | None:
        buf = self._buf
        if not buf.strip():
            return None
        m = SENTENCE_END.search(buf)
        if m and m.end(1) >= 2:
            s, self._buf = buf[: m.end(1)], buf[m.end():]
            return self._clean(s)
        # first sentence: cut at a clause boundary once long enough, so first audio is fast
        if self.emitted == 0 and len(buf) >= FIRST_SENTENCE_MIN_CHARS:
            cm = None
            for cm_ in CLAUSE_END.finditer(buf):
                if cm_.end(1) >= FIRST_SENTENCE_MIN_CHARS:
                    cm = cm_
                    break
            if cm and cm.end(1) <= FIRST_SENTENCE_MAX_CHARS:
                s, self._buf = buf[: cm.end(1)], buf[cm.end():]
                return self._clean(s)
        if len(buf) >= MAX_SENTENCE_CHARS:  # runaway sentence, cut at last space
            cut = buf.rfind(" ", 0, MAX_SENTENCE_CHARS)
            cut = cut if cut > 40 else MAX_SENTENCE_CHARS
            s, self._buf = buf[:cut], buf[cut:]
            return self._clean(s)
        return None

    def flush(self) -> list[str]:
        s, self._buf = self._buf, ""
        s = self._clean(s)
        return [s] if s else []

    def _clean(self, s: str) -> str:
        s = re.sub(r"\s+", " ", s).strip()
        if s:
            self.emitted += 1
        return s


def strip_tags(text: str) -> str:
    return TAG_RE.sub("", text)


def speakable(text: str) -> str:
    """Remove things TTS reads badly: markdown emphasis, code fences, bullets, URLs' protocol."""
    t = re.sub(r"```.*?```", " code block omitted ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"[*_#>]+", "", t)
    t = re.sub(r"https?://", "", t)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.M)
    return re.sub(r"\s+", " ", t).strip()
