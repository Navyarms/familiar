"""Interference guard: do not inject input while the user is actively using the mouse or keyboard.

Detection is cheap and dependency-free: the cursor moved since we last left it, or a physical key /
mouse button is currently down (GetAsyncKeyState). Injected input from our own SendInput calls is
accounted for by recording where we put the cursor. `wait_for_quiet` blocks (in a worker thread) until
the user has been idle for `quiet_s`, up to `max_wait_s`, then raises if they are still busy.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
import time

user32 = ctypes.windll.user32

_MOUSE_KEYS = (0x01, 0x02, 0x04)  # left, right, middle buttons
_IGNORE = {0x14, 0x90, 0x91}  # caps/num/scroll lock state bits are not "activity"


class InterferenceGuard:
    def __init__(self, quiet_s: float = 1.0, max_wait_s: float = 8.0, enabled: bool = True):
        self.quiet_s, self.max_wait_s, self.enabled = quiet_s, max_wait_s, enabled
        self._our_cursor: tuple[int, int] | None = None
        self._lock = threading.Lock()
        self.paused_total_s = 0.0

    # -------------------------------------------------------------- bookkeeping
    def note_cursor(self) -> None:
        """Call right after we move the cursor ourselves."""
        p = wt.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        self._our_cursor = (p.x, p.y)

    @staticmethod
    def _cursor() -> tuple[int, int]:
        p = wt.POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return (p.x, p.y)

    @staticmethod
    def keys_down() -> list[int]:
        down = []
        for vk in range(0x01, 0xFF):
            if vk in _IGNORE:
                continue
            if user32.GetAsyncKeyState(vk) & 0x8000:
                down.append(vk)
        return down

    def user_active(self) -> str | None:
        """Returns a reason string if the user seems to be using the PC right now, else None."""
        if not self.enabled:
            return None
        if self.keys_down():
            return "keyboard or mouse button held"
        c = self._cursor()
        if self._our_cursor is not None and (abs(c[0] - self._our_cursor[0]) > 3 or abs(c[1] - self._our_cursor[1]) > 3):
            return "mouse moving"
        if self._our_cursor is None:
            time.sleep(0.12)
            c2 = self._cursor()
            if c2 != c:
                return "mouse moving"
        return None

    def wait_for_quiet(self, kill: threading.Event | None = None) -> float:
        """Block until the user is idle for quiet_s. Returns seconds waited. Raises RuntimeError when they stay busy."""
        if not self.enabled:
            return 0.0
        t0 = time.monotonic()
        quiet_since: float | None = None
        while True:
            if kill is not None and kill.is_set():
                raise RuntimeError("stopped by the kill switch")
            reason = self.user_active()
            now = time.monotonic()
            if reason is None:
                quiet_since = quiet_since or now
                if now - quiet_since >= self.quiet_s or (self._our_cursor is not None and now - t0 < 0.05):
                    waited = now - t0
                    self.paused_total_s += waited
                    return waited
            else:
                quiet_since = None
                self._our_cursor = self._cursor()  # follow the user's cursor so "moving" means moving from here
            if now - t0 > self.max_wait_s:
                what = (reason or "mouse or keyboard").split()[0]
                raise RuntimeError(f"paused: you're using the {what}; I'll wait, ask again when you're done")
            time.sleep(0.1)


GUARD = InterferenceGuard()
