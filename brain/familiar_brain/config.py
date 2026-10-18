"""Paths and user settings. Everything lives under %LOCALAPPDATA%\\Familiar (override with FAMILIAR_HOME)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

APP_NAME = "Familiar"
BRAIN_VERSION = "0.1.0"


def repo_root() -> Path | None:
    """Repo root when running from source (brain/ sits directly under it), else None."""
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "personas").is_dir() and (p / "brain").is_dir():
            return p
    return None


def bundle_root() -> Path:
    """Where bundled resources live: next to the frozen exe, or the repo root in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return repo_root() or Path.cwd()


@dataclass
class Paths:
    home: Path

    @property
    def models(self) -> Path:
        return self.home / "models"

    @property
    def runtimes(self) -> Path:
        return self.home / "runtimes"

    @property
    def personas(self) -> Path:
        return self.home / "personas"

    @property
    def logs(self) -> Path:
        return self.home / "logs"

    @property
    def settings_file(self) -> Path:
        return self.home / "settings.json"

    def ensure(self) -> Paths:
        for p in (self.home, self.models, self.runtimes, self.personas, self.logs):
            p.mkdir(parents=True, exist_ok=True)
        return self

    @staticmethod
    def default() -> Paths:
        env = os.environ.get("FAMILIAR_HOME")
        if env:
            return Paths(Path(env))
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Paths(Path(base) / APP_NAME)


@dataclass
class Settings:
    persona_id: str = "wisp"
    tier_override: str | None = None  # "T0".."T4" or None = auto
    custom_chat_gguf: str | None = None
    custom_mmproj_gguf: str | None = None
    avatar_override: str | None = None  # absolute path to a VRM, dev convenience
    llm_threads: int | None = None
    llm_ctx: int = 4096
    thinking: bool = False
    tts_speed: float | None = None
    # voice in
    voice_enabled: bool = True
    mic_device: int | str | None = None  # sounddevice index or name substring; None = default
    wake_word: bool = True
    barge_in_vad: bool = False  # interrupt on any speech while speaking (headset users)
    vad_threshold: float = 0.5
    kws_threshold: float = 0.25
    stt_threads: int | None = None  # None = min(8, physical cores / 2)
    extra: dict = field(default_factory=dict)

    @staticmethod
    def load(path: Path) -> Settings:
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                known = {k: v for k, v in data.items() if k in Settings.__dataclass_fields__}
                return Settings(**known)
            except Exception:
                pass
        return Settings()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def update(self, patch: dict) -> None:
        for k, v in patch.items():
            if k in Settings.__dataclass_fields__:
                setattr(self, k, v)
            else:
                self.extra[k] = v
