"""Memory store (with the real embedder when present), retrieval context, and the extractor with a fake LLM."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from familiar_brain.memory.extract import extract_and_summarize
from familiar_brain.memory.store import MemoryStore

EMB_DIR = Path(os.environ.get("LOCALAPPDATA", "")) / "Familiar" / "models" / "bge-small-en-v1.5"


@pytest.fixture(scope="module")
def embedder():
    if not (EMB_DIR / "model_quantized.onnx").exists():
        pytest.skip("embedding model not in the local store")
    from familiar_brain.memory.embed import Embedder

    return Embedder(EMB_DIR)


def test_keyword_store_without_embedder(tmp_path: Path):
    m = MemoryStore(tmp_path / "m.sqlite")
    i1, created = m.remember("The user's dog is named Biscuit")
    assert created
    _, again = m.remember("the user's dog is named biscuit")
    assert not again
    assert m.recall("dog")[0]["text"].startswith("The user's dog")
    assert m.forget("dog") == 1 and m.count() == 0


def test_semantic_store(tmp_path: Path, embedder):
    m = MemoryStore(tmp_path / "m.sqlite", embedder)
    m.remember("The user's dog is named Biscuit")
    m.remember("The user drinks their coffee black")
    m.remember("The user works on a project called Familiar")
    _, created = m.remember("The user has a dog called Biscuit")  # near duplicate
    assert not created and m.count() == 3
    ctx = m.context_for("what's my dog called?")
    assert ctx and "Biscuit" in ctx[0], ctx
    assert not any("coffee" in c for c in ctx[:1])
    assert m.recall("espresso preferences", limit=1)[0]["text"].startswith("The user drinks")
    assert m.forget("the dog") == 1
    assert m.counts()["facts"] == 2


def test_episode_and_turns(tmp_path: Path, embedder):
    m = MemoryStore(tmp_path / "m.sqlite", embedder)
    m.add_turn("user", "Can you set a timer for the pasta?")
    m.add_turn("assistant", "Timer set for ten minutes.")
    pending = m.pending_turns()
    assert len(pending) == 2
    eid = m.add_episode("The user asked for a ten minute pasta timer and got one.", [t["id"] for t in pending], pending[0]["ts"], pending[-1]["ts"])
    assert eid and not m.pending_turns()
    eps = m.recall_episodes("cooking pasta timer")
    assert eps and eps[0]["id"] == eid
    items = m.list_items()
    assert any(i["kind"] == "episode" for i in items)
    assert m.delete("episode", eid) and m.counts()["episodes"] == 0


def test_upgrade_from_phase3_table(tmp_path: Path, embedder):
    import sqlite3

    p = tmp_path / "old.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created TEXT NOT NULL, source TEXT)")
    db.execute("INSERT INTO facts(text, created, source) VALUES ('The user likes teal', '2026-10-18T00:00:00', 'user')")
    db.commit()
    db.close()
    m = MemoryStore(p, embedder)  # adds columns and backfills embeddings
    assert m.recall("favorite color", limit=1)[0]["text"].startswith("The user likes teal")


class FakeLLM:
    def __init__(self, out):
        self.out = out
        self.calls = []

    async def complete_json(self, messages, schema, **kw):
        self.calls.append(messages)
        return self.out


async def test_extractor(tmp_path: Path, embedder):
    m = MemoryStore(tmp_path / "m.sqlite", embedder)
    m.remember("The user drinks their coffee black")
    m.add_turn("user", "My dog's name is Biscuit, and I live in Austin.")
    m.add_turn("assistant", "Biscuit, got it. Austin's a fun town.")
    llm = FakeLLM({"summary": "The user introduced their dog Biscuit and said they live in Austin.",
                   "facts": ["The user's dog is named Biscuit", "The user lives in Austin", "The user drinks coffee black",
                             "The assistant said hello"]})
    res = await extract_and_summarize(llm, m, m.pending_turns())
    assert res["episode_id"] and set(res["new_facts"]) == {"The user's dog is named Biscuit", "The user lives in Austin"}
    assert res["skipped"] == 2  # duplicate coffee fact + assistant line
    assert "Known facts" in llm.calls[0][1]["content"] and "Biscuit" in llm.calls[0][1]["content"]
    assert not m.pending_turns() and m.counts() == {"facts": 3, "episodes": 1, "turns": 2}
