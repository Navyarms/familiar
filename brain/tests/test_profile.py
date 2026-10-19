"""Location in the prompt, pinned explicit memories, recall fallback, weather default."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from familiar_brain.agent.turn import build_system_prompt
from familiar_brain.config import Paths, Settings
from familiar_brain.memory.store import MemoryStore
from familiar_brain.persona.loader import load_persona
from familiar_brain.profile import location_of
from familiar_brain.tools import memory as memtools
from familiar_brain.tools import weather


def test_location_source_precedence():
    s = Settings()
    assert location_of(s) == (None, "none")
    s.location_detected = "Columbus, Ohio, United States"
    assert location_of(s) == ("Columbus, Ohio, United States", "network")
    s.user_location = "Dublin, OH"
    assert location_of(s) == ("Dublin, OH", "settings")
    s.location_detect = False
    s.user_location = None
    assert location_of(s) == (None, "none")


def test_prompt_mentions_location_with_the_right_caveat(tmp_path: Path):
    persona = load_persona(Paths(tmp_path).ensure(), "wisp")
    exact = build_system_prompt(persona, location="Dublin, OH", location_source="settings")
    guess = build_system_prompt(persona, location="Columbus, Ohio", location_source="network")
    assert "The user is in Dublin, OH" in exact and "approximate" not in exact
    assert "probably in Columbus, Ohio" in guess and "approximate" in guess
    assert "Dublin" not in build_system_prompt(persona, location="Dublin, OH", location_source="none")


def test_explicit_memories_are_pinned_in_the_system_prompt(tmp_path: Path):
    m = MemoryStore(tmp_path / "m.sqlite")  # keyword mode: similarity would never match "weekend plans"
    v0 = m.version
    m.remember("The user's dog is named Biscuit", source="user")
    m.remember("The user drives a blue Subaru", source="user")
    m.remember("The user asked about Python packaging", source="extracted")
    pinned = m.pinned()
    assert pinned == ["The user drives a blue Subaru.", "The user's dog is named Biscuit."]  # explicit only, newest first
    assert m.version == v0 + 2  # extracted facts do not change the pinned set
    persona = load_persona(Paths(tmp_path).ensure(), "wisp")
    prompt = build_system_prompt(persona, pinned=pinned)
    assert "asked you to remember" in prompt and "blue Subaru" in prompt and "Python packaging" not in prompt
    # the per-turn context leaves pinned facts out (they are already in the prompt) but keeps the rest
    ctx = m.context_for("any weekend plans?", exclude=set(pinned))
    assert "The user drives a blue Subaru." not in ctx and "The user asked about Python packaging." in ctx
    m.forget("Subaru")
    assert m.version == v0 + 3


def test_recall_falls_back_to_recent_facts(tmp_path: Path):
    m = MemoryStore(tmp_path / "m.sqlite")
    m.remember("The user's dog is named Biscuit", source="user")
    ctx = SimpleNamespace(extra={"memory": m})
    hits, spoken = memtools.recall.__wrapped__(ctx, "about me") if hasattr(memtools.recall, "__wrapped__") else memtools.recall(ctx, "about me")
    assert hits and "Biscuit" in spoken


def test_weather_without_place_and_without_location():
    brain = SimpleNamespace(location=lambda: (None, "none"))
    ctx = SimpleNamespace(extra={"brain": brain})
    fn = getattr(weather.get_weather, "__wrapped__", weather.get_weather)
    result, spoken = asyncio.run(fn(ctx, None))
    assert result == {"ok": False} and "Settings" in spoken


def test_explicit_remember_supersedes_conflicting_fact(tmp_path: Path):
    import os

    import pytest

    emb_dir = Path(os.environ.get("LOCALAPPDATA", "")) / "Familiar" / "models" / "bge-small-en-v1.5"
    if not (emb_dir / "model_quantized.onnx").exists():
        pytest.skip("embedding model not in the local store")
    from familiar_brain.memory.embed import Embedder

    m = MemoryStore(tmp_path / "m.sqlite", Embedder(emb_dir))
    m.remember("The user's favorite color is teal", source="user")
    m.remember("The user's dog is named Biscuit", source="user")
    _id, created = m.remember("The user's favorite color is forest green", source="user")
    assert created and m.last_replaced == "The user's favorite color is teal."
    texts = [i["text"] for i in m.list_items() if i["kind"] == "fact"]
    assert "The user's favorite color is forest green." in texts and "The user's favorite color is teal." not in texts
    assert "The user's dog is named Biscuit." in texts  # unrelated fact untouched


def test_extracted_facts_must_be_grounded_in_the_users_words():
    from familiar_brain.memory.extract import grounded

    user = "Remember that my favorite color is forest green.\nWhat should I have for breakfast tomorrow?\nI've got two dogs."
    assert grounded("The user's favorite color is forest green.", user)
    assert grounded("The user has two dogs.", user)
    assert not grounded("The user prefers black coffee.", user)  # the assistant said that, not the user
    assert not grounded("The user is named Dyllon.", user)
    assert not grounded("The user.", user)
