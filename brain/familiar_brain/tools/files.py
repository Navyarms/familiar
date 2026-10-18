"""File tools, confined to user-approved roots (the user's profile folders by default + settings.file_roots)."""

from __future__ import annotations

import datetime as dt
import os
import shutil
import time
from pathlib import Path

from .registry import P, ToolContext, tool

KNOWN = {"downloads": "Downloads", "documents": "Documents", "desktop": "Desktop", "pictures": "Pictures",
         "videos": "Videos", "music": "Music"}
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "AppData", "$RECYCLE.BIN", ".venv", "venv", ".cache"}
TEXT_EXT = {".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts", ".html", ".xml", ".yaml", ".yml", ".ini", ".toml", ".cfg"}


def roots(ctx: ToolContext) -> list[Path]:
    home = Path.home()
    out = [home / v for v in KNOWN.values()]
    for r in getattr(ctx.settings, "file_roots", None) or []:
        out.append(Path(r))
    return [p for p in out if p.exists()]


def allowed(ctx: ToolContext, path: Path) -> bool:
    try:
        rp = path.resolve()
    except Exception:
        return False
    return any(rp == r.resolve() or r.resolve() in rp.parents for r in roots(ctx))


def _within(ctx: ToolContext, where: list[str] | None) -> list[Path]:
    if not where:
        return roots(ctx)
    out = []
    for w in where:
        key = w.strip().lower()
        if key in KNOWN:
            out.append(Path.home() / KNOWN[key])
        else:
            p = Path(w)
            if p.is_absolute() and allowed(ctx, p):
                out.append(p)
    return out or roots(ctx)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@tool("search_files", group="files",
      description="Find files in the user's folders (Downloads, Documents, Desktop, Pictures, Videos, Music) by name words, type and age.",
      parameters=P(query=("string", "Words from the file name; empty string = any file", False),
                   ext=("string", "Extension without the dot, e.g. 'pdf', 'xlsx', 'png'", False),
                   days=("integer", "Only files modified within this many days", False),
                   where=("array", "Folders to search: downloads, documents, desktop, pictures, videos, music (default: all)", False),
                   limit=("integer", "Max results, default 10", False)),
      timeout_s=20)
def search_files(ctx: ToolContext, query: str = "", ext: str | None = None, days: int | None = None, where: list[str] | None = None, limit: int = 10):
    q = [t for t in (query or "").lower().replace("_", " ").split() if t not in ("file", "files", "the", "a", "my")]
    ext = ("." + ext.lower().lstrip(".")) if ext else None
    cutoff = time.time() - days * 86400 if days else None
    hits: list[tuple[float, Path, int]] = []
    deadline = time.monotonic() + 8
    for root in _within(ctx, where):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            if dirpath.count(os.sep) - str(root).count(os.sep) > 4:
                dirnames[:] = []
            for fn in filenames:
                low = fn.lower()
                if ext and not low.endswith(ext):
                    continue
                if q and not all(t in low for t in q):
                    continue
                p = Path(dirpath) / fn
                try:
                    st = p.stat()
                except OSError:
                    continue
                if cutoff and st.st_mtime < cutoff:
                    continue
                hits.append((st.st_mtime, p, st.st_size))
            if time.monotonic() > deadline:
                break
    hits.sort(reverse=True)
    res = [{"path": str(p), "name": p.name, "modified": dt.datetime.fromtimestamp(m).strftime("%Y-%m-%d %H:%M"),
            "size": _fmt_size(s)} for m, p, s in hits[: max(1, min(50, limit))]]
    if not res:
        return [], "no matching files"
    names = ", ".join(f"{r['name']} ({r['modified'][:10]})" for r in res[:5])
    return res, f"{len(hits)} match{'es' if len(hits) != 1 else ''}; newest: {names}"


@tool("open_file", group="files", description="Open a file or folder with its default application (use the full path from search_files).",
      parameters=P(path=("string", "Full path", True)), describe=lambda a: f"open {Path(str(a.get('path', ''))).name}")
def open_file(ctx: ToolContext, path: str):
    p = Path(path)
    if not p.exists():
        return {"opened": False}, f"{p.name} doesn't exist"
    if not allowed(ctx, p):
        return {"opened": False}, "that location isn't in the folders I'm allowed to open"
    os.startfile(str(p))
    return {"opened": True}, f"opened {p.name}"


@tool("read_file", group="files", description="Read the beginning of a text file (txt, md, json, csv, code).",
      parameters=P(path=("string", "Full path", True), max_chars=("integer", "default 4000", False)))
def read_file(ctx: ToolContext, path: str, max_chars: int = 4000):
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {"ok": False}, f"{p.name} doesn't exist"
    if not allowed(ctx, p):
        return {"ok": False}, "that location isn't in the folders I'm allowed to read"
    if p.suffix.lower() not in TEXT_EXT and p.stat().st_size > 200_000:
        return {"ok": False}, f"{p.name} isn't a text file I can read"
    text = p.read_text(encoding="utf-8", errors="replace")[: max(100, min(20000, max_chars))]
    return {"path": str(p), "text": text, "truncated": p.stat().st_size > len(text)}, f"read {len(text)} characters from {p.name}"


@tool("write_file", group="files", description="Write a new text file (or overwrite an existing one) in the user's folders.",
      parameters=P(path=("string", "Full path or a name; a bare name goes to Documents", True), content=("string", "Text content", True)),
      permission="confirm", risk="medium", describe=lambda a: f"write {len(str(a.get('content', '')))} characters to {a.get('path')}")
def write_file(ctx: ToolContext, path: str, content: str):
    p = Path(path)
    if not p.is_absolute():
        p = Path.home() / "Documents" / p
    if not allowed(ctx, p):
        return {"ok": False}, "that location isn't in the folders I'm allowed to write"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p)}, f"wrote {p.name}"


@tool("move_file", group="files", description="Move or rename a file within the user's folders.",
      parameters=P(src=("string", "Current full path", True), dst=("string", "New full path or destination folder", True)),
      permission="confirm", risk="medium", describe=lambda a: f"move {Path(str(a.get('src', ''))).name} to {a.get('dst')}")
def move_file(ctx: ToolContext, src: str, dst: str):
    s, d = Path(src), Path(dst)
    if not s.exists():
        return {"ok": False}, f"{s.name} doesn't exist"
    if d.is_dir():
        d = d / s.name
    if not (allowed(ctx, s) and allowed(ctx, d)):
        return {"ok": False}, "both locations must be inside the folders I'm allowed to use"
    if d.exists():
        return {"ok": False}, f"{d.name} already exists there"
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return {"path": str(d)}, f"moved to {d}"
