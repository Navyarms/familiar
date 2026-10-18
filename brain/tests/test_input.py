"""Input / screen tools: hotkey parsing, interference guard logic, and a real UI Automation round trip on Notepad."""

from __future__ import annotations

import asyncio
import subprocess
import time
from types import SimpleNamespace

import pytest

from familiar_brain.tools.guard import InterferenceGuard
from familiar_brain.tools.input import VK, parse_hotkey, release_all
from familiar_brain.tools.registry import ToolContext


def test_parse_hotkey():
    assert parse_hotkey("ctrl+shift+s") == [VK["ctrl"], VK["shift"], ord("S")]
    assert parse_hotkey("Win + D") == [VK["win"], ord("D")]
    assert parse_hotkey("alt-tab") == [VK["alt"], VK["tab"]]
    assert parse_hotkey("F5") == [VK["f5"]]
    assert parse_hotkey("enter") == [0x0D]
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+bogus")


def test_guard_waits_then_raises(monkeypatch):
    g = InterferenceGuard(quiet_s=0.05, max_wait_s=0.3)
    # user holding a key the whole time -> gives up with a friendly error
    monkeypatch.setattr(g, "keys_down", lambda: [0x41])
    with pytest.raises(RuntimeError, match="paused"):
        g.wait_for_quiet()
    # idle -> returns quickly
    monkeypatch.setattr(g, "keys_down", lambda: [])
    monkeypatch.setattr(g, "_cursor", staticmethod(lambda: (10, 10)))
    g._our_cursor = (10, 10)
    assert g.wait_for_quiet() < 0.2
    # disabled -> never blocks
    g.enabled = False
    monkeypatch.setattr(g, "keys_down", lambda: [0x41])
    assert g.wait_for_quiet() == 0.0


def test_release_all_is_safe_when_nothing_held():
    assert release_all() == 0


def _ctx():
    async def _noop(*a, **k):
        return None

    return ToolContext(paths=SimpleNamespace(home="."), settings=SimpleNamespace(interference_guard=False, vision_max_width=1024),
                       send=_noop, announce=_noop, loop=asyncio.new_event_loop(), kill=asyncio.Event(), extra={})


@pytest.mark.timeout(60)
def test_uia_roundtrip_on_notepad():
    """Open Notepad, type through SendInput, read it back through UI Automation, click the File menu by name, close."""
    from familiar_brain.tools import input as inp
    from familiar_brain.tools import screen
    from familiar_brain.tools.windows import find_window

    proc = subprocess.Popen(["notepad.exe"])
    try:
        for _ in range(40):
            if find_window("notepad"):
                break
            time.sleep(0.25)
        w = find_window("notepad")
        assert w, "Notepad did not open"
        ctx = _ctx()
        from familiar_brain.tools.windows import focus_window

        focus_window(ctx, "Notepad")
        time.sleep(0.6)
        marker = f"familiar uia test {int(time.time())}"
        inp.type_text(ctx, marker)
        data, summary = {"text": ""}, ""
        for _ in range(12):  # the Windows 11 Notepad exposes typed text a moment later
            time.sleep(0.3)
            data, summary = screen.read_window_text(ctx, window="Notepad")
            if marker in data["text"]:
                break
        assert marker in data["text"], summary
        els, s2 = screen.find_elements(ctx, "File", window="Notepad")
        assert isinstance(els, dict) and els["elements"], s2
        res, s3 = screen.click_element(ctx, "File", window="Notepad")
        assert res["found"], s3
        time.sleep(0.3)
        inp.press_keys(ctx, "esc")  # close the menu
        fg, s4 = screen.get_foreground_window(ctx)
        assert "notepad" in fg["title"].lower() or "notepad" in fg["process"].lower(), s4
    finally:
        # discard changes: close via WM_CLOSE then answer "Don't save" with the keyboard, else kill
        try:
            inp.press_keys(_ctx(), "ctrl+w")
            time.sleep(0.5)
            inp.press_keys(_ctx(), "n")  # "Don't save" in the Windows 11 Notepad dialog
            time.sleep(0.5)
        except Exception:
            pass
        if proc.poll() is None:
            subprocess.run(["taskkill", "/F", "/IM", "Notepad.exe"], capture_output=True)
            subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)


def test_bluetooth_read_only():
    """Reads the Bluetooth state through the Settings page without changing it (skips if no Bluetooth hardware)."""
    from familiar_brain.tools import screen

    try:
        res, summary = screen.set_bluetooth(_ctx(), "check")
    except RuntimeError as e:
        pytest.skip(str(e))
    assert "enabled" in res and res["changed"] is False, summary
    subprocess.run(["taskkill", "/F", "/IM", "SystemSettings.exe"], capture_output=True)
