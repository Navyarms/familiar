"""Memory store: SQLite with facts (atomic statements + embedding), episodes (conversation summaries) and raw turns
waiting to be summarized. Everything stays in %LOCALAPPDATA%\\Familiar\\memory.sqlite; Settings can list and delete it."""

from __future__ import annotations

import datetime as dt
import logging
import sqlite3
import threading
from pathlib import Path

import numpy as np

from .embed import Embedder, cosine

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL, source TEXT DEFAULT 'user',
  embedding BLOB, last_used TEXT, uses INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS episodes (
  id INTEGER PRIMARY KEY, started TEXT NOT NULL, ended TEXT NOT NULL, summary TEXT NOT NULL, turns INTEGER DEFAULT 0, embedding BLOB);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, summarized INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT);
"""


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _blob(v: np.ndarray | None) -> bytes | None:
    return None if v is None else np.asarray(v, np.float32).tobytes()


def _vec(b: bytes | None) -> np.ndarray | None:
    return None if b is None else np.frombuffer(b, dtype=np.float32)


class MemoryStore:
    def __init__(self, path: Path, embedder: Embedder | None = None):
        self.path = path
        self.embedder = embedder
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self.db.executescript(SCHEMA)
            cols = {r["name"] for r in self.db.execute("PRAGMA table_info(facts)")}
            for col, typ in (("embedding", "BLOB"), ("last_used", "TEXT"), ("uses", "INTEGER DEFAULT 0")):
                if col not in cols:  # upgrade from the Phase 3 table
                    self.db.execute(f"ALTER TABLE facts ADD COLUMN {col} {typ}")
            self.db.commit()
        if embedder:
            self._backfill()

    # ------------------------------------------------------------ embeddings
    def _embed(self, text: str) -> np.ndarray | None:
        return self.embedder.encode_one(text) if self.embedder else None

    def _backfill(self) -> None:
        rows = self.db.execute("SELECT id, text FROM facts WHERE embedding IS NULL").fetchall()
        rows += [("e", r["id"], r["summary"]) for r in self.db.execute("SELECT id, summary FROM episodes WHERE embedding IS NULL")]
        if not rows:
            return
        with self._lock:
            for r in rows:
                if isinstance(r, sqlite3.Row):
                    self.db.execute("UPDATE facts SET embedding=? WHERE id=?", (_blob(self._embed(r["text"])), r["id"]))
                else:
                    self.db.execute("UPDATE episodes SET embedding=? WHERE id=?", (_blob(self._embed(r[2])), r[1]))
            self.db.commit()
        log.info("backfilled %d embeddings", len(rows))

    # ------------------------------------------------------------ facts
    def remember(self, text: str, source: str = "user", dedupe: float = 0.92) -> tuple[int, bool]:
        """Insert unless a near-duplicate exists. Returns (id, created)."""
        text = " ".join(text.split()).strip().rstrip(".") + "."
        emb = self._embed(text)
        if emb is not None:
            near = self._search("facts", emb, limit=1)
            if near and near[0][1] >= dedupe:
                return near[0][0], False
        else:
            row = self.db.execute("SELECT id FROM facts WHERE lower(text)=lower(?)", (text,)).fetchone()
            if row:
                return row["id"], False
        with self._lock:
            cur = self.db.execute("INSERT INTO facts(text, created, source, embedding) VALUES (?,?,?,?)", (text, _now(), source, _blob(emb)))
            self.db.commit()
        return cur.lastrowid, True

    def _search(self, table: str, q: np.ndarray, limit: int = 8) -> list[tuple[int, float]]:
        rows = self.db.execute(f"SELECT id, embedding FROM {table} WHERE embedding IS NOT NULL").fetchall()
        if not rows:
            return []
        mat = np.stack([_vec(r["embedding"]) for r in rows])
        sims = cosine(q, mat)
        order = np.argsort(-sims)[:limit]
        return [(rows[i]["id"], float(sims[i])) for i in order]

    def recall(self, query: str, limit: int = 8, min_score: float = 0.0) -> list[dict]:
        emb = self._embed(query)
        out = []
        if emb is not None:
            for fid, score in self._search("facts", emb, limit):
                if score >= min_score:
                    r = self.db.execute("SELECT id, text, created, source FROM facts WHERE id=?", (fid,)).fetchone()
                    out.append({"id": r["id"], "kind": "fact", "text": r["text"], "created": r["created"], "source": r["source"], "score": round(score, 3)})
        else:  # keyword fallback
            toks = [t for t in query.lower().split() if len(t) > 2]
            for r in self.db.execute("SELECT id, text, created, source FROM facts ORDER BY id DESC LIMIT 500"):
                score = sum(1 for t in toks if t in r["text"].lower())
                if score or not toks:
                    out.append({"id": r["id"], "kind": "fact", "text": r["text"], "created": r["created"], "source": r["source"], "score": float(score)})
            out.sort(key=lambda d: -d["score"])
            out = out[:limit]
        return out

    def recall_episodes(self, query: str, limit: int = 2, min_score: float = 0.5) -> list[dict]:
        emb = self._embed(query)
        if emb is None:
            return []
        out = []
        for eid, score in self._search("episodes", emb, limit):
            if score >= min_score:
                r = self.db.execute("SELECT id, summary, started, ended FROM episodes WHERE id=?", (eid,)).fetchone()
                out.append({"id": r["id"], "kind": "episode", "text": r["summary"], "created": r["started"], "score": round(score, 3)})
        return out

    def context_for(self, query: str, *, k_facts: int = 6, k_recent: int = 3, min_score: float = 0.55) -> list[str]:
        """What to put in the prompt for this user turn: relevant facts, a few recent ones, related episode summaries."""
        picked: list[str] = []
        seen: set[int] = set()
        for f in self.recall(query, limit=k_facts, min_score=min_score):
            seen.add(f["id"])
            picked.append(f["text"])
        for r in self.db.execute("SELECT id, text FROM facts ORDER BY id DESC LIMIT ?", (k_recent,)):
            if r["id"] not in seen:
                picked.append(r["text"])
        ids = list(seen)
        if ids:
            with self._lock:
                self.db.execute(f"UPDATE facts SET last_used=?, uses=uses+1 WHERE id IN ({','.join('?' * len(ids))})", (_now(), *ids))
                self.db.commit()
        eps = [f"(earlier conversation, {e['created'][:10]}) {e['text']}" for e in self.recall_episodes(query)]
        return picked + eps

    def forget(self, query: str, min_score: float = 0.6) -> int:
        hits = [h for h in self.recall(query, limit=5) if h["score"] >= min_score] if self.embedder else self.recall(query, limit=5)
        with self._lock:
            for h in hits:
                self.db.execute("DELETE FROM facts WHERE id=?", (h["id"],))
            self.db.commit()
        return len(hits)

    def delete(self, kind: str, id: int) -> bool:
        table = "facts" if kind == "fact" else "episodes"
        with self._lock:
            cur = self.db.execute(f"DELETE FROM {table} WHERE id=?", (id,))
            self.db.commit()
        return cur.rowcount > 0

    def delete_all(self) -> None:
        with self._lock:
            self.db.execute("DELETE FROM facts")
            self.db.execute("DELETE FROM episodes")
            self.db.execute("DELETE FROM turns")
            self.db.commit()

    def recent(self, limit: int = 20) -> list[str]:
        return [r["text"] for r in self.db.execute("SELECT text FROM facts ORDER BY id DESC LIMIT ?", (limit,))][::-1]

    def list_items(self, query: str = "", limit: int = 100) -> list[dict]:
        if query.strip():
            return self.recall(query, limit=limit) + self.recall_episodes(query, limit=min(10, limit), min_score=0.2)
        facts = [{"id": r["id"], "kind": "fact", "text": r["text"], "created": r["created"], "source": r["source"] or ""}
                 for r in self.db.execute("SELECT id, text, created, source FROM facts ORDER BY id DESC LIMIT ?", (limit,))]
        eps = [{"id": r["id"], "kind": "episode", "text": r["summary"], "created": r["started"], "source": f"{r['turns']} turns"}
               for r in self.db.execute("SELECT id, summary, started, turns FROM episodes ORDER BY id DESC LIMIT ?", (limit,))]
        return facts + eps

    def counts(self) -> dict[str, int]:
        return {t: self.db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("facts", "episodes", "turns")}

    # ------------------------------------------------------------ turns / episodes
    def add_turn(self, role: str, text: str) -> None:
        with self._lock:
            self.db.execute("INSERT INTO turns(ts, role, text) VALUES (?,?,?)", (_now(), role, text[:4000]))
            self.db.commit()

    def pending_turns(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT id, ts, role, text FROM turns WHERE summarized=0 ORDER BY id")]

    def add_episode(self, summary: str, turn_ids: list[int], started: str, ended: str) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO episodes(started, ended, summary, turns, embedding) VALUES (?,?,?,?,?)",
                                  (started, ended, summary, len(turn_ids), _blob(self._embed(summary))))
            if turn_ids:
                self.db.execute(f"UPDATE turns SET summarized=1 WHERE id IN ({','.join('?' * len(turn_ids))})", turn_ids)
                self.db.execute("DELETE FROM turns WHERE summarized=1 AND id < (SELECT MAX(id) FROM turns) - 200")
            self.db.commit()
        return cur.lastrowid

    def mark_summarized(self, turn_ids: list[int]) -> None:
        if not turn_ids:
            return
        with self._lock:
            self.db.execute(f"UPDATE turns SET summarized=1 WHERE id IN ({','.join('?' * len(turn_ids))})", turn_ids)
            self.db.commit()

    def count(self) -> int:
        return self.counts()["facts"]
