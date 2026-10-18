"""Model manager: decide the tier, report what is missing, download with resume + SHA256, unpack runtimes."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import tarfile
import time
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from ..config import Paths, Settings, bundle_root
from .hardware import Hardware, choose_tier, probe
from .manifest import TIER_MODELS, Item, items_for_tier

log = logging.getLogger(__name__)

ProgressCb = Callable[[str, int, int, float, int, int], Awaitable[None]]  # item, done, total, bps, overall_done, overall_total


class ModelManager:
    def __init__(self, paths: Paths, settings: Settings):
        self.paths = paths
        self.settings = settings
        self.hw: Hardware = probe()
        auto_tier, reason = choose_tier(self.hw)
        if settings.tier_override in TIER_MODELS:
            self.tier, self.tier_reason = settings.tier_override, f"user override (auto would be {auto_tier}: {reason})"
        else:
            self.tier, self.tier_reason = auto_tier, reason
        log.info("hardware: %s -> tier %s (%s)", self.hw, self.tier, self.tier_reason)

    # ---------------------------------------------------------------- locations
    def _dest(self, item: Item) -> Path:
        base = self.paths.runtimes if item.kind == "runtime" else self.paths.models
        return base / item.subdir / item.name if item.subdir else base / item.name

    def _unpacked_dir(self, item: Item) -> Path:
        """Where an archive item's contents live after unpacking."""
        if item.kind == "runtime":
            return self.paths.runtimes / item.name.removesuffix(".zip")
        return self.paths.models / (item.subdir or item.name.split(".")[0])

    def runtime_dir(self) -> Path:
        return self.paths.runtimes / TIER_MODELS[self.tier]["runtime"].name.removesuffix(".zip")

    def bundled_runtime_dir(self) -> Path | None:
        """Installer-bundled llama-server (runtimes/<cpu|vulkan>/), if present."""
        flavor = "vulkan" if "vulkan" in TIER_MODELS[self.tier]["runtime"].name else "cpu"
        d = bundle_root() / "runtimes" / flavor
        return d if (d / "llama-server.exe").exists() else None

    def llama_server_exe(self) -> Path | None:
        b = self.bundled_runtime_dir()
        if b:
            return b / "llama-server.exe"
        exe = self.runtime_dir() / "llama-server.exe"
        return exe if exe.exists() else None

    def chat_model_path(self) -> Path:
        if self.settings.custom_chat_gguf:
            return Path(self.settings.custom_chat_gguf)
        return self._dest(TIER_MODELS[self.tier]["chat"])

    def mmproj_path(self) -> Path | None:
        if self.settings.custom_chat_gguf:
            return Path(self.settings.custom_mmproj_gguf) if self.settings.custom_mmproj_gguf else None
        return self._dest(TIER_MODELS[self.tier]["mmproj"])

    def ngl(self) -> int:
        return TIER_MODELS[self.tier]["ngl"]

    def kokoro_paths(self) -> tuple[Path, Path]:
        from .manifest import KOKORO, KOKORO_VOICES

        return self._dest(KOKORO), self._dest(KOKORO_VOICES)

    def vad_path(self) -> Path:
        from .manifest import SILERO_VAD

        return self._dest(SILERO_VAD)

    def kws_dir(self) -> Path:
        from .manifest import KWS_EN

        return self._unpacked_dir(KWS_EN)

    def stt_dir(self) -> Path:
        return self._dest(TIER_MODELS[self.tier]["stt"][0]).parent

    def embed_dir(self) -> Path:
        from .manifest import EMBED

        return self._dest(EMBED[0]).parent

    # ---------------------------------------------------------------- status
    def required(self) -> list[Item]:
        items = items_for_tier(self.tier)
        if self.settings.custom_chat_gguf:
            items = [i for i in items if i.kind not in ("chat", "mmproj")]
        if self.bundled_runtime_dir():
            items = [i for i in items if i.kind != "runtime"]
        return items

    def is_present(self, item: Item) -> bool:
        if item.kind == "runtime":
            return (self.runtime_dir() / "llama-server.exe").exists()
        if item.archive:
            return (self._unpacked_dir(item) / "tokens.txt").exists()
        p = self._dest(item)
        return p.exists() and (item.sha256 is None or p.stat().st_size == item.size)

    def missing(self) -> list[Item]:
        return [i for i in self.required() if not self.is_present(i)]

    def status(self) -> list[dict]:
        return [{"name": i.name, "bytes_total": i.size, "present": self.is_present(i)} for i in self.required()]

    # ---------------------------------------------------------------- download
    async def download_missing(self, progress: ProgressCb) -> None:
        missing = self.missing()
        overall_total = sum(i.size for i in missing)
        overall_done = 0
        async with httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(30, read=120)) as client:
            for item in missing:
                done = await self._download(client, item, progress, overall_done, overall_total)
                overall_done += done
                if item.archive:
                    await asyncio.to_thread(self._unpack, item)

    async def _download(self, client: httpx.AsyncClient, item: Item, progress: ProgressCb, overall_done: int, overall_total: int) -> int:
        dest = self._dest(item)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_suffix(dest.suffix + ".part")
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        t0 = time.monotonic()
        last_emit = 0.0
        total = item.size
        for attempt in range(5):
            try:
                async with client.stream("GET", item.url, headers=headers) as r:
                    if r.status_code == 416:  # already complete
                        break
                    r.raise_for_status()
                    if r.status_code == 200 and have:  # server ignored Range
                        have = 0
                    cl = r.headers.get("content-length")
                    if cl:
                        total = have + int(cl)
                    mode = "ab" if have else "wb"
                    with open(part, mode) as f:
                        async for chunk in r.aiter_bytes(1 << 20):
                            f.write(chunk)
                            have += len(chunk)
                            now = time.monotonic()
                            if now - last_emit > 0.25:
                                last_emit = now
                                bps = have / max(now - t0, 1e-3)
                                await progress(item.name, have, total, bps, overall_done + have, overall_total)
                break
            except (httpx.HTTPError, OSError) as e:
                log.warning("download %s attempt %d failed: %s", item.name, attempt, e)
                await asyncio.sleep(2 * (attempt + 1))
                headers = {"Range": f"bytes={have}-"}
        else:
            raise RuntimeError(f"download failed: {item.name}")
        if item.sha256:
            digest = await asyncio.to_thread(_sha256, part)
            if digest != item.sha256:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"SHA256 mismatch for {item.name}: got {digest[:12]}..., expected {item.sha256[:12]}...")
        part.replace(dest)
        await progress(item.name, total, total, 0, overall_done + total, overall_total)
        return total

    def _unpack(self, item: Item) -> None:
        apath = self._dest(item)
        out = self._unpacked_dir(item)
        out.mkdir(parents=True, exist_ok=True)
        if apath.name.endswith(".zip"):
            with zipfile.ZipFile(apath) as z:
                z.extractall(out)
        else:
            with tarfile.open(apath, "r:bz2") as t:
                t.extractall(out, filter="data")
        # archives often nest everything one folder deep: flatten
        marker = "llama-server.exe" if item.kind == "runtime" else "tokens.txt"
        if not (out / marker).exists():
            for sub in out.iterdir():
                if sub.is_dir() and (sub / marker).exists():
                    for f in sub.iterdir():
                        f.replace(out / f.name)
                    sub.rmdir()
                    break
        apath.unlink(missing_ok=True)
        log.info("unpacked %s to %s", item.name, out)

    def adopt_local_file(self, item_name: str, src: Path) -> bool:
        """Dev helper: copy a verified local file into the store instead of downloading (used by tests/dev)."""
        for item in items_for_tier(self.tier):
            if item.name == item_name and src.exists():
                if item.sha256 and _sha256(src) != item.sha256:
                    return False
                dest = self._dest(item)
                dest.parent.mkdir(parents=True, exist_ok=True)
                import shutil

                shutil.copy2(src, dest)
                return True
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()
