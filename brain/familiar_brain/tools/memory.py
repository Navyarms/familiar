"""Memory tools (Phase 3 minimum): atomic facts in SQLite with keyword recall. Phase 5 adds embeddings, episodes
and the Settings viewer. Recent facts are appended to the system prompt by the agent."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from .registry import P, ToolContext, tool


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL, source TEXT)")
        self.db.commit()

    def remember(self, text: str, source: str = "user") -> int:
        cur = self.db.execute("INSERT INTO facts(text, created, source) VALUES (?,?,?)", (text.strip(), dt.datetime.now().isoformat(timespec="seconds"), source))
        self.db.commit()
        return cur.lastrowid

    def recall(self, query: str, limit: int = 8) -> list[dict]:
        toks = [t for t in query.lower().split() if len(t) > 2]
        rows = self.db.execute("SELECT id, text, created FROM facts ORDER BY id DESC LIMIT 500").fetchall()
        scored = []
        for i, text, created in rows:
            low = text.lower()
            score = sum(1 for t in toks if t in low)
            if score or not toks:
                scored.append((score, i, text, created))
        scored.sort(key=lambda r: (-r[0], -r[1]))
        return [{"id": i, "text": t, "created": c} for _, i, t, c in scored[:limit]]

    def recent(self, limit: int = 20) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT text FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()][::-1]

    def forget(self, query: str) -> int:
        hits = self.recall(query, limit=5)
        hits = [h for h in hits if any(t in h["text"].lower() for t in query.lower().split() if len(t) > 2)]
        for h in hits:
            self.db.execute("DELETE FROM facts WHERE id=?", (h["id"],))
        self.db.commit()
        return len(hits)

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]


def store(ctx: ToolContext) -> MemoryStore:
    m = ctx.extra.get("memory")
    if m is None:
        m = ctx.extra["memory"] = MemoryStore(Path(ctx.paths.home) / "memory.sqlite")
    return m


@tool("remember", group="memory", description="Save a fact about the user or their preferences for future conversations.",
      parameters=P(fact=("string", "One short sentence, e.g. 'The user's dog is named Biscuit'", True)),
      describe=lambda a: f"remember: {a.get('fact')}")
def remember(ctx: ToolContext, fact: str):
    i = store(ctx).remember(fact)
    return {"id": i}, "I'll remember that"


@tool("recall", group="memory", description="Look up what you remember about a topic.",
      parameters=P(query=("string", "Topic or keywords", True)))
def recall(ctx: ToolContext, query: str):
    hits = store(ctx).recall(query)
    if not hits:
        return [], f"nothing remembered about {query}"
    return hits, "; ".join(h["text"] for h in hits[:5])


@tool("forget", group="memory", description="Delete remembered facts matching a topic.",
      parameters=P(query=("string", "Topic or keywords", True)), permission="confirm", risk="low",
      describe=lambda a: f"forget what I know about {a.get('query')}")
def forget(ctx: ToolContext, query: str):
    n = store(ctx).forget(query)
    return {"deleted": n}, f"forgot {n} thing{'s' if n != 1 else ''}" if n else "nothing to forget"
