"""Input tools: keyboard and mouse through SendInput. Every action passes the interference guard first,
and anything we press is tracked so the kill switch can release it."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

from .guard import GUARD
from .registry import P, ToolContext, tool

user32 = ctypes.windll.user32
INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSEEVENTF_MOVE, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x1, 0x8000, 0x4000, 0x800, 0x1000
MOUSE_DOWN = {"left": 0x2, "right": 0x8, "middle": 0x20}
MOUSE_UP = {"left": 0x4, "right": 0x10, "middle": 0x40}
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79

VK = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B, "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "win": 0x5B, "windows": 0x5B, "cmd": 0x5B, "meta": 0x5B, "menu": 0x5D, "apps": 0x5D,
    "capslock": 0x14, "numlock": 0x90, "printscreen": 0x2C, "prtsc": 0x2C, "pause": 0x13,
    "volumeup": 0xAF, "volumedown": 0xAE, "volumemute": 0xAD, "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE, "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "lbracket": 0xDB, "rbracket": 0xDD, "grave": 0xC0, "tilde": 0xC0,
}
for _i in range(1, 25):
    VK[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK[_c] = ord(_c.upper())
for _d in "0123456789":
    VK[_d] = ord(_d)
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5D, 0x2C, 0x90, 0xAF, 0xAE, 0xAD, 0xB3, 0xB0, 0xB1}


class _KI(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MI(ctypes.Structure):
    _fields_ = [("dx", wt.LONG), ("dy", wt.LONG), ("mouseData", wt.DWORD), ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _U(ctypes.Union):
    _fields_ = [("ki", _KI), ("mi", _MI)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _U)]


_pressed: set[int] = set()
_buttons: set[str] = set()
_io_lock = threading.Lock()


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    n = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if n != len(inputs):
        raise RuntimeError(f"SendInput sent {n}/{len(inputs)} events (error {ctypes.get_last_error()})")


def _key(vk: int, up: bool) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED else 0)
    scan = user32.MapVirtualKeyW(vk, 0)
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=_KI(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)))


def _unicode(ch: str, up: bool) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, u=_U(ki=_KI(wVk=0, wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), time=0, dwExtraInfo=None)))


def parse_hotkey(combo: str) -> list[int]:
    """'ctrl+shift+s' -> [VK_CONTROL, VK_SHIFT, ord('S')]. Accepts 'control', 'win', 'cmd', single chars, F-keys."""
    parts = [p.strip().lower() for p in combo.replace(" ", "").replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty key combination")
    out = []
    for p in parts:
        key = VK.get(p)
        if key is None and len(p) == 1:
            r = user32.VkKeyScanW(ord(p))
            if r == -1:
                raise ValueError(f"unknown key '{p}'")
            key = r & 0xFF
        if key is None:
            raise ValueError(f"unknown key '{p}'")
        out.append(key)
    return out


def press_combo(vks: list[int]) -> None:
    with _io_lock:
        try:
            for vk in vks:
                _send(_key(vk, False))
                _pressed.add(vk)
                time.sleep(0.015)
            time.sleep(0.03)
        finally:
            for vk in reversed(vks):
                _send(_key(vk, True))
                _pressed.discard(vk)
                time.sleep(0.01)


def type_unicode(text: str, cps: float = 60.0) -> None:
    delay = 1.0 / max(5.0, cps)
    with _io_lock:
        for ch in text:
            if ch == "\n":
                _send(_key(0x0D, False), _key(0x0D, True))
            elif ch == "\t":
                _send(_key(0x09, False), _key(0x09, True))
            else:
                _send(_unicode(ch, False), _unicode(ch, True))
            time.sleep(delay)


def _virtual_screen() -> tuple[int, int, int, int]:
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN), user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN), user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def move_to(x: int, y: int) -> None:
    vx, vy, vw, vh = _virtual_screen()
    nx = int((x - vx) * 65535 / max(1, vw - 1))
    ny = int((y - vy) * 65535 / max(1, vh - 1))
    _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=nx, dy=ny, mouseData=0, dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, time=0, dwExtraInfo=None))))
    GUARD.note_cursor()


def click_at(x: int, y: int, button: str = "left", clicks: int = 1) -> None:
    with _io_lock:
        move_to(x, y)
        time.sleep(0.04)
        for _ in range(clicks):
            _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=0, dy=0, mouseData=0, dwFlags=MOUSE_DOWN[button], time=0, dwExtraInfo=None))))
            _buttons.add(button)
            time.sleep(0.03)
            _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=0, dy=0, mouseData=0, dwFlags=MOUSE_UP[button], time=0, dwExtraInfo=None))))
            _buttons.discard(button)
            time.sleep(0.06)


def scroll_at(x: int | None, y: int | None, amount: int, horizontal: bool = False) -> None:
    with _io_lock:
        if x is not None and y is not None:
            move_to(x, y)
            time.sleep(0.03)
        flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        step = 120 if amount > 0 else -120
        for _ in range(abs(int(amount))):
            _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=0, dy=0, mouseData=ctypes.c_ulong(step & 0xFFFFFFFF).value, dwFlags=flag, time=0, dwExtraInfo=None))))
            time.sleep(0.02)


def drag(x1: int, y1: int, x2: int, y2: int, duration_s: float = 0.4) -> None:
    with _io_lock:
        move_to(x1, y1)
        time.sleep(0.05)
        _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=0, dy=0, mouseData=0, dwFlags=MOUSE_DOWN["left"], time=0, dwExtraInfo=None))))
        _buttons.add("left")
        steps = max(8, int(duration_s / 0.016))
        for i in range(1, steps + 1):
            move_to(int(x1 + (x2 - x1) * i / steps), int(y1 + (y2 - y1) * i / steps))
            time.sleep(duration_s / steps)
        _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=0, dy=0, mouseData=0, dwFlags=MOUSE_UP["left"], time=0, dwExtraInfo=None))))
        _buttons.discard("left")


def release_all() -> int:
    """Kill switch: release every key and button we may still be holding."""
    n = 0
    for vk in list(_pressed):
        try:
            _send(_key(vk, True))
            n += 1
        except Exception:
            pass
        _pressed.discard(vk)
    for b in list(_buttons):
        try:
            _send(INPUT(type=INPUT_MOUSE, u=_U(mi=_MI(dx=0, dy=0, mouseData=0, dwFlags=MOUSE_UP[b], time=0, dwExtraInfo=None))))
            n += 1
        except Exception:
            pass
        _buttons.discard(b)
    return n


def _guard(ctx: ToolContext) -> float:
    GUARD.enabled = bool(getattr(ctx.settings, "interference_guard", True))
    return GUARD.wait_for_quiet(getattr(ctx, "kill", None))  # asyncio.Event: only .is_set() is read, which is thread-safe


# ------------------------------------------------------------------ tools
@tool("type_text", group="input", description="Type text into whatever has keyboard focus (focus the right window first). Use \\n for Enter.",
      parameters=P(text=("string", "Text to type", True), press_enter=("boolean", "press Enter afterwards", False)),
      describe=lambda a: f"type “{str(a.get('text', ''))[:50]}{'…' if len(str(a.get('text', ''))) > 50 else ''}”", timeout_s=120)
def type_text(ctx: ToolContext, text: str, press_enter: bool = False):
    waited = _guard(ctx)
    type_unicode(text)
    if press_enter:
        press_combo([0x0D])
    return {"chars": len(text), "paused_s": round(waited, 2)}, f"typed {len(text)} characters" + (" and pressed Enter" if press_enter else "")


@tool("press_keys", group="input", description="Press a key or shortcut, e.g. 'enter', 'ctrl+s', 'alt+tab', 'win+d', 'ctrl+shift+esc', 'f5'.",
      parameters=P(keys=("string", "Key combination", True), times=("integer", "repeat count, default 1", False)),
      describe=lambda a: f"press {a.get('keys')}")
def press_keys(ctx: ToolContext, keys: str, times: int = 1):
    vks = parse_hotkey(keys)
    _guard(ctx)
    for _ in range(max(1, min(50, times))):
        press_combo(vks)
        time.sleep(0.05)
    return {"keys": keys, "times": times}, f"pressed {keys}" + (f" {times} times" if times > 1 else "")


@tool("click", group="input", description="Click at screen coordinates (prefer click_element by name when possible).",
      parameters=P(x=("integer", "screen x", True), y=("integer", "screen y", True), button=("string", "left | right | middle", ["left", "right", "middle"], False),
                   double=("boolean", "double-click", False)),
      describe=lambda a: f"{'double-' if a.get('double') else ''}{a.get('button', 'left')} click at {a.get('x')},{a.get('y')}")
def click(ctx: ToolContext, x: int, y: int, button: str = "left", double: bool = False):
    _guard(ctx)
    click_at(int(x), int(y), button, 2 if double else 1)
    return {"x": x, "y": y}, f"clicked at {x},{y}"


@tool("scroll", group="input", description="Scroll the mouse wheel. Positive = up, negative = down; about 3 per screenful.",
      parameters=P(amount=("integer", "wheel notches, negative to scroll down", True), x=("integer", "optional screen x", False), y=("integer", "optional screen y", False)),
      describe=lambda a: f"scroll {'up' if int(a.get('amount', 0)) > 0 else 'down'} {abs(int(a.get('amount', 0)))}")
def scroll(ctx: ToolContext, amount: int, x: int | None = None, y: int | None = None):
    _guard(ctx)
    scroll_at(x, y, int(amount))
    return {"amount": amount}, f"scrolled {'up' if amount > 0 else 'down'} {abs(amount)}"


