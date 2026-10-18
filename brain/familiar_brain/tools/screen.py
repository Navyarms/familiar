"""Screen tools. UI Automation first (fast, exact), vision second (slow, approximate).

- find_elements / click_element / set_element_text / read_window_text: Windows UI Automation through the
  `uiautomation` package on the foreground window (or a window picked by title).
- look_at_screen: screenshot of the foreground window (or the whole primary screen), downscaled, sent to the
  local Qwen3-VL through llama-server. 10-25 s on CPU, so the model is told to prefer the element tools.
- set_bluetooth: deterministic Settings-page automation (open ms-settings:bluetooth, read the toggle, flip only if needed).
"""

from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import time

from .guard import GUARD
from .registry import P, ToolContext, tool
from .windows import find_window, list_windows

log = logging.getLogger(__name__)

INTERACTIVE = {"ButtonControl", "ListItemControl", "TreeItemControl", "TabItemControl", "HyperlinkControl", "CheckBoxControl",
               "RadioButtonControl", "ComboBoxControl", "EditControl", "MenuItemControl", "SplitButtonControl", "MenuBarControl",
               "ToggleButtonControl", "SliderControl", "DocumentControl"}
TEXTUAL = {"TextControl", "EditControl", "DocumentControl", "HyperlinkControl", "ButtonControl", "ListItemControl", "MenuItemControl",
           "TabItemControl", "CheckBoxControl", "RadioButtonControl", "HeaderItemControl", "DataItemControl", "TreeItemControl"}


def _auto():
    import uiautomation as auto

    auto.SetGlobalSearchTimeout(3)
    return auto


def _window_control(auto, title: str | None):
    """uiautomation WindowControl for a title substring, or the foreground window."""
    if title:
        w = find_window(title)
        if not w:
            raise RuntimeError(f"no window matching {title!r}")
        return auto.ControlFromHandle(w["hwnd"]), w["title"]
    fg = auto.GetForegroundControl()
    top = fg.GetTopLevelControl() or fg
    return top, top.Name


def _walk(auto, root, max_depth=18, limit=400, want=None):
    out = []
    t0 = time.monotonic()
    try:
        for c, depth in auto.WalkControl(root, includeTop=False, maxDepth=max_depth):
            try:
                tn = c.ControlTypeName
                if want and tn not in want:
                    continue
                name = (c.Name or "").strip()
                if not name and tn != "EditControl":
                    continue
                out.append((c, depth, tn, name))
            except Exception:
                continue
            if len(out) >= limit or time.monotonic() - t0 > 4:
                break
    except Exception as e:  # WinUI throws transient COM errors while pages load
        log.debug("walk stopped: %s", e)
    return out


def _desc(c, tn, name) -> dict:
    r = c.BoundingRectangle
    d = {"name": name, "type": tn.replace("Control", ""), "x": (r.left + r.right) // 2, "y": (r.top + r.bottom) // 2}
    try:
        if c.GetPattern(_auto().PatternId.TogglePattern):
            d["toggled"] = bool(c.GetTogglePattern().ToggleState)
    except Exception:
        pass
    try:
        if tn == "EditControl":
            d["value"] = (c.GetValuePattern().Value or "")[:120]
    except Exception:
        pass
    return d


def _find(auto, root, query: str, control_type: str | None = None):
    q = query.lower().strip()
    cands = _walk(auto, root, want=INTERACTIVE | {"TextControl"})
    if control_type:
        ct = control_type.lower().replace("control", "")
        cands = [x for x in cands if x[2].lower().replace("control", "") == ct] or cands
    exact = [x for x in cands if x[3].lower() == q]
    starts = [x for x in cands if x[3].lower().startswith(q)]
    contains = [x for x in cands if q in x[3].lower()]
    toks = set(q.split())
    fuzzy = sorted((x for x in cands if toks & set(x[3].lower().split())), key=lambda x: -len(toks & set(x[3].lower().split())))
    for group in (exact, starts, contains, fuzzy):
        inter = [x for x in group if x[2] in INTERACTIVE]
        if inter:
            return inter[0]
        if group:
            return group[0]
    return None


def _activate(auto, c, tn: str) -> str:
    """Invoke / Toggle / Select / Expand patterns first, mouse click as the last resort."""
    for pat, how in ((auto.PatternId.InvokePattern, "invoke"), (auto.PatternId.TogglePattern, "toggle"),
                     (auto.PatternId.SelectionItemPattern, "select"), (auto.PatternId.ExpandCollapsePattern, "expand")):
        try:
            p = c.GetPattern(pat)
            if p:
                if how == "invoke":
                    c.GetInvokePattern().Invoke()
                elif how == "toggle":
                    c.GetTogglePattern().Toggle()
                elif how == "select":
                    c.GetSelectionItemPattern().Select()
                else:
                    c.GetExpandCollapsePattern().Expand()
                return how
        except Exception:
            continue
    from .input import click_at

    r = c.BoundingRectangle
    click_at((r.left + r.right) // 2, (r.top + r.bottom) // 2)
    return "click"


# ------------------------------------------------------------------ tools
@tool("get_foreground_window", group="screen", description="Which window is active right now (title and program).")
def get_foreground_window(ctx: ToolContext):
    auto = _auto()
    top, title = _window_control(auto, None)
    pid = top.ProcessId
    try:
        import psutil

        proc = psutil.Process(pid).name()
    except Exception:
        proc = "?"
    return {"title": title, "process": proc}, f"active window: {title} ({proc})"


@tool("find_elements", group="screen",
      description="List UI elements (buttons, menus, links, toggles, fields) of a window, optionally filtered by label. Fast; use before look_at_screen.",
      parameters=P(query=("string", "Part of the element's label, e.g. 'Reply', 'Bluetooth', 'Save'; empty = all", False),
                   window=("string", "Window title to search; default = the active window", False)), timeout_s=20)
def find_elements(ctx: ToolContext, query: str = "", window: str | None = None):
    auto = _auto()
    root, title = _window_control(auto, window)
    items = _walk(auto, root, want=INTERACTIVE)
    q = query.lower().strip()
    if q:
        items = [x for x in items if q in x[3].lower()]
    res = [_desc(c, tn, name) for c, _d, tn, name in items[:40]]
    if not res:
        return [], f"no elements matching {query!r} in {title}"
    return {"window": title, "elements": res}, f"{len(res)} elements in {title}: " + ", ".join(f"{e['name']} ({e['type']}{', on' if e.get('toggled') else ', off' if 'toggled' in e else ''})" for e in res[:12])


@tool("click_element", group="screen",
      description="Click a UI element by its label (button, menu item, link, list item, toggle, tab) in the active or named window.",
      parameters=P(name=("string", "Element label, e.g. 'Reply', 'File', 'Bluetooth & devices'", True),
                   window=("string", "Window title; default = active window", False),
                   control_type=("string", "Optional: Button, MenuItem, ListItem, CheckBox, TabItem, Hyperlink, ToggleButton", False)),
      describe=lambda a: f"click “{a.get('name')}”" + (f" in {a.get('window')}" if a.get("window") else ""), timeout_s=25, narrate=True)
def click_element(ctx: ToolContext, name: str, window: str | None = None, control_type: str | None = None):
    auto = _auto()
    root, title = _window_control(auto, window)
    hit = _find(auto, root, name, control_type)
    if not hit:
        return {"found": False}, f"couldn't find “{name}” in {title}"
    c, _d, tn, label = hit
    GUARD.wait_for_quiet()
    how = _activate(auto, c, tn)
    time.sleep(0.4)
    return {"found": True, "name": label, "type": tn.replace("Control", ""), "how": how}, f"clicked “{label}” ({how})"


@tool("set_element_text", group="screen", description="Put text into a text field by its label (replaces the current value).",
      parameters=P(name=("string", "Field label or placeholder", True), text=("string", "Text to set", True), window=("string", "Window title; default = active", False)),
      describe=lambda a: f"set “{a.get('name')}” to “{str(a.get('text', ''))[:40]}”", timeout_s=25)
def set_element_text(ctx: ToolContext, name: str, text: str, window: str | None = None):
    auto = _auto()
    root, title = _window_control(auto, window)
    hit = _find(auto, root, name, "Edit")
    if not hit:
        return {"found": False}, f"couldn't find a field “{name}” in {title}"
    c, _d, tn, label = hit
    GUARD.wait_for_quiet()
    try:
        c.GetValuePattern().SetValue(text)
        how = "value"
    except Exception:
        from .input import click_at, press_combo, type_unicode

        r = c.BoundingRectangle
        click_at((r.left + r.right) // 2, (r.top + r.bottom) // 2)
        press_combo([0x11, ord("A")])
        type_unicode(text)
        how = "typed"
    return {"found": True, "name": label, "how": how}, f"set “{label}” ({how})"


@tool("read_window_text", group="screen",
      description="Read the visible text of a window (document text, messages, labels), e.g. an email or a dialog.",
      parameters=P(window=("string", "Window title; default = active window", False), max_chars=("integer", "default 4000", False)), timeout_s=20)
def read_window_text(ctx: ToolContext, window: str | None = None, max_chars: int = 4000):
    auto = _auto()
    root, title = _window_control(auto, window)
    parts, seen = [], set()
    for c, _d, tn, name in _walk(auto, root, want=TEXTUAL, limit=600):
        txt = name
        if tn in ("EditControl", "DocumentControl"):
            try:
                v = c.GetValuePattern().Value
                if v:
                    txt = v
            except Exception:
                try:
                    txt = c.GetTextPattern().DocumentRange.GetText(-1) or name
                except Exception:
                    pass
        txt = (txt or "").strip()
        if txt and txt not in seen:
            seen.add(txt)
            parts.append(txt)
    text = "\n".join(parts)[: max(200, min(20000, max_chars))]
    return {"window": title, "text": text}, f"read {len(text)} characters from {title}: {text[:160]!r}"


def grab(window_only: bool, max_width: int) -> tuple[bytes, tuple[int, int], str]:
    """PNG bytes of the foreground window (or primary screen), downscaled to max_width. Returns (png, (w,h), title)."""
    from PIL import ImageGrab

    title = "screen"
    bbox = None
    if window_only:
        import ctypes
        import ctypes.wintypes as wt

        h = ctypes.windll.user32.GetForegroundWindow()
        r = wt.RECT()
        if h and ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r)) and r.right - r.left > 100 and r.bottom - r.top > 100:
            bbox = (r.left, r.top, r.right, r.bottom)
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(h, buf, 256)
            title = buf.value or "window"
    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)))
    img = img.convert("RGB")
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue(), img.size, title


TEXTY = re.compile(r"\b(say|says|said|read|text|dialog|message|error|warning|title|write|written|words|button|menu|label)\b", re.I)
VISUAL = re.compile(r"\b(picture|photo|image|screenshot|look like|looks like|color|colour|drawing|chart|graph|video|game|layout|design|icon)\b", re.I)


@tool("look_at_screen", group="screen",
      description="Look at the active window (or whole screen) with vision and answer a question about it. Slow; only when element tools cannot help.",
      parameters=P(question=("string", "What to look for, e.g. 'what does the error say?'; default = describe it", False),
                   whole_screen=("boolean", "true = entire primary screen instead of the active window", False)),
      describe=lambda a: "look at the screen", timeout_s=120, narrate=True)
async def look_at_screen(ctx: ToolContext, question: str = "", whole_screen: bool = False):
    brain = ctx.extra.get("brain")
    llm = getattr(brain, "llm", None)
    if llm is None:
        return {"ok": False}, "vision is not available right now"
    import asyncio

    # Actions over vision: a textual question about the active window is answered through accessibility when it can be.
    if not whole_screen and question and TEXTY.search(question) and not VISUAL.search(question):
        try:
            data, _s = await asyncio.to_thread(read_window_text, ctx, None, 3000)
            if len(data.get("text", "")) >= 40:
                return {"window": data["window"], "answer": data["text"], "via": "accessibility"}, \
                    f"(read from {data['window']} without vision) {data['text'][:280]}"
        except Exception as e:  # fall through to vision
            log.debug("accessibility fast path failed: %s", e)

    png, size, title = await asyncio.to_thread(grab, not whole_screen, int(getattr(ctx.settings, "vision_max_width", 1024)))
    prompt = (question.strip() or "Describe what is on this screen in two sentences, then list the main clickable things you see.")
    prompt += " Be concrete and brief."
    t0 = time.monotonic()
    answer = await llm.ask_image(png, prompt, max_tokens=220)
    dt = time.monotonic() - t0
    log.info("vision %dx%d in %.1fs: %s", size[0], size[1], dt, answer[:100])
    return {"window": title, "answer": answer, "seconds": round(dt, 1)}, answer[:300]


# ------------------------------------------------------------------ Bluetooth via the Settings page
def _bluetooth_toggle(auto):
    """Open ms-settings:bluetooth and return (window, toggle control). Retries while the WinUI page loads."""
    win = auto.WindowControl(searchDepth=1, Name="Settings")
    if not win.Exists(0.5, 0.1):
        os.startfile("ms-settings:bluetooth")
    else:
        subprocess.Popen(["cmd", "/c", "start", "ms-settings:bluetooth"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if not win.Exists(10, 0.2):
        raise RuntimeError("the Settings window did not open")
    win.SetActive()
    for _ in range(14):
        try:
            tog = win.ButtonControl(searchDepth=25, Name="Bluetooth")
            if tog.Exists(1, 0.2) and tog.GetPattern(auto.PatternId.TogglePattern):
                return win, tog
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("couldn't find the Bluetooth switch on the Settings page (is Bluetooth hardware present?)")


@tool("set_bluetooth", group="system", description="Bluetooth: check whether it is on, or turn it on or off (through the Settings page).",
      parameters=P(action=("string", "check | on | off", ["check", "on", "off"], True)),
      describe=lambda a: {"on": "turn Bluetooth on", "off": "turn Bluetooth off"}.get(a.get("action"), "check Bluetooth"), timeout_s=45, narrate=True)
def set_bluetooth(ctx: ToolContext, action: str):
    enabled = None if action == "check" else action == "on"
    auto = _auto()
    win, tog = _bluetooth_toggle(auto)
    state = bool(tog.GetTogglePattern().ToggleState)
    if enabled is None or enabled == state:
        return {"enabled": state, "changed": False}, (f"Bluetooth is already {'on' if state else 'off'}" if enabled is not None
                                                      else f"Bluetooth is {'on' if state else 'off'}")
    GUARD.wait_for_quiet()
    tog.GetTogglePattern().Toggle()
    for _ in range(20):
        time.sleep(0.25)
        try:
            if bool(tog.GetTogglePattern().ToggleState) == enabled:
                return {"enabled": enabled, "changed": True}, f"Bluetooth turned {'on' if enabled else 'off'}"
        except Exception:
            pass
    return {"enabled": state, "changed": False, "ok": False}, "I clicked the Bluetooth switch but the state didn't change"


def windows_summary() -> str:
    return "; ".join(w["title"] for w in list_windows()[:10])
