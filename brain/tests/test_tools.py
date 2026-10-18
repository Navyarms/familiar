"""Tool registry, agent loop (with a scripted fake LLM) and a few real tool units."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from familiar_brain.agent.turn import Turn
from familiar_brain.llm.client import StreamStats, ToolCall
from familiar_brain.tools.registry import P, ToolContext, ToolRegistry, ToolSpec, _coerce


# ------------------------------------------------------------------ helpers
class FakeLLM:
    """Yields scripted steps: each step is (content, [ToolCall...]). Records the messages it was given."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls: list[list[dict]] = []

    async def stream(self, messages, *, max_tokens=400, stats: StreamStats = None, tools=None, temperature=0.7, **kwargs):
        self.calls.append([dict(m) for m in messages])
        content, tool_calls = self.steps.pop(0) if self.steps else ("", [])
        for tok in content.split(" "):
            if tok:
                yield tok + " "
        stats.tool_calls = tool_calls
        stats.ttft_s = 0.01


def persona():
    return SimpleNamespace(system_prompt="You are Test.", voice="af_heart", voice_speed=1.0, expression_tags=lambda: ["happy"])


def registry_with(*specs):
    r = ToolRegistry()
    for s in specs:
        r.register(s)
    r.configure(SimpleNamespace(tool_permissions={}, web_enabled=True))
    return r


def ctx(loop):
    return ToolContext(paths=SimpleNamespace(home=Path(".")), settings=SimpleNamespace(file_roots=[]), send=_noop, announce=_noop,
                       loop=loop, kill=asyncio.Event())


async def _noop(*a, **k):
    return None


def spec(name, fn, permission="auto", risk="low"):
    return ToolSpec(name=name, group="test", description=name, parameters=P(x=("integer", "x", False)), handler=fn,
                    permission=permission, risk=risk, describe=lambda a: f"{name} with {a}")


# ------------------------------------------------------------------ registry
def test_coerce_types_and_unknown_keys():
    schema = P(n=("integer", "", True), f=("number", "", False), b=("boolean", "", False), s=("string", "", False), a=("array", "", False))
    out = _coerce(schema, {"n": "30", "f": "1.5", "b": "true", "s": 5, "a": "downloads, desktop", "junk": 1})
    assert out == {"n": 30, "f": 1.5, "b": True, "s": "5", "a": ["downloads", "desktop"]}
    with pytest.raises(ValueError):
        _coerce(schema, {})


def test_permission_resolution():
    r = registry_with(spec("a", lambda c: "ok"), spec("b", lambda c: "ok", permission="confirm"), spec("c", lambda c: "ok", permission="blocked"))
    assert [t.name for t in r.available()] == ["a", "b"]
    r.configure(SimpleNamespace(tool_permissions={"c": "auto", "group:test": "confirm"}, web_enabled=True))
    assert r.permission("a") == "confirm"  # group override beats default
    assert r.permission("c") == "auto"  # per-tool override beats group
    assert r.permission("nope") == "blocked"


async def test_run_coerces_and_times_out():
    def slow(c, x=0):
        time.sleep(1.0)
        return "late"

    r = registry_with(spec("slow", slow))
    r.tools["slow"].timeout_s = 0.2
    res = await r.run("slow", {"x": "1"}, ctx(asyncio.get_running_loop()))
    assert not res["ok"] and "timed out" in res["summary"]


# ------------------------------------------------------------------ agent loop
async def run_turn(llm, reg, confirm=None, text="do it"):
    events = []

    async def send(m):
        events.append(m)

    turn = Turn(llm=llm, tts=None, persona=persona(), history=[], send=send, speak=False, registry=reg,
                ctx=ctx(asyncio.get_running_loop()), confirm=confirm)
    out = await turn.run(text)
    return out, events, turn


async def test_tool_call_then_answer():
    seen = {}

    def open_app(c, x=0):
        seen["x"] = x
        return {"opened": True}, "opened it"

    reg = registry_with(spec("open_app", open_app))
    llm = FakeLLM([("Sure, opening.", [ToolCall("c1", "open_app", json.dumps({"x": 3}))]), ("Done, it is open.", [])])
    out, events, turn = await run_turn(llm, reg)
    assert seen == {"x": 3}
    assert out.startswith("Sure, opening.") and "Done, it is open." in out
    types = [e["type"] for e in events]
    assert "tool.start" in types and "tool.result" in types
    res = next(e for e in events if e["type"] == "tool.result")
    assert res["ok"] and res["summary"] == "opened it"
    # second model call carried the assistant tool_calls + tool result
    msgs = llm.calls[1]
    assert msgs[-2]["role"] == "assistant" and msgs[-2]["tool_calls"][0]["function"]["name"] == "open_app"
    assert msgs[-1]["role"] == "tool" and json.loads(msgs[-1]["content"])["ok"] is True
    assert turn.history[-1]["content"].startswith("Sure")


async def test_confirm_denied_and_allowed():
    ran = []
    reg = registry_with(spec("write", lambda c, x=0: ran.append(x) or "wrote", permission="confirm", risk="medium"))

    async def deny(request_id, tool, args, desc, risk):
        return False

    llm = FakeLLM([("", [ToolCall("c1", "write", "{\"x\": 1}")]), ("Okay, I won't.", [])])
    out, events, _ = await run_turn(llm, reg, confirm=deny)
    assert ran == []
    req = next(e for e in events if e["type"] == "tool.request_confirm")
    assert req["tool"] == "write" and req["risk"] == "medium" and "write with" in req["description"]
    assert next(e for e in events if e["type"] == "tool.result")["summary"] == "the user declined"
    assert any(e["type"] == "state" and e["state"] == "needs_confirm" for e in events)

    async def allow(*a):
        return True

    llm = FakeLLM([("", [ToolCall("c1", "write", "{\"x\": 2}")]), ("Written.", [])])
    out, events, _ = await run_turn(llm, reg, confirm=allow)
    assert ran == [2] and out.strip() == "Written."


async def test_blocked_and_unknown_tools_do_not_run():
    reg = registry_with(spec("danger", lambda c, x=0: "boom", permission="blocked"))
    llm = FakeLLM([("", [ToolCall("c1", "danger", "{}"), ToolCall("c2", "ghost", "{}")]), ("Can't.", [])])
    out, events, _ = await run_turn(llm, reg)
    results = [e for e in events if e["type"] == "tool.result"]
    assert [r["ok"] for r in results] == [False, False]
    assert "turned off" in results[0]["summary"] and "no tool" in results[1]["summary"]


async def test_bad_json_arguments_reported():
    reg = registry_with(spec("t", lambda c, x=0: "ok"))
    llm = FakeLLM([("", [ToolCall("c1", "t", "{not json")]), ("Sorry.", [])])
    _, events, _ = await run_turn(llm, reg)
    assert "bad arguments" in next(e for e in events if e["type"] == "tool.result")["summary"]


async def test_step_limit():
    reg = registry_with(spec("t", lambda c, x=0: "ok"))
    llm = FakeLLM([("", [ToolCall(f"c{i}", "t", "{}")]) for i in range(10)])
    _, events, turn = await run_turn(llm, reg)
    assert len([e for e in events if e["type"] == "tool.result"]) == turn.max_steps - 1


# ------------------------------------------------------------------ real tools
def test_clipboard_roundtrip():
    from familiar_brain.tools.clipboard import read_text, write_text

    old = read_text()
    try:
        write_text("familiar clipboard test ✓")
        assert read_text() == "familiar clipboard test ✓"
    finally:
        write_text(old or "")


async def test_search_files_in_extra_root(tmp_path: Path):
    from familiar_brain.tools import files

    (tmp_path / "Quarterly Report.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp_path / "notes.txt").write_text("hi")
    old = tmp_path / "old.pdf"
    old.write_bytes(b"%PDF")
    os.utime(old, (time.time() - 10 * 86400, time.time() - 10 * 86400))
    c = ToolContext(paths=SimpleNamespace(home=tmp_path), settings=SimpleNamespace(file_roots=[str(tmp_path)]), send=_noop,
                    announce=_noop, loop=asyncio.get_running_loop(), kill=asyncio.Event())
    res, summary = files.search_files(c, query="quarterly", ext="pdf", where=[str(tmp_path)])
    assert [r["name"] for r in res] == ["Quarterly Report.pdf"]
    res, _ = files.search_files(c, query="", ext="pdf", days=2, where=[str(tmp_path)])
    assert [r["name"] for r in res] == ["Quarterly Report.pdf"]
    assert files.allowed(c, tmp_path / "notes.txt") and not files.allowed(c, Path("C:/Windows/system.ini"))
    data, _ = files.read_file(c, str(tmp_path / "notes.txt"))
    assert data["text"] == "hi"


def test_timer_parse():
    import datetime as dt

    from familiar_brain.tools.timers import _parse_when
    now = dt.datetime.now()
    assert abs((_parse_when("in 10 minutes") - now).total_seconds() - 600) < 2
    t = _parse_when("at 3:30 pm")
    assert t.hour == 15 and t.minute == 30 and t > now
    assert _parse_when("tomorrow at 9").date() == (now + dt.timedelta(days=1)).date()
    assert _parse_when("whenever") is None


def test_memory_store(tmp_path: Path):
    from familiar_brain.tools.memory import MemoryStore

    m = MemoryStore(tmp_path / "m.sqlite")
    m.remember("The user's dog is named Biscuit")
    m.remember("The user likes espresso")
    assert m.recall("dog")[0]["text"].endswith("Biscuit")
    assert m.recent()[-1].endswith("espresso")
    assert m.forget("espresso") == 1 and m.count() == 1


def test_list_windows_and_resolve_app():
    from familiar_brain.tools.system import resolve_app
    from familiar_brain.tools.windows import list_windows

    assert resolve_app("calculator") == ("alias", "calc.exe")
    assert resolve_app("Settings")[1].startswith("ms-settings")
    assert isinstance(list_windows(), list)
