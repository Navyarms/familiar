"""One conversational turn: LLM stream -> tag stripping -> sentence chunking -> TTS -> shell, with tool calls.

Agent loop: the model may answer, call tools, or both. Content streamed before a tool call is spoken
("Sure, opening Spotify"), tools run (after confirmation when their permission says so), their results
go back to the model, and the final answer is spoken. At most `max_steps` model calls per turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from ..llm import LlmClient, StreamStats
from ..persona import Persona
from ..tools.registry import ToolContext, ToolRegistry
from ..tts import ENVELOPE_HZ, KokoroTTS
from .textflow import SentenceChunker, TagStripper, speakable

log = logging.getLogger(__name__)

Send = Callable[[dict], Awaitable[None]]
Confirm = Callable[[str, str, dict, str, str], Awaitable[bool]]  # request_id, tool, args, description, risk -> allowed

EXPRESSION_INSTRUCTIONS = (
    "\n\nYou are voiced aloud through an animated avatar. You may start a sentence with one expression tag "
    "from this list to change your face: {tags}. Example: \"[happy] Done, Spotify is open.\" Use at most one "
    "tag per sentence and never invent other tags. Speak in short, natural sentences; no markdown, no lists, "
    "no emoji."
)
TOOL_INSTRUCTIONS = (
    "\n\nYou operate this Windows PC through tools. Rules:\n"
    "1. You know nothing about this PC by yourself: not the clipboard, files, open windows, volume, battery, or anything "
    "current on the web. To answer about any of those you MUST call the matching tool first (clipboard_read, "
    "search_files, list_windows, get_volume, system_info, web_search). Never guess their contents.\n"
    "2. When the user asks you to do something (open, close, set, find, copy, remind, remember, mute, play), call the "
    "tool right away; you may say a few words first. Actions only happen through tools: never say something is done, "
    "remembered, copied or set unless a tool result says so.\n"
    "3. After tool results, reply in one short sentence with what happened. If a tool was declined or failed, say so "
    "and stop; do not retry the same call.\n"
    "4. search_files: put only the file-name words in query (often empty), the type in ext, and days=1 for today or "
    "yesterday, days=7 for this week. open_app launches programs by name. get_weather for weather. web_search for news, "
    "prices, scores, or facts you are unsure about; if the snippets do not contain the answer, call fetch_page on the "
    "best result's url before answering. remember when the user tells you something about themselves to keep.\n"
    "5. Operating apps: one tool call at a time, check each result. Inside an app: focus_window or open_app first, then "
    "find_elements / click_element / set_element_text / read_window_text by label (fast, exact). type_text and press_keys "
    "for typing and shortcuts; click only with coordinates from find_elements; look_at_screen only when the element tools "
    "cannot see what you need. Bluetooth: set_bluetooth. You may say two to four words about the step, but ALWAYS put the "
    "tool call in the same response as those words; a response that only announces an action does nothing. If something "
    "is not where you expect after two tries, stop and tell the user what you see."
)
MEMORY_PREFIX = "\n\nThings you remember about the user:\n"
ANNOUNCE_RE = re.compile(r"^(?:\[\w+\]\s*)?(?:i['\u2019]ll|i will|let me|i['\u2019]m going to|checking|clicking|opening|looking|reading|typing|turning|setting|searching|finding|closing|pressing|focusing|snapping|minimizing|maximizing)\b", re.I)


def _ex(user: str, tool: str, args: dict, result: str, reply: str, cid: str, said: str | None = None) -> list[dict]:
    import json as _json

    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": said, "tool_calls": [{"id": cid, "type": "function", "function": {"name": tool, "arguments": _json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": cid, "name": tool, "content": _json.dumps({"ok": True, "summary": result})},
        {"role": "assistant", "content": reply},
    ]


# Worked examples for small models: they sit in the cached prefix, so they cost nothing per turn.
FEW_SHOT: list[dict] = [
    *_ex("Remember that I take my coffee black.", "remember", {"fact": "The user takes their coffee black"}, "I'll remember that", "Noted, black coffee it is.", "ex1"),
    *_ex("Close Notepad.", "close_app", {"name": "Notepad"}, "asked Notepad to close", "Notepad is closed.", "ex2", said="Closing Notepad."),
    *_ex("What does the Mail window say?", "read_window_text", {"window": "Mail"}, "read 240 characters from Mail: 'Hi, can we move lunch to 1?'",
         "The email asks if lunch can move to one o'clock.", "ex3", said="Reading it."),
]


def build_system_prompt(persona: Persona, *, tools: bool = False, memories: list[str] | None = None) -> str:
    p = persona.system_prompt.strip() + EXPRESSION_INSTRUCTIONS.format(tags=", ".join(f"[{t}]" for t in persona.expression_tags()))
    if tools:
        p += TOOL_INSTRUCTIONS
    if memories:
        p += MEMORY_PREFIX + "\n".join(f"- {m}" for m in memories[-20:])
    return p


class Turn:
    def __init__(self, *, llm: LlmClient, tts: KokoroTTS | None, persona: Persona, history: list[dict],
                 send: Send, speak: bool = True, max_tokens: int = 400, registry: ToolRegistry | None = None,
                 ctx: ToolContext | None = None, confirm: Confirm | None = None, memories: list[str] | None = None,
                 max_steps: int = 10):
        self.llm, self.tts, self.persona, self.history, self.send = llm, tts, persona, history, send
        self.speak = speak and tts is not None
        self.max_tokens = max_tokens
        self.registry, self.ctx, self.confirm, self.memories, self.max_steps = registry, ctx, confirm, memories, max_steps
        self.turn_id = uuid.uuid4().hex[:8]
        self.interrupted = False
        self._tts_queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        self.first_audio_s: float | None = None
        self.tool_log: list[dict] = []
        self._sid = 0  # sentence counter across steps
        self._spoke_this_step = False

    # ------------------------------------------------------------------ main
    async def run(self, user_text: str) -> str:
        t0 = time.monotonic()
        use_tools = bool(self.registry and self.ctx and self.registry.available())
        now = time.strftime("%A %B %d %Y, %I:%M %p").replace(" 0", " ")
        # Time and remembered facts ride on the user turn, not the system prompt, so the cached prefix stays stable.
        note = f"(It is now {now}. Use this if the question involves the time or date; do not mention this note."
        if self.memories:
            note += " Things you remember about the user: " + "; ".join(self.memories[-20:]) + "."
        user_content = f"{user_text}\n\n{note})"
        messages = [{"role": "system", "content": build_system_prompt(self.persona, tools=use_tools)},
                    *(FEW_SHOT if use_tools else []), *self.history, {"role": "user", "content": user_content}]
        tools = self.registry.openai_tools() if use_tools else None
        full_spoken = ""
        last_stats = StreamStats()
        tts_task = asyncio.create_task(self._tts_worker(t0)) if self.speak else None
        await self.send({"type": "state", "state": "thinking"})
        try:
            nudged = False
            force_tool = False
            for step in range(self.max_steps):
                stats = StreamStats()
                last_stats = stats if step == 0 else last_stats
                content = await self._stream_step(messages, tools, stats, force_tool=force_tool)
                force_tool = False
                full_spoken += content
                if not stats.tool_calls:
                    # Small models sometimes narrate the action and stop. Once per turn, push them to actually call it.
                    first = re.split(r"[.!?\n]", content.strip(), maxsplit=1)[0].strip()
                    if use_tools and not nudged and step < self.max_steps - 1 and len(first) <= 40 and ANNOUNCE_RE.match(first):
                        nudged = True
                        force_tool = True  # the retry must produce a tool call (tool_choice=required)
                        log.info("nudging: model announced %r without a tool call", content.strip()[:60])
                        messages.append({"role": "assistant", "content": content.strip()})
                        messages.append({"role": "user", "content": "(You announced an action but did not call the tool. Call it now.)"})
                        continue
                    break
                if step == self.max_steps - 1:
                    log.warning("tool step limit reached")
                    break
                messages.append({"role": "assistant", "content": content or None, "tool_calls": [c.openai() for c in stats.tool_calls]})
                for call in stats.tool_calls:
                    result = await self._run_tool(call)
                    messages.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                     "content": json.dumps(result, default=str)[:4000]})
                await self.send({"type": "state", "state": "thinking"})
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
            full_spoken = full_spoken.strip()
            if not self.interrupted and (full_spoken or self.tool_log):
                self.history.append({"role": "user", "content": user_text})
                summary = full_spoken or "(did: " + "; ".join(t["summary"] for t in self.tool_log) + ")"
                self.history.append({"role": "assistant", "content": summary})
                del self.history[:-20]
            await self.send({"type": "reply.done", "turn_id": self.turn_id, "text": full_spoken, "interrupted": self.interrupted,
                             "stats": {k: v for k, v in {
                                 "ttft_s": last_stats.ttft_s, "total_s": round(time.monotonic() - t0, 3), "tokens": last_stats.gen_tokens,
                                 "tok_s": last_stats.tok_s, "prompt_tokens": last_stats.prompt_tokens,
                                 "first_audio_s": self.first_audio_s, "tools": len(self.tool_log) or None}.items() if v is not None}})
        return full_spoken

    async def _stream_step(self, messages: list[dict], tools: list[dict] | None, stats: StreamStats, *, force_tool: bool = False) -> str:
        stripper = TagStripper(set(self.persona.expression_tags()))
        chunker = SentenceChunker()
        chunker.emitted = self._sid
        content = ""
        self._spoke_this_step = False
        async for tok in self.llm.stream(messages, max_tokens=self.max_tokens, stats=stats, tools=tools, temperature=0.4 if tools else 0.7,
                                         tool_choice="required" if (force_tool and tools) else "auto"):
            clean = stripper.feed(tok)
            self._flush_tags(stripper, chunker)
            if clean:
                content += clean
                await self.send({"type": "reply.token", "turn_id": self.turn_id, "text": clean})
                for s in chunker.feed(clean):
                    await self._tts_queue.put((chunker.emitted, s))
        tail = stripper.flush()
        self._flush_tags(stripper, chunker)
        if tail:
            content += tail
            await self.send({"type": "reply.token", "turn_id": self.turn_id, "text": tail})
            for s in chunker.feed(tail):
                await self._tts_queue.put((chunker.emitted, s))
        for s in chunker.flush():
            await self._tts_queue.put((chunker.emitted, s))
        self._sid = chunker.emitted
        self._spoke_this_step = bool(content.strip())
        if content and stats.tool_calls:
            await self.send({"type": "reply.token", "turn_id": self.turn_id, "text": "\n"})
            content += "\n"
        return content

    # ------------------------------------------------------------------ tools
    async def _run_tool(self, call) -> dict:
        request_id = uuid.uuid4().hex[:8]
        spec = self.registry.tools.get(call.name)
        try:
            args = call.args()
        except Exception as e:  # noqa: BLE001
            args, parse_err = {}, str(e)
        else:
            parse_err = None
        if spec is None:
            result = {"ok": False, "summary": f"there is no tool called {call.name}"}
        elif parse_err:
            result = {"ok": False, "summary": f"bad arguments for {call.name}: {parse_err[:120]}"}
        else:
            perm = self.registry.permission(call.name, args)
            desc = spec.human(args)
            if perm == "blocked":
                result = {"ok": False, "summary": f"{call.name} is turned off in settings"}
            else:
                allowed = True
                if perm == "confirm":
                    await self.send({"type": "state", "state": "needs_confirm", "detail": desc})
                    await self.send({"type": "tool.request_confirm", "request_id": request_id, "tool": call.name, "args": args,
                                     "description": desc, "risk": spec.risk})
                    self._say(f"Should I {desc}?")
                    allowed = await self.confirm(request_id, call.name, args, desc, spec.risk) if self.confirm else False
                if not allowed:
                    result = {"ok": False, "summary": "the user declined" if perm == "confirm" else "not allowed"}
                else:
                    await self.send({"type": "state", "state": "executing", "detail": desc})
                    if spec.narrate and not self._spoke_this_step and getattr(self.ctx.settings, "narrate_tools", True):
                        self._say(desc[0].upper() + desc[1:] + ".")
                    await self.send({"type": "tool.start", "request_id": request_id, "tool": call.name, "args": args, "description": desc})
                    result = await self.registry.run(call.name, args, self.ctx)
        await self.send({"type": "tool.result", "request_id": request_id, "tool": call.name, "ok": bool(result.get("ok")),
                         "summary": result.get("summary", "")})
        self.tool_log.append({"tool": call.name, "args": args, "ok": bool(result.get("ok")), "summary": result.get("summary", "")})
        log.info("tool %s(%s) -> %s", call.name, json.dumps(args)[:120], result.get("summary", "")[:120])
        return {k: v for k, v in result.items() if k in ("ok", "summary", "result")}

    def _say(self, text: str) -> None:
        """Speak a line that did not come from the model (confirmation prompts)."""
        if self.speak:
            self._sid += 1
            self._tts_queue.put_nowait((self._sid, text))

    def _flush_tags(self, stripper: TagStripper, chunker: SentenceChunker) -> None:
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
