"""One conversational turn: LLM stream -> tag stripping -> sentence chunking -> TTS -> shell."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from ..llm import LlmClient, StreamStats
from ..persona import Persona
from ..tts import ENVELOPE_HZ, KokoroTTS
from .textflow import SentenceChunker, TagStripper, speakable

log = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]

EXPRESSION_INSTRUCTIONS = (
    "\n\nYou are voiced aloud through an animated avatar. You may start a sentence with one expression tag "
    "from this list to change your face: {tags}. Example: \"[happy] Done, Spotify is open.\" Use at most one "
    "tag per sentence and never invent other tags. Speak in short, natural sentences; no markdown, no lists, "
    "no emoji."
)


def build_system_prompt(persona: Persona) -> str:
    return persona.system_prompt.strip() + EXPRESSION_INSTRUCTIONS.format(tags=", ".join(f"[{t}]" for t in persona.expression_tags()))


class Turn:
    def __init__(self, *, llm: LlmClient, tts: KokoroTTS | None, persona: Persona, history: list[dict],
                 send: Send, speak: bool = True, max_tokens: int = 400):
        self.llm, self.tts, self.persona, self.history, self.send = llm, tts, persona, history, send
        self.speak = speak and tts is not None
        self.max_tokens = max_tokens
        self.turn_id = uuid.uuid4().hex[:8]
        self.interrupted = False
        self._tts_queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        self.first_audio_s: float | None = None

    async def run(self, user_text: str) -> str:
        t0 = time.monotonic()
        messages = [{"role": "system", "content": build_system_prompt(self.persona)}, *self.history,
                    {"role": "user", "content": user_text}]
        stripper = TagStripper(set(self.persona.expression_tags()))
        chunker = SentenceChunker()
        stats = StreamStats()
        full = ""
        tts_task = asyncio.create_task(self._tts_worker(t0)) if self.speak else None
        await self.send({"type": "state", "state": "thinking"})
        try:
            async for tok in self.llm.stream(messages, max_tokens=self.max_tokens, stats=stats):
                clean = stripper.feed(tok)
                self._flush_tags(stripper, chunker)
                if clean:
                    full += clean
                    await self.send({"type": "reply.token", "turn_id": self.turn_id, "text": clean})
                    for s in chunker.feed(clean):
                        await self._tts_queue.put((chunker.emitted, s))
            tail = stripper.flush()
            self._flush_tags(stripper, chunker)
            if tail:
                full += tail
                await self.send({"type": "reply.token", "turn_id": self.turn_id, "text": tail})
                for s in chunker.feed(tail):
                    await self._tts_queue.put((chunker.emitted, s))
            for s in chunker.flush():
                await self._tts_queue.put((chunker.emitted, s))
        except asyncio.CancelledError:
            self.interrupted = True
            raise
        finally:
            await self._tts_queue.put(None)
            if tts_task:
                if self.interrupted:
                    tts_task.cancel()
                try:
                    await tts_task
                except (asyncio.CancelledError, Exception) as e:  # noqa: BLE001
                    if not isinstance(e, asyncio.CancelledError):
                        log.exception("tts worker failed")
            full = full.strip()
            if full and not self.interrupted:
                self.history.append({"role": "user", "content": user_text})
                self.history.append({"role": "assistant", "content": full})
                del self.history[:-20]
            await self.send({"type": "reply.done", "turn_id": self.turn_id, "text": full, "interrupted": self.interrupted,
                             "stats": {k: v for k, v in {
                                 "ttft_s": stats.ttft_s, "total_s": stats.total_s, "tokens": stats.gen_tokens,
                                 "tok_s": stats.tok_s, "prompt_tokens": stats.prompt_tokens,
                                 "first_audio_s": self.first_audio_s}.items() if v is not None}})
        return full

    def _flush_tags(self, stripper: TagStripper, chunker: SentenceChunker) -> None:
        """Expression tags apply to the sentence that is currently being accumulated."""
        if not stripper.tags:
            return
        tags, stripper.tags = stripper.tags, []
        for _, tag in tags:
            asyncio.get_running_loop().create_task(
                self.send({"type": "expression", "tag": tag, "sentence_id": chunker.emitted + 1}))

    async def _tts_worker(self, t0: float) -> None:
        while True:
            item = await self._tts_queue.get()
            if item is None:
                return
            sid, sentence = item
            text = speakable(sentence)
            if not text:
                continue
            try:
                syn = await self.tts.synth(text, self.persona.voice, self.persona.voice_speed)
            except Exception:
                log.exception("tts failed for %r", text)
                continue
            if self.first_audio_s is None:
                self.first_audio_s = time.monotonic() - t0
                await self.send({"type": "state", "state": "speaking"})
            await self.send({"type": "reply.audio", "turn_id": self.turn_id, "sentence_id": sid, "text": sentence,
                             "sample_rate": syn.sample_rate, "pcm16_b64": syn.pcm16_b64(), "duration_s": round(syn.duration_s, 3),
                             "envelope_hz": ENVELOPE_HZ, "envelope": syn.envelope, "visemes": syn.visemes})
            log.debug("tts %d: %.2fs audio in %.2fs", sid, syn.duration_s, syn.synth_s)
