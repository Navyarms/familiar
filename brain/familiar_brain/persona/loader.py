"""Persona packs. A pack is a folder with persona.json, prompt.md, expressions.json and optionally avatar.vrm.

Search order: user packs (%LOCALAPPDATA%\\Familiar\\personas\\<id>) then bundled packs (<bundle>/personas/<id>).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Paths, bundle_root

log = logging.getLogger(__name__)

DEFAULT_EXPRESSIONS = {
    "neutral": {"vrm": "neutral", "weight": 1.0, "decay_s": 2.0},
    "happy": {"vrm": "happy", "weight": 1.0, "decay_s": 3.0},
    "sad": {"vrm": "sad", "weight": 1.0, "decay_s": 3.0},
    "angry": {"vrm": "angry", "weight": 0.8, "decay_s": 2.5},
    "surprised": {"vrm": "surprised", "weight": 1.0, "decay_s": 2.0},
    "relaxed": {"vrm": "relaxed", "weight": 1.0, "decay_s": 3.0},
    "thinking": {"vrm": "relaxed", "weight": 0.5, "decay_s": 4.0},
    "smug": {"vrm": "happy", "weight": 0.6, "decay_s": 3.0},
    "wink": {"vrm": "blinkLeft", "weight": 1.0, "decay_s": 0.4},
}


@dataclass
class Persona:
    id: str
    name: str
    root: Path
    wake_phrase: str
    system_prompt: str
    voice: str
    voice_speed: float
    pitch_shift: float
    expressions: dict[str, dict]
    avatar_path: Path | None
    raw: dict = field(default_factory=dict)

    def info(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "wake_phrase": self.wake_phrase,
            "voice": self.voice,
            "avatar_path": str(self.avatar_path) if self.avatar_path else None,
            "expressions": self.expressions,
        }

    def expression_tags(self) -> list[str]:
        return list(self.expressions.keys())


def _candidates(paths: Paths, persona_id: str) -> list[Path]:
    return [paths.personas / persona_id, bundle_root() / "personas" / persona_id]


def list_personas(paths: Paths) -> list[dict]:
    seen: dict[str, dict] = {}
    for base in (paths.personas, bundle_root() / "personas"):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            pj = d / "persona.json"
            if d.name not in seen and pj.exists():
                try:
                    j = json.loads(pj.read_text(encoding="utf-8"))
                    seen[d.name] = {"id": j.get("id", d.name), "name": j.get("name", d.name), "path": str(d)}
                except Exception as e:
                    log.warning("bad persona.json in %s: %s", d, e)
    return list(seen.values())


def load_persona(paths: Paths, persona_id: str, avatar_override: str | None = None) -> Persona:
    root = next((c for c in _candidates(paths, persona_id) if (c / "persona.json").exists()), None)
    if root is None:
        raise FileNotFoundError(f"persona '{persona_id}' not found in {[str(c) for c in _candidates(paths, persona_id)]}")
    j = json.loads((root / "persona.json").read_text(encoding="utf-8"))
    prompt_file = root / j.get("prompt", "prompt.md")
    system_prompt = prompt_file.read_text(encoding="utf-8") if prompt_file.exists() else f"You are {j.get('name', persona_id)}."
    expr_file = root / j.get("expressions", "expressions.json")
    expressions = dict(DEFAULT_EXPRESSIONS)
    if expr_file.exists():
        try:
            user_expr = json.loads(expr_file.read_text(encoding="utf-8"))
            for tag, spec in user_expr.items():
                if isinstance(spec, str):
                    spec = {"vrm": spec}
                expressions[tag] = {**DEFAULT_EXPRESSIONS.get(tag, {"weight": 1.0, "decay_s": 2.5}), **spec}
        except Exception as e:
            log.warning("bad expressions.json in %s: %s", root, e)
    voice = j.get("voice") or {}
    if isinstance(voice, str):
        voice = {"voice": voice}
    avatar = None
    if avatar_override and Path(avatar_override).exists():
        avatar = Path(avatar_override)
    else:
        cand = root / j.get("avatar", "avatar.vrm")
        if cand.exists():
            avatar = cand
    return Persona(
        id=j.get("id", persona_id),
        name=j.get("name", persona_id),
        root=root,
        wake_phrase=j.get("wake_phrase", f"hey {j.get('name', persona_id)}").lower(),
        system_prompt=system_prompt,
        voice=voice.get("voice", "af_heart"),
        voice_speed=float(voice.get("speed", 1.0)),
        pitch_shift=float(voice.get("pitch_shift", 0)),
        expressions=expressions,
        avatar_path=avatar,
        raw=j,
    )
