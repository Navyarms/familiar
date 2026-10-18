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


@tool("minimize_window", group="windows", description="Minimize a window by title/app name.",
      parameters=P(title=("string", "Part of the window title or app name", True)), describe=lambda a: f"minimize {a.get('title')}")
def minimize_window(ctx: ToolContext, title: str):
    w = find_window(title)
    if not w:
        return {"found": False}, f"no window matching {title}"
    user32.ShowWindow(w["hwnd"], SW_MINIMIZE)
    return {"found": True}, f"minimized {w['title']}"


@tool("maximize_window", group="windows", description="Maximize a window by title/app name.",
      parameters=P(title=("string", "Part of the window title or app name", True)), describe=lambda a: f"maximize {a.get('title')}")
def maximize_window(ctx: ToolContext, title: str):
    w = find_window(title)
    if not w:
        return {"found": False}, f"no window matching {title}"
    user32.ShowWindow(w["hwnd"], SW_MAXIMIZE)
    _focus(w["hwnd"])
    return {"found": True}, f"maximized {w['title']}"


@tool("close_window", group="windows", description="Close one window by title/app name (asks it to close, unsaved work may prompt).",
      parameters=P(title=("string", "Part of the window title or app name", True)), permission="confirm", risk="medium",
      describe=lambda a: f"close the {a.get('title')} window")
def close_window(ctx: ToolContext, title: str):
    w = find_window(title)
    if not w:
        return {"found": False}, f"no window matching {title}"
    user32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
    return {"found": True}, f"closed {w['title']}"


@tool("snap_window", group="windows", description="Snap a window to the left or right half of its screen, or center it.",
      parameters=P(title=("string", "Part of the window title or app name", True), position=("string", "left | right | center", ["left", "right", "center"], True)),
      describe=lambda a: f"snap {a.get('title')} to the {a.get('position')}")
def snap_window(ctx: ToolContext, title: str, position: str):
    w = find_window(title)
    if not w:
        return {"found": False}, f"no window matching {title}"
    h = w["hwnd"]
    user32.ShowWindow(h, SW_RESTORE)
    left, t, r, b = _work_area(h)
    W, H = r - left, b - t
    if position == "left":
        x, y, cw, ch = left, t, W // 2, H
    elif position == "right":
        x, y, cw, ch = left + W // 2, t, W - W // 2, H
    else:
        cw, ch = int(W * 0.7), int(H * 0.8)
        x, y = left + (W - cw) // 2, t + (H - ch) // 2
    user32.MoveWindow(h, x, y, cw, ch, True)
    _focus(h)
    return {"found": True, "rect": [x, y, x + cw, y + ch]}, f"snapped {w['title']} {position}"


@tool("minimize_all", group="windows", description="Minimize EVERY window to show the desktop. Only when the user says 'show the desktop' or 'minimize everything'; for one app use minimize_window.",
      describe=lambda a: "show the desktop")
def minimize_all(ctx: ToolContext):
    # Win+D equivalent through the shell COM object (works without sending keystrokes)
    import subprocess

    subprocess.run(["powershell", "-NoProfile", "-Command", "(New-Object -ComObject Shell.Application).MinimizeAll()"],
                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
    return {"ok": True}, "showing the desktop"
