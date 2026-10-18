"""Clipboard tools via user32/kernel32 (Unicode text only)."""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

from .registry import P, ToolContext, tool

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
CF_UNICODETEXT, GMEM_MOVEABLE = 13, 0x0002
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]


def _open() -> None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise RuntimeError("clipboard is busy")


def read_text() -> str:
    _open()
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return ""
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return ""
        p = kernel32.GlobalLock(h)
        try:
            return ctypes.wstring_at(p)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def write_text(text: str) -> None:
    data = text.encode("utf-16-le") + b"\x00\x00"
    _open()
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        user32.CloseClipboard()


@tool("clipboard_read", group="clipboard", description="Read the text currently on the clipboard.")
def clipboard_read(ctx: ToolContext):
    t = read_text()
    if not t:
        return {"text": ""}, "the clipboard has no text"
    return {"text": t[:4000]}, f"clipboard: {t[:160]}{'…' if len(t) > 160 else ''}"


@tool("clipboard_write", group="clipboard", description="Put text on the clipboard so the user can paste it.",
      parameters=P(text=("string", "Text to copy", True)), describe=lambda a: f"copy “{str(a.get('text', ''))[:60]}” to the clipboard")
def clipboard_write(ctx: ToolContext, text: str):
    write_text(text)
    return {"chars": len(text)}, "copied to the clipboard"
