"""FastAPI + WebSocket server. One Brain per process; any number of shell sockets (usually one)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from familiar_protocol import parse_shell_message
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .agent.turn import FEW_SHOT, Turn, build_system_prompt
from .audio import FileSource, MicSource
from .audio.pipeline import PipelineConfig, VoicePipeline
from .audio.stt import WhisperSTT, default_stt_threads
from .audio.vad import SileroVAD
from .audio.wakeword import WakeWord
from .config import BRAIN_VERSION, Paths, Settings
from .llm import LlamaServer, LlmClient, default_threads
from .memory import Embedder, MemoryStore, extract_and_summarize
from .models import ModelManager
from .persona import Persona, list_personas, load_persona
from .persona import manage as personas
from .tools import REGISTRY, ToolContext
from .tts import KokoroTTS

log = logging.getLogger(__name__)


class Brain:
    def __init__(self, paths: Paths, settings: Settings):
        self.paths, self.settings = paths, settings
        self.models = ModelManager(paths, settings)
        self.persona: Persona | None = None
        self.llama: LlamaServer | None = None
        self.llm: LlmClient | None = None
        self.tts: KokoroTTS | None = None
        self.history: list[dict] = []
        self.state = "booting"
        self.sockets: set[WebSocket] = set()
        self.turn_task: asyncio.Task | None = None
        self.boot_task: asyncio.Task | None = None
        self.download_task: asyncio.Task | None = None
        self.ready = False
        self.voice: VoicePipeline | None = None
        self.mic_file: str | None = None  # dev: feed a wav instead of the microphone
        # tools
        self.registry = REGISTRY
        self.registry.configure(settings)
        self.kill_event = asyncio.Event()
        self.pending_confirms: dict[str, asyncio.Future] = {}
        self.memory = MemoryStore(paths.home / "memory.sqlite")  # keyword-only until the embedder loads
        self.memory_task: asyncio.Task | None = None
        self._turns_since_memory = 0
        self._last_activity = time.monotonic()
        self.tool_ctx: ToolContext | None = None
        self._announce_n = 0
        self._announce_queue: list[str] = []

    # ------------------------------------------------------------ messaging
    async def send(self, msg: dict) -> None:
        if msg.get("type") == "mic.state":
            await self._on_mic_mode(msg["mode"])
        dead = []
        data = json.dumps(msg)
        for ws in list(self.sockets):
            try:
                await ws.send_text(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)

    async def set_state(self, state: str, detail: str | None = None) -> None:
        self.state = state
        if self.voice:
            self.voice.busy = state in ("thinking", "speaking", "executing")
        await self.send({"type": "state", "state": state, "detail": detail})

    async def _on_mic_mode(self, mode: str) -> None:
        if mode == "listening" and self.state == "idle":
            await self.set_state("listening")
        elif mode in ("wake", "idle", "off") and self.state == "listening":
            await self.set_state("idle")

    def hello(self) -> dict:
        return {"type": "hello", "protocol": 1, "brain_version": BRAIN_VERSION,
                "persona": self.persona.info() if self.persona else None}

    def tools_info(self) -> dict:
        return {"type": "tools.info", "tools": self.registry.info()}

    def _ctx(self) -> ToolContext:
        if self.tool_ctx is None:
            self.tool_ctx = ToolContext(paths=self.paths, settings=self.settings, send=self.send, announce=self.announce,
                                        loop=asyncio.get_running_loop(), kill=self.kill_event,
                                        extra={"memory": self.memory, "brain": self})
        return self.tool_ctx

    async def confirm(self, request_id: str, tool: str, args: dict, description: str, risk: str) -> bool:
        """Wait for tool.confirm / tool.deny from the shell (or a spoken yes/no). Times out as a deny."""
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.pending_confirms[request_id] = fut
        try:
            return bool(await asyncio.wait_for(fut, timeout=self.settings.confirm_timeout_s))
        except TimeoutError:
            return False
        finally:
            self.pending_confirms.pop(request_id, None)

    def resolve_confirm(self, request_id: str | None, allowed: bool) -> bool:
        """Resolve one pending confirmation (None = the oldest). Returns whether anything was pending."""
        if not self.pending_confirms:
            return False
        rid = request_id if request_id in self.pending_confirms else next(iter(self.pending_confirms))
        fut = self.pending_confirms.get(rid)
        if fut and not fut.done():
            fut.set_result(allowed)
        return True

    async def announce(self, text: str) -> None:
        """Say something unprompted (timer fired). Queued while a turn is running; goes out as a tiny turn otherwise."""
        if self.turn_task and not self.turn_task.done():
            self._announce_queue.append(text)
            return
        await self._announce_now(text)

    async def _flush_announcements(self) -> None:
        while self._announce_queue and not (self.turn_task and not self.turn_task.done()):
            await self._announce_now(self._announce_queue.pop(0))

    async def _announce_now(self, text: str) -> None:
        self._announce_n += 1
        tid = f"announce{self._announce_n}"
        await self.send({"type": "reply.token", "turn_id": tid, "text": text})
        if self.tts:
            try:
                syn = await self.tts.synth(text, self.persona.voice, self.persona.voice_speed)
                await self.send({"type": "reply.audio", "turn_id": tid, "sentence_id": 1, "text": text, "sample_rate": syn.sample_rate,
                                 "pcm16_b64": syn.pcm16_b64(), "duration_s": round(syn.duration_s, 3), "envelope_hz": 50,
                                 "envelope": syn.envelope, "visemes": syn.visemes})
            except Exception:
                log.exception("announce tts failed")
        self.history.append({"role": "assistant", "content": text})
        await self.send({"type": "reply.done", "turn_id": tid, "text": text, "interrupted": False, "stats": {}})

    # ------------------------------------------------------------ memory
    async def _memory_loop(self) -> None:
        """Every few seconds: if the conversation has been idle long enough (or turns are pending from a previous run),
        summarize + extract facts. Never while a turn is running; the LLM request is cancelled if a turn starts."""
        await asyncio.sleep(5)
        while True:
            try:
                await asyncio.sleep(5)
                if not (self.settings.memory_enabled and self.llm and self.ready):
                    continue
                if self.turn_task and not self.turn_task.done():
                    continue
                pending = self.memory.pending_turns()
                idle = time.monotonic() - self._last_activity
                enough = self._turns_since_memory >= self.settings.memory_min_turns or (pending and self._turns_since_memory == 0)
                if pending and enough and idle >= self.settings.memory_idle_s:
                    await self.consolidate_memory(pending)
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("memory loop")

    async def consolidate_memory(self, pending: list[dict] | None = None) -> dict:
        pending = pending if pending is not None else self.memory.pending_turns()
        if not pending:
            return {"new_facts": []}
        res = await extract_and_summarize(self.llm, self.memory, pending)
        self._turns_since_memory = 0
        if res.get("new_facts") or res.get("episode_id"):
            await self.send(self.memory_items())
        return res

    def personas_items(self, detail_id: str | None) -> dict:
        prompt = expr = None
        if detail_id:
            prompt, expr = personas.detail(self.paths, detail_id)
        return {"type": "personas.items", "personas": personas.summaries(self.paths, self.persona.id if self.persona else None),
                "prompt": prompt, "expressions": expr}

    def settings_items(self) -> dict:
        from dataclasses import asdict

        return {"type": "settings.items", "settings": asdict(self.settings)}

    async def preview_voice(self, voice: str, speed: float, text: str | None) -> None:
        if not self.tts:
            await self.send({"type": "error", "code": "tts", "message": "voice engine not loaded", "fatal": False})
            return
        name = self.persona.name if self.persona else "Wisp"
        text = text or f"Hi, I'm {name}. This is what I sound like."
        try:
            syn = await self.tts.synth(text, voice, speed)
        except Exception as e:
            await self.send({"type": "error", "code": "tts", "message": f"preview failed: {e}", "fatal": False})
            return
        await self.send({"type": "reply.audio", "turn_id": f"preview-{voice}", "sentence_id": 1, "text": text, "sample_rate": syn.sample_rate,
                         "pcm16_b64": syn.pcm16_b64(), "duration_s": round(syn.duration_s, 3), "envelope_hz": 50,
                         "envelope": syn.envelope, "visemes": syn.visemes})

    def memory_items(self, query: str = "", limit: int = 100) -> dict:
        return {"type": "memory.items", "items": self.memory.list_items(query, limit), "counts": self.memory.counts(), "query": query}

    async def kill(self) -> None:
        """Kill switch: stop the current turn, deny every pending confirmation, cancel what we can."""
        log.warning("KILL SWITCH")
        self.kill_event.set()
        try:
            from .tools.input import release_all

            released = release_all()
            if released:
                log.warning("kill switch released %d held keys/buttons", released)
        except Exception:
            log.exception("release_all failed")
        for rid in list(self.pending_confirms):
            self.resolve_confirm(rid, False)
        await self.interrupt()
        self.kill_event = asyncio.Event()
        if self.tool_ctx:
            self.tool_ctx.kill = self.kill_event
        await self.send({"type": "tool.result", "request_id": "kill", "tool": "kill_switch", "ok": True, "summary": "stopped everything"})
        await self.set_state("idle")

    def models_info(self) -> dict:
        return {"type": "models.info", "tier": self.models.tier, "tier_reason": self.models.tier_reason,
                "hardware": self.models.hw.as_dict(), "required": self.models.status()}

    # ------------------------------------------------------------ boot
    async def boot(self) -> None:
        try:
            self.persona = load_persona(self.paths, self.settings.persona_id, self.settings.avatar_override)
        except Exception as e:
            log.exception("persona load failed")
            await self.send({"type": "error", "code": "persona", "message": str(e), "fatal": True})
            return
        await self.send(self.hello())
        await self.send(self.models_info())
        await self.send(self.tools_info())
        if self.models.missing():
            await self.set_state("downloading", "waiting for the shell to start the download")
            return  # shell sends models.download when the user confirms
        await self.start_engines()

    async def download_then_start(self) -> None:
        if self.download_task and not self.download_task.done():
            return

        async def _run():
            await self.set_state("downloading")

            async def progress(item, done, total, bps, odone, ototal):
                await self.send({"type": "model.download_progress", "item": item, "bytes_done": done, "bytes_total": total,
                                 "speed_bps": bps, "overall_done": odone, "overall_total": ototal})

            try:
                await self.models.download_missing(progress)
            except Exception as e:
                log.exception("download failed")
                await self.send({"type": "error", "code": "download", "message": str(e), "fatal": False})
                await self.set_state("error", str(e))
                return
            await self.send(self.models_info())
            await self.start_engines()

        self.download_task = asyncio.create_task(_run())

    async def start_engines(self) -> None:
        await self.set_state("booting", "starting the language model")
        exe = self.models.llama_server_exe()
        if not exe:
            await self.send({"type": "error", "code": "runtime", "message": "llama-server runtime not found", "fatal": True})
            await self.set_state("error", "runtime missing")
            return
        threads = self.settings.llm_threads or default_threads()
        self.llama = LlamaServer(exe, self.models.chat_model_path(), self.models.mmproj_path(), threads=threads,
                                 ctx=self.settings.llm_ctx, ngl=self.models.ngl(), log_dir=self.paths.logs)
        try:
            await self.llama.start()
        except Exception as e:
            log.exception("llama-server failed")
            await self.send({"type": "error", "code": "llm", "message": str(e), "fatal": True})
            await self.set_state("error", str(e))
            return
        self.llm = LlmClient(self.llama.base_url, thinking=self.settings.thinking)
        await self.set_state("booting", "loading the voice")
        try:
            model, voices = self.models.kokoro_paths()
            self.tts = KokoroTTS(model, voices)
            await self.tts.load()
            await self.tts.synth("Hello.", self.persona.voice, self.persona.voice_speed)  # first synth is slow; do it now
        except Exception as e:
            log.exception("kokoro failed")
            await self.send({"type": "error", "code": "tts", "message": f"voice unavailable: {e}", "fatal": False})
            self.tts = None
        await self.set_state("booting", "warming up")
        await self.warm()
        try:
            emb = await asyncio.to_thread(Embedder, self.models.embed_dir())
            self.memory = MemoryStore(self.paths.home / "memory.sqlite", emb)
            if self.tool_ctx:
                self.tool_ctx.extra["memory"] = self.memory
            log.info("memory: %s", self.memory.counts())
        except Exception as e:
            log.warning("embedder unavailable, memory falls back to keyword search: %s", e)
        self.memory_task = asyncio.create_task(self._memory_loop())
        self.ready = True
        await self.send({"type": "model.ready", "tier": self.models.tier, "chat_model": self.models.chat_model_path().name,
                         "tts_model": "kokoro-v1.0" if self.tts else "none"})
        await self.set_state("idle")
        if self.settings.voice_enabled:
            await self.start_voice()

    async def start_voice(self) -> None:
        """Mic -> VAD/wake word -> STT. Failures here are not fatal: text chat keeps working."""
        await self.stop_voice()
        quiet = self.state not in ("idle", "booting")  # a turn is running: do not stomp its state
        try:
            if not quiet:
                await self.set_state("booting", "loading speech recognition")
            loop = asyncio.get_running_loop()
            source = FileSource(loop, self.mic_file, realtime=True) if self.mic_file else MicSource(loop, self.settings.mic_device)
            vad = SileroVAD(self.models.vad_path(), threshold=self.settings.vad_threshold)
            kws = None
            if self.settings.wake_word:
                try:
                    kws = WakeWord(self.models.kws_dir(), [self.persona.wake_phrase], threshold=self.settings.kws_threshold)
                except Exception as e:
                    log.warning("wake word unavailable: %s", e)
                    await self.send({"type": "error", "code": "wakeword", "message": f"wake word unavailable: {e}", "fatal": False})
            stt = WhisperSTT(self.models.stt_dir(), threads=self.settings.stt_threads or default_stt_threads())
            await stt.load()
            cfg = PipelineConfig(wake_phrases=[self.persona.wake_phrase], wake_enabled=self.settings.wake_word,
                                 barge_in_vad=self.settings.barge_in_vad)
            self.voice = VoicePipeline(source=source, vad=vad, kws=kws, stt=stt, send=self.send,
                                       on_utterance=self._on_utterance, on_barge_in=self._on_barge_in, config=cfg)
            await self.voice.start()
            log.info("voice pipeline started on %s (wake=%s, barge_in_vad=%s)", source.describe(), bool(kws), cfg.barge_in_vad)
        except Exception as e:
            log.exception("voice pipeline failed")
            self.voice = None
            await self.send({"type": "error", "code": "voice", "message": f"microphone unavailable: {e}", "fatal": False})
        finally:
            if self.state == "booting" and not quiet:
                await self.set_state("idle")

    async def stop_voice(self) -> None:
        if self.voice:
            v, self.voice = self.voice, None
            await v.stop()

    YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|go for it|please do|confirm|affirmative)\b", re.I)
    NO = re.compile(r"^\W*(no|nope|nah|don't|do not|cancel|stop|never ?mind|negative)\b", re.I)

    async def _on_utterance(self, text: str, reason: str) -> None:
        if self.pending_confirms:
            if self.YES.match(text):
                await self.send({"type": "transcript.final", "text": text})
                self.resolve_confirm(None, True)
                return
            if self.NO.match(text):
                await self.send({"type": "transcript.final", "text": text})
                self.resolve_confirm(None, False)
                return
        await self.handle_text(text, speak=True)

    async def _on_barge_in(self) -> None:
        log.info("barge-in: interrupting current turn")
        await self.interrupt()
        await self.set_state("idle")

    async def warm(self) -> None:
        if not self.llm:
            return
        try:
            use_tools = bool(self.registry.available())
            await self.llm.warm([{"role": "system", "content": build_system_prompt(self.persona, tools=use_tools)},
                                 *(FEW_SHOT if use_tools else [])],
                                tools=self.registry.openai_tools() if use_tools else None)
        except Exception as e:
            log.warning("warm-up failed: %s", e)

    async def shutdown(self) -> None:
        if self.memory_task:
            self.memory_task.cancel()
        await self.stop_voice()
        await self.interrupt()
        if self.llm:
            await self.llm.aclose()
        if self.llama:
            self.llama.stop()

    # ------------------------------------------------------------ turns
    async def handle_text(self, text: str, speak: bool) -> None:
        if not self.ready or not self.llm:
            await self.send({"type": "error", "code": "not_ready", "message": "still starting up", "fatal": False})
            return
        await self.interrupt()
        memories = self.memory.context_for(text) if self.settings.memory_enabled else []
        turn = Turn(llm=self.llm, tts=self.tts, persona=self.persona, history=self.history, send=self.send, speak=speak,
                    registry=self.registry, ctx=self._ctx(), confirm=self.confirm, memories=memories)
        await self.send({"type": "transcript.final", "text": text})
        self._last_activity = time.monotonic()

        async def _run():
            try:
                reply = await turn.run(text)
                if self.settings.memory_enabled and not turn.interrupted:
                    self.memory.add_turn("user", text)
                    if reply or turn.tool_log:
                        self.memory.add_turn("assistant", reply or "(did: " + "; ".join(t["summary"] for t in turn.tool_log) + ")")
                    self._turns_since_memory += 1
                    self._last_activity = time.monotonic()
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.exception("turn failed")
                await self.send({"type": "error", "code": "turn", "message": str(e), "fatal": False})
            finally:
                if self.state != "error":
                    await self.set_state("idle")
                self.turn_task = None
                await self._flush_announcements()

        self.turn_task = asyncio.create_task(_run())

    async def interrupt(self) -> None:
        if self.turn_task and not self.turn_task.done():
            self.turn_task.cancel()
            try:
                await self.turn_task
            except (asyncio.CancelledError, Exception):
                pass
        self.turn_task = None

    async def switch_persona(self, persona_id: str) -> None:
        await self.interrupt()
        try:
            self.persona = load_persona(self.paths, persona_id, self.settings.avatar_override)
        except Exception as e:
            await self.send({"type": "error", "code": "persona", "message": str(e), "fatal": False})
            return
        self.settings.persona_id = persona_id
        self.settings.save(self.paths.settings_file)
        self.history.clear()
        await self.send(self.hello())
        await self.warm()

    # ------------------------------------------------------------ dispatch
    async def dispatch(self, raw: str) -> None:
        try:
            msg = parse_shell_message(raw)
        except Exception as e:
            await self.send({"type": "error", "code": "bad_message", "message": str(e)[:300], "fatal": False})
            return
        t = msg.type
        if t == "text.send":
            await self.handle_text(msg.text, msg.speak)
        elif t == "interrupt":
            await self.interrupt()
            await self.set_state("idle")
        elif t == "kill_switch":
            await self.kill()
        elif t == "tool.confirm":
            self.resolve_confirm(msg.request_id, True)
        elif t == "tool.deny":
            self.resolve_confirm(msg.request_id, False)
        elif t == "personas.list":
            await self.send(self.personas_items(msg.detail_id))
        elif t == "persona.import":
            try:
                pid = personas.import_pack(self.paths, msg.path)
                await self.send(self.personas_items(pid))
            except Exception as e:
                await self.send({"type": "error", "code": "persona", "message": f"import failed: {e}", "fatal": False})
        elif t == "persona.duplicate":
            try:
                pid = personas.duplicate(self.paths, msg.persona_id, msg.new_id, msg.new_name)
                await self.send(self.personas_items(pid))
            except Exception as e:
                await self.send({"type": "error", "code": "persona", "message": str(e), "fatal": False})
        elif t == "persona.update":
            try:
                personas.update(self.paths, msg.persona_id, prompt=msg.prompt, voice=msg.voice, wake_phrase=msg.wake_phrase, name=msg.name)
                if self.persona and msg.persona_id == self.persona.id:
                    await self.switch_persona(msg.persona_id)  # reload prompt/voice/wake phrase
                await self.send(self.personas_items(msg.persona_id))
            except Exception as e:
                await self.send({"type": "error", "code": "persona", "message": str(e), "fatal": False})
        elif t == "persona.delete":
            try:
                if self.persona and msg.persona_id == self.persona.id:
                    raise ValueError("switch to another persona first")
                personas.delete(self.paths, msg.persona_id)
                await self.send(self.personas_items(None))
            except Exception as e:
                await self.send({"type": "error", "code": "persona", "message": str(e), "fatal": False})
        elif t == "voices.list":
            await self.send({"type": "voices.items", "voices": self.tts.voices() if self.tts else [], "current": self.persona.voice if self.persona else ""})
        elif t == "voice.preview":
            await self.preview_voice(msg.voice, msg.speed, msg.text)
        elif t == "devices.list":
            from .audio import list_input_devices

            await self.send({"type": "devices.items", "devices": list_input_devices(), "current": self.settings.mic_device})
        elif t == "settings.get":
            await self.send(self.settings_items())
        elif t == "memory.list":
            await self.send(self.memory_items(msg.query, msg.limit))
        elif t == "memory.delete":
            if msg.all:
                self.memory.delete_all()
            elif msg.kind and msg.id is not None:
                self.memory.delete(msg.kind, msg.id)
            await self.send(self.memory_items())
        elif t == "models.download":
            await self.download_then_start()
        elif t == "persona.switch":
            await self.switch_persona(msg.persona_id)
        elif t == "settings.update":
            self.settings.update(msg.settings)
            self.settings.save(self.paths.settings_file)
            await self.send(self.settings_items())
            if "persona_id" in msg.settings and self.persona and msg.settings["persona_id"] != self.persona.id:
                await self.switch_persona(msg.settings["persona_id"])
            if any(k in msg.settings for k in ("tool_permissions", "web_enabled", "file_roots")):
                self.registry.configure(self.settings)
                await self.send(self.tools_info())
                await self.warm()
            voice_keys = ("voice_enabled", "mic_device", "wake_word", "barge_in_vad", "vad_threshold", "kws_threshold")
            if self.ready and any(k in msg.settings for k in voice_keys):
                if self.settings.voice_enabled:
                    await self.start_voice()
                else:
                    await self.stop_voice()
        elif t == "summon":
            await self.send({"type": "state", "state": self.state})
        elif t == "ptt.start":
            if self.voice:
                await self.voice.ptt_start()
            else:
                await self.send({"type": "error", "code": "voice", "message": "voice input is not running", "fatal": False})
        elif t == "ptt.stop":
            if self.voice:
                await self.voice.ptt_stop()
        elif t == "playback":
            if self.voice:
                self.voice.set_speaking(msg.speaking)
            if not msg.speaking and self.state == "speaking":
                await self.set_state("idle")
        else:  # tool.* arrive in later phases
            log.info("ignoring %s (not implemented in this phase)", t)


def create_app(brain: Brain, token: str) -> FastAPI:
    app = FastAPI(title="Familiar Brain", version=BRAIN_VERSION)

    @app.on_event("startup")
    async def _startup():
        brain.boot_task = asyncio.create_task(brain.boot())

    @app.on_event("shutdown")
    async def _shutdown():
        await brain.shutdown()

    @app.get("/health")
    async def health():
        return {"ok": True, "state": brain.state, "ready": brain.ready, "uptime_s": round(time.time() - START, 1)}

    @app.get("/personas")
    async def personas(t: str = Query("")):
        if t != token:
            return {"error": "unauthorized"}
        return list_personas(brain.paths)

    @app.websocket("/ws")
    async def ws(sock: WebSocket, t: str = Query("", alias="token")):
        if t != token:
            await sock.close(code=4401)
            return
        await sock.accept()
        brain.sockets.add(sock)
        try:
            await sock.send_text(json.dumps(brain.hello()))
            await sock.send_text(json.dumps(brain.models_info()))
            await sock.send_text(json.dumps(brain.tools_info()))
            await sock.send_text(json.dumps({"type": "state", "state": brain.state}))
            while True:
                raw = await sock.receive_text()
                await brain.dispatch(raw)
        except WebSocketDisconnect:
            pass
        finally:
            brain.sockets.discard(sock)

    return app


START = time.time()
