"""FastAPI + WebSocket server. One Brain per process; any number of shell sockets (usually one)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from familiar_protocol import parse_shell_message
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .agent.turn import Turn, build_system_prompt
from .audio import FileSource, MicSource
from .audio.pipeline import PipelineConfig, VoicePipeline
from .audio.stt import WhisperSTT, default_stt_threads
from .audio.vad import SileroVAD
from .audio.wakeword import WakeWord
from .config import BRAIN_VERSION, Paths, Settings
from .llm import LlamaServer, LlmClient, default_threads
from .models import ModelManager
from .persona import Persona, list_personas, load_persona
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
        try:
            await self.llm.warm([{"role": "system", "content": build_system_prompt(self.persona)}])
        except Exception as e:
            log.warning("warm-up failed: %s", e)
        self.ready = True
        await self.send({"type": "model.ready", "tier": self.models.tier, "chat_model": self.models.chat_model_path().name,
                         "tts_model": "kokoro-v1.0" if self.tts else "none"})
        await self.set_state("idle")
        if self.settings.voice_enabled:
            await self.start_voice()

    async def start_voice(self) -> None:
        """Mic -> VAD/wake word -> STT. Failures here are not fatal: text chat keeps working."""
        await self.stop_voice()
        try:
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
            if self.state == "booting":
                await self.set_state("idle")

    async def stop_voice(self) -> None:
        if self.voice:
            v, self.voice = self.voice, None
            await v.stop()

    async def _on_utterance(self, text: str, reason: str) -> None:
        await self.handle_text(text, speak=True)

    async def _on_barge_in(self) -> None:
        log.info("barge-in: interrupting current turn")
        await self.interrupt()
        await self.set_state("idle")

    async def shutdown(self) -> None:
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
        turn = Turn(llm=self.llm, tts=self.tts, persona=self.persona, history=self.history, send=self.send, speak=speak)
        await self.send({"type": "transcript.final", "text": text})

        async def _run():
            try:
                await turn.run(text)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.exception("turn failed")
                await self.send({"type": "error", "code": "turn", "message": str(e), "fatal": False})
            finally:
                if self.state != "error":
                    await self.set_state("idle")

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
        if self.llm:
            try:
                await self.llm.warm([{"role": "system", "content": build_system_prompt(self.persona)}])
            except Exception as e:
                log.warning("warm-up failed: %s", e)

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
        elif t == "interrupt" or t == "kill_switch":
            await self.interrupt()
            await self.set_state("idle")
        elif t == "models.download":
            await self.download_then_start()
        elif t == "persona.switch":
            await self.switch_persona(msg.persona_id)
        elif t == "settings.update":
            self.settings.update(msg.settings)
            self.settings.save(self.paths.settings_file)
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
