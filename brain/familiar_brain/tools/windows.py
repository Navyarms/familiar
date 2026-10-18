"""Window tools via user32 (ctypes): list, focus, minimize, maximize, close, move/resize, snap."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

import psutil

from .registry import P, ToolContext, tool

user32 = ctypes.windll.user32
SW_MINIMIZE, SW_MAXIMIZE, SW_RESTORE, SW_SHOW = 6, 3, 9, 5
WM_CLOSE = 0x0010
GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x80
OWN_TITLES = {"familiar overlay", "familiar", "familiar settings"}


def _title(h) -> str:
    n = user32.GetWindowTextLengthW(h)
    if not n:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(h, buf, n + 1)
    return buf.value.replace("​", "").replace("﻿", "").strip()  # some games pad titles with zero-width spaces


def _pid(h) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
    return pid.value


def list_windows() -> list[dict]:
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
    def cb(h, _):
        if not user32.IsWindowVisible(h):
            return True
        if user32.GetWindowLongW(h, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
            return True
        t = _title(h)
        if not t or t.lower() in OWN_TITLES or t in ("Program Manager", "Windows Input Experience"):
            return True
        r = wt.RECT()
        user32.GetWindowRect(h, ctypes.byref(r))
        if r.right - r.left < 50 or r.bottom - r.top < 50:
            return True
        pid = _pid(h)
        try:
            proc = psutil.Process(pid).name()
        except Exception:
            proc = "?"
        out.append({"hwnd": int(h), "title": t, "process": proc, "minimized": bool(user32.IsIconic(h)),
                    "rect": [r.left, r.top, r.right, r.bottom]})
        return True

    user32.EnumWindows(cb, 0)
    return out


def find_window(query: str) -> dict | None:
    q = query.lower().strip()
    wins = list_windows()
    for w in wins:
        if w["title"].lower() == q:
            return w
    for w in wins:
        if q in w["title"].lower():
            return w
    for w in wins:
        if q in w["process"].lower().replace(".exe", ""):
            return w
    toks = set(q.split())
    best = max(wins, key=lambda w: len(toks & set(w["title"].lower().split())), default=None)
    if best and toks & set(best["title"].lower().split()):
        return best
    return None


def _focus(h: int) -> None:
    if user32.IsIconic(h):
        user32.ShowWindow(h, SW_RESTORE)
    # SetForegroundWindow is refused unless our thread is attached to the foreground thread's input queue
    fg = user32.GetForegroundWindow()
    cur = ctypes.windll.kernel32.GetCurrentThreadId()
    fgt = user32.GetWindowThreadProcessId(fg, None)
    if fgt and fgt != cur:
        user32.AttachThreadInput(cur, fgt, True)
    user32.BringWindowToTop(h)
    user32.SetForegroundWindow(h)
    if fgt and fgt != cur:
        user32.AttachThreadInput(cur, fgt, False)


def close_windows_of(q: str) -> int:
    n = 0
    for w in list_windows():
        if q in w["title"].lower() or q in w["process"].lower().replace(".exe", ""):
            user32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
            n += 1
    return n


def _work_area(h: int) -> tuple[int, int, int, int]:
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT), ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]

    mon = user32.MonitorFromWindow(h, 2)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


@tool("list_windows", group="windows", description="List the open application windows (title and program).")
def tool_list_windows(ctx: ToolContext):
    wins = list_windows()
    names = [f"{w['title']} ({w['process']})" for w in wins[:25]]
    return [{"title": w["title"], "process": w["process"], "minimized": w["minimized"]} for w in wins], \
        (f"{len(wins)} windows: " + "; ".join(names)) if wins else "no windows open"


@tool("focus_window", group="windows", description="Bring a window to the front by (part of) its title or program name.",
      parameters=P(title=("string", "Part of the window title or app name, e.g. 'Chrome', 'budget.xlsx'", True)),
      describe=lambda a: f"focus the {a.get('title')} window")
def focus_window(ctx: ToolContext, title: str):
    w = find_window(title)
    if not w:
        return {"found": False}, f"no window matching {title}"
    _focus(w["hwnd"])
    return {"found": True, "title": w["title"]}, f"focused {w['title']}"


@tool("window_action", group="windows", description="Minimize, maximize, restore, close, or snap (left/right/center) a window by its title or app name.",
      parameters=P(title=("string", "Part of the window title or app name", True),
                   action=("string", "minimize | maximize | restore | close | snap_left | snap_right | center", ["minimize", "maximize", "restore", "close", "snap_left", "snap_right", "center"], True)),
      describe=lambda a: f"{str(a.get('action', '')).replace('_', ' ')} the {a.get('title')} window",
      permission_for=lambda a: "confirm" if a.get("action") == "close" else None, risk="medium")
def window_action(ctx: ToolContext, title: str, action: str):
    w = find_window(title)
    if not w:
        return {"found": False}, f"no window matching {title}"
    h = w["hwnd"]
    if action == "minimize":
        user32.ShowWindow(h, SW_MINIMIZE)
    elif action == "maximize":
        user32.ShowWindow(h, SW_MAXIMIZE)
        _focus(h)
    elif action == "restore":
        user32.ShowWindow(h, SW_RESTORE)
        _focus(h)
    elif action == "close":
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    else:
        user32.ShowWindow(h, SW_RESTORE)
        left, t, r, b = _work_area(h)
        W, H = r - left, b - t
        if action == "snap_left":
            x, y, cw, ch = left, t, W // 2, H
        elif action == "snap_right":
            x, y, cw, ch = left + W // 2, t, W - W // 2, H
        else:
            cw, ch = int(W * 0.7), int(H * 0.8)
            x, y = left + (W - cw) // 2, t + (H - ch) // 2
        user32.MoveWindow(h, x, y, cw, ch, True)
        _focus(h)
    return {"found": True, "title": w["title"], "action": action}, f"{action.replace('_', ' ')}: {w['title']}"


