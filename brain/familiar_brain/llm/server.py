"""Supervises a bundled llama-server process (CPU or Vulkan build) on a random localhost port."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


def _tie_to_this_process(proc: subprocess.Popen):
    """Windows: put the child in a job object that kills it when the last handle closes, i.e. when this process
    exits for any reason (TerminateProcess from the shell, a crash, a test harness). Returns the job handle to keep
    alive; None elsewhere or on failure."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                           "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC_LIMIT(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # JobObjectExtendedLimitInformation
            k32.CloseHandle(job)
            return None
        if not k32.AssignProcessToJobObject(job, wintypes.HANDLE(proc._handle)):  # type: ignore[attr-defined]
            k32.CloseHandle(job)
            return None
        return job
    except Exception as e:  # noqa: BLE001 - best effort; the normal stop() path still works
        log.debug("job object not set up: %s", e)
        return None


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LlamaServer:
    def __init__(self, exe: Path, model: Path, mmproj: Path | None, *, threads: int, ctx: int, ngl: int, log_dir: Path):
        self.exe, self.model, self.mmproj = exe, model, mmproj
        self.threads, self.ctx, self.ngl = threads, ctx, ngl
        self.log_dir = log_dir
        self.port = free_port()
        self.proc: subprocess.Popen | None = None
        self._log_fh = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    _job = None

    async def start(self, timeout_s: float = 300) -> None:
        self.port = free_port()
        args = [str(self.exe), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
                "-t", str(self.threads), "-c", str(self.ctx), "-ngl", str(self.ngl), "--jinja", "-fa", "on",
                "--parallel", "1", "--no-webui", "--log-timestamps"]
        if self.mmproj and self.mmproj.exists():
            args += ["--mmproj", str(self.mmproj)]
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_dir / "llama-server.log", "ab")
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = dict(os.environ)
        env.setdefault("OMP_NUM_THREADS", str(self.threads))
        log.info("starting llama-server: %s", " ".join(args))
        self.proc = subprocess.Popen(args, stdout=self._log_fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     creationflags=flags, env=env, cwd=str(self.exe.parent))
        self._job = _tie_to_this_process(self.proc)  # llama-server dies with the brain, even on a hard kill
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=2) as client:
            while time.monotonic() - t0 < timeout_s:
                if not self.alive():
                    raise RuntimeError(f"llama-server exited early with code {self.proc.returncode}; see {self.log_dir / 'llama-server.log'}")
                try:
                    r = await client.get(f"{self.base_url}/health")
                    if r.status_code == 200 and r.json().get("status") == "ok":
                        log.info("llama-server ready on %s after %.1fs", self.base_url, time.monotonic() - t0)
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        self.stop()
        raise TimeoutError("llama-server did not become healthy in time")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        self.proc = None
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:
                pass
            self._log_fh = None

    def __del__(self):  # belt and braces: never leave a llama-server behind
        try:
            self.stop()
        except Exception:
            pass


def default_threads() -> int:
    import psutil

    phys = psutil.cpu_count(logical=False) or 4
    # leave a couple of cores for TTS/STT and the shell; cap at 16 (diminishing returns, see Phase 0)
    return max(2, min(16, phys - 2 if phys > 6 else phys))


if sys.platform != "win32":  # pragma: no cover
    log.warning("LlamaServer is written for Windows; other platforms are Phase 7")
