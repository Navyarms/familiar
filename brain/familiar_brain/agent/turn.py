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
    "1. You know nothing about this PC or the outside world by yourself: not the clipboard, files, open windows, the "
    "active window, volume, battery, Bluetooth, the weather, or anything current on the web. To answer about any of "
    "those you MUST call the matching tool first (clipboard_read, search_files, list_windows, get_foreground_window, "
    "read_window_text, get_volume, system_info, set_bluetooth check, get_weather, web_search). Never guess their "
    "contents; never invent window names or weather.\n"
    "2. When the user asks you to do something (open, close, set, find, copy, remind, remember, mute, play), call the "
    "tool right away; you may say a few words first. Actions only happen through tools: never say something is done, "
    "remembered, copied or set unless a tool result says so.\n"
    "3. After tool results, reply in one short sentence with what happened. If a tool was declined or failed, say so "
    "and stop; do not retry the same call.\n"
    "4. search_files: put only the file-name words in query (often empty), the type in ext, and days=1 for today or "
    "yesterday, days=7 for this week. open_app launches programs by name. get_weather for weather. web_search for news, "
    "prices, scores, or facts you are unsure about; if the snippets do not contain the answer, call fetch_page on the "
    "best result's url before answering. remember when the user tells you something about themselves to keep.\n"
    "5. Never state what is open, focused, on the clipboard, or on screen unless a tool told you in THIS conversation; "
    "if you have not checked, call the tool. Examples of correct choices: 'remember that I take my coffee black' -> "
    "remember; 'close Notepad' -> close_app; 'what does this dialog say?' -> read_window_text; 'what am I looking at?' "
    "-> get_foreground_window; 'what's open?' -> list_windows; 'is Bluetooth on?' -> set_bluetooth(check); 'what's the "
    "volume?' -> get_volume; 'scroll down' -> scroll(direction=down); 'type hi' -> type_text (do not focus first "
    "unless the user named a window); 'what's the weather like?' -> get_weather with no place (it uses the user's "
    "location); 'where am I?' -> answer from what you know about the user, no tool.\n"
    "6. Operating apps: one tool call at a time, check each result. Inside an app: focus_window or open_app first, then "
    "find_elements / click_element / set_element_text / read_window_text by label (fast, exact). type_text and press_keys "
    "for typing and shortcuts; click only with coordinates from find_elements; look_at_screen only when the element tools "
    "cannot see what you need. Bluetooth: set_bluetooth. You may say two to four words about the step, but ALWAYS put the "
    "tool call in the same response as those words; a response that only announces an action does nothing. If something "
    "is not where you expect after two tries, stop and tell the user what you see."
)
MEMORY_PREFIX = "\n\nThings you remember about the user:\n"
TIME_WORDS = re.compile(r"\b(time|date|today|tomorrow|yesterday|tonight|morning|afternoon|evening|week|month|year|day|hour|minute|o'?clock|late|early|schedule|calendar|when|now|clock)\b", re.I)
# Questions that can only be answered by a tool. In a chatty conversation the small model sometimes answers these
# from thin air ("I don't have live weather"), so the first step runs with tool_choice=required instead of hoping.
MUST_TOOL_RE = re.compile(r"\b(weather|forecast|(?:is|will) it (?:going to )?(?:rain|snow|storm)|how (?:hot|cold|warm) is it|"
                          r"temperature outside|(?:what(?:'s| is) (?:on|in) (?:my|the) clipboard)|(?:what(?:'s| is) (?:open|running)|which windows))\b", re.I)
ANNOUNCE_RE = re.compile(r"^(?:\[\w+\]\s*)?(?:i['\u2019]ll|i will|let me|i['\u2019]m going to|checking|clicking|opening|looking|reading|typing|turning|setting|searching|finding|closing|pressing|focusing|snapping|minimizing|maximizing)\b", re.I)


def _ex(user: str, tool: str, args: dict, result: str, reply: str, cid: str, said: str | None = None) -> list[dict]:
    import json as _json

    return [
        {"role": "user", "content": user},
        {"role": "assistant", "content": said, "tool_calls": [{"id": cid, "type": "function", "function": {"name": tool, "arguments": _json.dumps(args)}}]},
        {"role": "tool", "tool_call_id": cid, "name": tool, "content": _json.dumps({"ok": True, "summary": result})},
        {"role": "assistant", "content": reply},
    ]


# Worked examples as real messages teach the "say a few words AND call the tool in one response" format; without them the
# 4B model narrates and stops. Their tool RESULTS must not contain reusable state (window lists, app names as facts):
# an example whose result listed "Excel, Thunderbird" made the model claim those were open. Keep results neutral.
FEW_SHOT: list[dict] = [
    # The model repeats example facts as if the user had said them (it told a user they "take their coffee black"),
    # and without a remember example it fakes "got it" with no tool call. So the example fact is one that is true
    # and harmless for every user.
    *_ex("Remember that I'm using you on my Windows PC.", "remember", {"fact": "The user runs Familiar on a Windows PC"},
         "I'll remember that", "Noted.", "ex1"),
    *_ex("Close Notepad.", "close_app", {"name": "Notepad"}, "asked Notepad to close", "Notepad is closed.", "ex2", said="Closing Notepad."),
    *_ex("What does this dialog say?", "read_window_text", {}, "read 48 characters: 'Do you want to save changes?'",
         "It's asking whether to save your changes.", "ex3", said="Reading it."),
]


USER_NAME_NOTE = "\n\nThe user's name is {name}. Use it now and then, the way a friend would, not in every reply."
LOCATION_NOTE = {
    "settings": "\n\nThe user is in {loc}. Use that for weather, local time, nearby places and when they ask where they are.",
    "network": ("\n\nThe user is probably in {loc} (a guess from their network connection, so it may be off). Use it for weather "
                "and local questions, and say it is approximate if they ask where they are."),
}


PINNED_PREFIX = "\n\nThings the user asked you to remember (true until they say otherwise):\n"


def build_system_prompt(persona: Persona, *, tools: bool = False, memories: list[str] | None = None,
                        user_name: str | None = None, location: str | None = None, location_source: str = "settings",
                        pinned: list[str] | None = None) -> str:
    p = persona.system_prompt.strip() + EXPRESSION_INSTRUCTIONS.format(tags=", ".join(f"[{t}]" for t in persona.expression_tags()))
    if user_name and user_name.strip():
        p += USER_NAME_NOTE.format(name=user_name.strip())
    if location and location.strip() and location_source in LOCATION_NOTE:
        p += LOCATION_NOTE[location_source].format(loc=location.strip())
    if pinned:
        p += PINNED_PREFIX + "\n".join(f"- {m}" for m in pinned[:12])
    if tools:
        p += TOOL_INSTRUCTIONS
    if memories:
        p += MEMORY_PREFIX + "\n".join(f"- {m}" for m in memories[-20:])
    return p


class Turn:
    def __init__(self, *, llm: LlmClient, tts: KokoroTTS | None, persona: Persona, history: list[dict],
                 send: Send, speak: bool = True, max_tokens: int = 400, registry: ToolRegistry | None = None,
                 ctx: ToolContext | None = None, confirm: Confirm | None = None, memories: list[str] | None = None,
                 max_steps: int = 10, user_name: str | None = None, location: str | None = None, location_source: str = "settings",
                 pinned: list[str] | None = None):
        self.llm, self.tts, self.persona, self.history, self.send = llm, tts, persona, history, send
        self.speak = speak and tts is not None
        self.user_name, self.location, self.location_source, self.pinned = user_name, location, location_source, pinned
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
        # Time (only when the message is about time) and remembered facts ride on the user turn, not the system
        # prompt, so the cached prefix stays stable. Small models tend to echo notes, so keep them short.
        parts = []
        if TIME_WORDS.search(user_text):
            parts.append("It is now " + time.strftime("%A %B %d %Y, %I:%M %p").replace(" 0", " ") + ".")
        if self.memories:
            parts.append("You remember: " + "; ".join(self.memories[-12:]) + ".")
        user_content = user_text if not parts else f"{user_text}\n\n(Private context, do not read aloud: {' '.join(parts)})"
        # Questions that need live data get only the last exchange of history: after a few chatty turns the 4B model
        # answers "what's the weather like?" in prose (even inventing a forecast) and llama-server's
        # tool_choice=required does not override that, while the same request with a short history calls the tool.
        must_tool = bool(use_tools and MUST_TOOL_RE.search(user_text))
        hist = self.history[-2:] if must_tool else self.history
        if must_tool:
            user_content = user_text  # the memory/time note on the user turn has the same chat-mode effect; the tool needs neither
        messages = [{"role": "system", "content": build_system_prompt(self.persona, tools=use_tools, user_name=self.user_name,
                                                                         location=self.location, location_source=self.location_source,
                                                                         pinned=self.pinned)},
                    *(FEW_SHOT if use_tools else []), *hist, {"role": "user", "content": user_content}]
        tools = self.registry.openai_tools() if use_tools else None
        full_spoken = ""
        last_stats = StreamStats()
        tts_task = asyncio.create_task(self._tts_worker(t0)) if self.speak else None
        await self.send({"type": "state", "state": "thinking"})
        try:
            nudged = False
            force_tool = must_tool
            if force_tool:
                log.info("question needs live data: tool_choice=required, history trimmed to the last exchange")
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
        recent: list[str] = []  # last sentences, to cut off a model stuck repeating itself
        looped = False
        async for tok in self.llm.stream(messages, max_tokens=self.max_tokens, stats=stats, tools=tools, temperature=0.4 if tools else 0.7,
                                         tool_choice="required" if (force_tool and tools) else "auto"):
            clean = stripper.feed(tok)
            self._flush_tags(stripper, chunker)
            if clean:
                content += clean
                await self.send({"type": "reply.token", "turn_id": self.turn_id, "text": clean})
                for s in chunker.feed(clean):
                    key = " ".join(s.lower().split())
                    if len(recent) >= 2 and key == recent[-1] == recent[-2]:
                        looped = True
                        break
                    recent = (recent + [key])[-3:]
                    await self._tts_queue.put((chunker.emitted, s))
            if looped:
                log.warning("stopped a repetition loop after %r", recent[-1][:60])
                break
        tail = "" if looped else stripper.flush()
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
