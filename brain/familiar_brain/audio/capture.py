"""Audio sources: the microphone (sounddevice) or a wav file (tests, demos). Both deliver 16 kHz mono
float32 frames of FRAME samples into an asyncio queue from a background thread."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import wave
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SR = 16000
FRAME = 512  # 32 ms; Silero's native chunk at 16 kHz


class AudioSource:
    """Base: frames arrive on `queue`; `start()`/`stop()`; `level` is the latest RMS (0..1)."""

    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.queue: asyncio.Queue[np.ndarray | None] = asyncio.Queue(maxsize=200)
        self.level = 0.0
        self.running = False

    def _push(self, frame: np.ndarray) -> None:
        self.level = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, frame)
        except (asyncio.QueueFull, RuntimeError):
            pass  # consumer is behind or loop is closing; dropping audio beats blocking the device thread

    def start(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        return type(self).__name__


class MicSource(AudioSource):
    def __init__(self, loop: asyncio.AbstractEventLoop, device: int | str | None = None):
        super().__init__(loop)
        self.device = device
        self._stream = None
        self._resample_from: int | None = None
        self._carry = np.zeros(0, np.float32)

    def describe(self) -> str:
        import sounddevice as sd

        try:
            d = sd.query_devices(self.device if self.device is not None else sd.default.device[0], "input")
            return f"{d['name']} @ {int(d['default_samplerate'])} Hz"
        except Exception:
            return "default microphone"

    @staticmethod
    def resolve(device: int | str | None) -> int | None:
        """Accept an index or a case-insensitive name substring (first input device that matches)."""
        if device is None or isinstance(device, int):
            return device
        import sounddevice as sd

        matches = [(i, d) for i, d in enumerate(sd.query_devices())
                   if d["max_input_channels"] > 0 and str(device).lower() in d["name"].lower()]
        if not matches:
            raise RuntimeError(f"no input device matching {device!r}")
        # the same physical device appears once per host API; WASAPI is the least surprising on Windows 10/11
        for i, d in matches:
            if sd.query_hostapis(d["hostapi"])["name"].startswith("Windows WASAPI"):
                return i
        return matches[0][0]

    def start(self) -> None:
        import sounddevice as sd

        dev = self.device = self.resolve(self.device)
        try:
            info = sd.query_devices(dev if dev is not None else sd.default.device[0], "input")
        except Exception as e:
            raise RuntimeError(f"no microphone available: {e}") from e
        native = int(info["default_samplerate"])
        is_wasapi = sd.query_hostapis(info["hostapi"])["name"].startswith("Windows WASAPI")
        # Ask for 16 kHz directly (WASAPI needs auto_convert for that); fall back to native rate + our own resampler.
        for sr in (SR, native):
            try:
                extra = sd.WasapiSettings(auto_convert=True) if (is_wasapi and sr == SR) else None
                self._stream = sd.InputStream(samplerate=sr, channels=1, dtype="float32", blocksize=FRAME if sr == SR else 0,
                                              device=dev, callback=self._cb, latency="low", extra_settings=extra)
                self._stream.start()
                self._resample_from = None if sr == SR else sr
                break
            except Exception as e:
                log.warning("mic open at %d Hz failed: %s", sr, e)
                self._stream = None
        if not self._stream:
            raise RuntimeError("could not open the microphone")
        self.running = True
        log.info("mic started: %s (%s)", self.describe(), "native 16 kHz" if not self._resample_from else f"resampling from {self._resample_from}")

    def _cb(self, indata, frames, t, status):  # sounddevice thread
        if status:
            log.debug("mic status: %s", status)
        x = indata[:, 0]
        if self._resample_from:
            n = int(len(x) * SR / self._resample_from)
            x = np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)
            x = np.concatenate([self._carry, x])
            while len(x) >= FRAME:
                self._push(x[:FRAME].copy())
                x = x[FRAME:]
            self._carry = x
        else:
            self._push(x.copy())

    def stop(self) -> None:
        self.running = False
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None


class FileSource(AudioSource):
    """Plays a wav (any rate, mono/stereo) as if it were the mic. `realtime=False` feeds as fast as possible.
    `then_silence_s` appends silence so end-of-speech triggers."""

    def __init__(self, loop: asyncio.AbstractEventLoop, path: str | Path, *, realtime: bool = True, then_silence_s: float = 2.0, loop_file: bool = False):
        super().__init__(loop)
        self.path = Path(path)
        self.realtime = realtime
        self.then_silence_s = then_silence_s
        self.loop_file = loop_file
        self._thread: threading.Thread | None = None
        self.samples = self._load()

    def describe(self) -> str:
        return f"file:{self.path.name} ({len(self.samples)/SR:.1f}s)"

    def _load(self) -> np.ndarray:
        with wave.open(str(self.path), "rb") as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
        dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[sw]
        x = np.frombuffer(raw, dtype=dtype).astype(np.float32)
        x = (x - 128) / 128 if sw == 1 else x / float(2 ** (8 * sw - 1))
        if ch > 1:
            x = x.reshape(-1, ch).mean(axis=1)
        if sr != SR:
            x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * SR / sr)), np.arange(len(x)), x).astype(np.float32)
        return x.astype(np.float32)

    def start(self) -> None:
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        sil = np.zeros(int(self.then_silence_s * SR), np.float32)
        t0 = time.monotonic()
        sent = 0
        while self.running:
            data = np.concatenate([self.samples, sil])
            for i in range(0, len(data) - FRAME + 1, FRAME):
                if not self.running:
                    return
                self._push(data[i:i + FRAME].copy())
                sent += FRAME
                if self.realtime:
                    wait = t0 + sent / SR - time.monotonic()
                    if wait > 0:
                        time.sleep(wait)
            if not self.loop_file:
                break
        self.running = False
        try:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, None)  # end-of-stream marker
        except RuntimeError:
            pass

    def stop(self) -> None:
        self.running = False


def list_input_devices() -> list[dict]:
    import sounddevice as sd

    out = []
    try:
        default_in = sd.default.device[0]
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                out.append({"index": i, "name": d["name"], "default": i == default_in, "hostapi": sd.query_hostapis(d["hostapi"])["name"]})
    except Exception as e:
        log.warning("device query failed: %s", e)
    return out
