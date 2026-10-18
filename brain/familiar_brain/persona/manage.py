"""Persona pack management: list with details, import (zip/folder), duplicate into the user folder, edit, delete."""

from __future__ import annotations

import json
import re
import shutil
import zipfile
from pathlib import Path

from ..config import Paths, bundle_root

SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _slug(s: str) -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", s.lower()).strip("-")
    return s[:40] or "persona"


def summaries(paths: Paths, active_id: str | None) -> list[dict]:
    out: dict[str, dict] = {}
    for base, user in ((paths.personas, True), (bundle_root() / "personas", False)):
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir()):
            pj = d / "persona.json"
            if not pj.exists() or d.name in out:
                continue
            try:
                j = json.loads(pj.read_text(encoding="utf-8"))
            except Exception:
                continue
            voice = j.get("voice") or {}
            if isinstance(voice, str):
                voice = {"voice": voice}
            out[d.name] = {"id": j.get("id", d.name), "name": j.get("name", d.name), "path": str(d),
                           "wake_phrase": j.get("wake_phrase", f"hey {j.get('name', d.name)}").lower(), "voice": voice.get("voice", "af_heart"),
                           "has_avatar": (d / j.get("avatar", "avatar.vrm")).exists(), "user_pack": user, "active": j.get("id", d.name) == active_id}
    return list(out.values())


def find_dir(paths: Paths, persona_id: str) -> Path | None:
    for base in (paths.personas, bundle_root() / "personas"):
        d = base / persona_id
        if (d / "persona.json").exists():
            return d
    return None


def detail(paths: Paths, persona_id: str) -> tuple[str | None, dict | None]:
    d = find_dir(paths, persona_id)
    if not d:
        return None, None
    j = json.loads((d / "persona.json").read_text(encoding="utf-8"))
    pf = d / j.get("prompt", "prompt.md")
    ef = d / j.get("expressions", "expressions.json")
    prompt = pf.read_text(encoding="utf-8") if pf.exists() else ""
    try:
        expr = json.loads(ef.read_text(encoding="utf-8")) if ef.exists() else {}
    except Exception:
        expr = {}
    return prompt, expr


def import_pack(paths: Paths, src: str) -> str:
    """Zip or folder -> %LOCALAPPDATA%\\Familiar\\personas\\<id>. Returns the persona id."""
    p = Path(src)
    if not p.exists():
        raise FileNotFoundError(src)
    paths.personas.mkdir(parents=True, exist_ok=True)
    if p.is_file() and p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            roots = {n.split("/")[0] for n in names if "/" in n}
            pj = next((n for n in names if n.endswith("persona.json")), None)
            if not pj:
                raise ValueError("the zip has no persona.json")
            prefix = pj[: -len("persona.json")]
            j = json.loads(z.read(pj).decode("utf-8"))
            pid = _slug(j.get("id") or j.get("name") or p.stem)
            dest = paths.personas / pid
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            for n in names:
                if not n.startswith(prefix) or n.endswith("/"):
                    continue
                rel = Path(n[len(prefix):])
                if ".." in rel.parts:
                    continue
                target = dest / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as fsrc, open(target, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
            del roots
            return pid
    if p.is_dir() and (p / "persona.json").exists():
        j = json.loads((p / "persona.json").read_text(encoding="utf-8"))
        pid = _slug(j.get("id") or j.get("name") or p.name)
        dest = paths.personas / pid
        if dest.resolve() == p.resolve():
            return pid
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(p, dest)
        return pid
    raise ValueError("expected a .zip or a folder containing persona.json")


def duplicate(paths: Paths, persona_id: str, new_id: str, new_name: str) -> str:
    src = find_dir(paths, persona_id)
    if not src:
        raise FileNotFoundError(persona_id)
    nid = _slug(new_id or new_name)
    if not SAFE_ID.match(nid):
        raise ValueError("bad id")
    dest = paths.personas / nid
    if dest.exists():
        raise FileExistsError(f"a persona called {nid} already exists")
    shutil.copytree(src, dest)
    pj = dest / "persona.json"
    j = json.loads(pj.read_text(encoding="utf-8"))
    j["id"], j["name"] = nid, new_name or j.get("name", nid)
    j["wake_phrase"] = f"hey {j['name']}".lower()
    pj.write_text(json.dumps(j, indent=2), encoding="utf-8")
    return nid


def update(paths: Paths, persona_id: str, *, prompt: str | None = None, voice: dict | None = None,
           wake_phrase: str | None = None, name: str | None = None) -> None:
    d = paths.personas / persona_id
    if not (d / "persona.json").exists():
        raise PermissionError("only packs in your personas folder can be edited; duplicate it first")
    pj = d / "persona.json"
    j = json.loads(pj.read_text(encoding="utf-8"))
    if prompt is not None:
        (d / j.get("prompt", "prompt.md")).write_text(prompt, encoding="utf-8")
    if voice is not None:
        cur = j.get("voice") if isinstance(j.get("voice"), dict) else {"voice": j.get("voice", "af_heart")}
        cur.update({k: v for k, v in voice.items() if k in ("engine", "voice", "speed", "pitch_shift")})
        j["voice"] = cur
    if wake_phrase:
        j["wake_phrase"] = wake_phrase.strip().lower()
    if name:
        j["name"] = name.strip()
    pj.write_text(json.dumps(j, indent=2), encoding="utf-8")


def delete(paths: Paths, persona_id: str) -> None:
    d = paths.personas / persona_id
    if not (d / "persona.json").exists():
        raise PermissionError("only packs in your personas folder can be deleted")
    shutil.rmtree(d)
