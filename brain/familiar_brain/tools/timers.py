"""Timers and reminders. In-memory for now (lost on restart); they fire through ctx.announce so Wisp says them aloud."""

from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import re

from .registry import P, ToolContext, tool

_timers: dict[int, dict] = {}
_ids = itertools.count(1)


def _fmt(seconds: float) -> str:
    s = int(round(seconds))
    if s < 60:
        return f"{s} second{'s' if s != 1 else ''}"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}" + (f" {s} seconds" if s else "")
    h, m = divmod(m, 60)
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")


async def _fire(ctx: ToolContext, tid: int) -> None:
    t = _timers.get(tid)
    if not t:
        return
    try:
        await asyncio.sleep(max(0.0, (t["at"] - dt.datetime.now()).total_seconds()))
        if tid in _timers:
            msg = f"Reminder: {t['label']}" if t["kind"] == "reminder" else (f"Your {t['label']} timer is done." if t["label"] else "Your timer is done.")
            await ctx.announce(msg)
    except asyncio.CancelledError:
        pass
    finally:
        _timers.pop(tid, None)


def _parse_when(when: str) -> dt.datetime | None:
    """'in 10 minutes', 'at 3:30 pm', 'at 15:30', 'tomorrow at 9', ISO."""
    w = when.strip().lower()
    now = dt.datetime.now()
    m = re.match(r"in\s+(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr)s?", w)
    if m:
        n, unit = float(m.group(1)), m.group(2)
        mult = 1 if unit.startswith("s") else 60 if unit.startswith("m") else 3600
        return now + dt.timedelta(seconds=n * mult)
    day = now.date()
    if "tomorrow" in w:
        day = day + dt.timedelta(days=1)
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", w)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if ap == "pm" and h < 12:
            h += 12
        if ap == "am" and h == 12:
            h = 0
        t = dt.datetime.combine(day, dt.time(h % 24, mi))
        if t <= now and "tomorrow" not in w:
            t += dt.timedelta(days=1)
        return t
    try:
        return dt.datetime.fromisoformat(when)
    except ValueError:
        return None


@tool("set_timer", group="timers", description="Start a countdown timer; Wisp will say when it is done.",
      parameters=P(seconds=("integer", "Duration in seconds (10 minutes = 600)", True), label=("string", "What it's for, e.g. 'pasta'", False)),
      describe=lambda a: f"set a {a.get('seconds')} second timer")
def set_timer(ctx: ToolContext, seconds: int, label: str = ""):
    seconds = max(1, int(seconds))
    if re.fullmatch(r"\s*\d+\s*(second|sec|minute|min|hour|hr)s?\s*", label or ""):
        label = ""  # "5 seconds" is a duration, not a name
    tid = next(_ids)
    _timers[tid] = {"id": tid, "kind": "timer", "label": label, "at": dt.datetime.now() + dt.timedelta(seconds=seconds)}
    _timers[tid]["task"] = ctx.loop.create_task(_fire(ctx, tid))
    return {"id": tid, "seconds": seconds}, f"timer set for {_fmt(seconds)}" + (f" ({label})" if label else "")


@tool("set_reminder", group="timers", description="Remind the user of something at a time ('in 20 minutes', 'at 3:30 pm', 'tomorrow at 9').",
      parameters=P(when=("string", "When, in natural words or ISO time", True), text=("string", "What to say", True)),
      describe=lambda a: f"remind you {a.get('when')}: {a.get('text')}")
def set_reminder(ctx: ToolContext, when: str, text: str):
    at = _parse_when(when)
    if not at:
        return {"ok": False}, f"I didn't understand the time '{when}'"
    tid = next(_ids)
    _timers[tid] = {"id": tid, "kind": "reminder", "label": text, "at": at}
    _timers[tid]["task"] = ctx.loop.create_task(_fire(ctx, tid))
    delta = (at - dt.datetime.now()).total_seconds()
    return {"id": tid, "at": at.isoformat(timespec="minutes")}, f"reminder set for {at.strftime('%I:%M %p').lstrip('0')} (in {_fmt(delta)})"


@tool("timers", group="timers", description="List running timers and reminders, or cancel one by id (0 = all).",
      parameters=P(action=("string", "list | cancel", ["list", "cancel"], True), id=("integer", "timer id for cancel; 0 = all", False)),
      describe=lambda a: "cancel the timer" if a.get("action") == "cancel" else "list timers")
def timers(ctx: ToolContext, action: str, id: int = 0):
    now = dt.datetime.now()
    if action == "list":
        items = [{"id": t["id"], "kind": t["kind"], "label": t["label"], "remaining_s": int((t["at"] - now).total_seconds())}
                 for t in _timers.values()]
        if not items:
            return [], "no timers running"
        return items, "; ".join(f"{i['kind']} {i['id']}{' ' + i['label'] if i['label'] else ''}: {_fmt(i['remaining_s'])} left" for i in items)
    ids = list(_timers) if not id else [id]
    n = 0
    for tid in ids:
        t = _timers.pop(tid, None)
        if t:
            t["task"].cancel()
            n += 1
    return {"cancelled": n}, f"cancelled {n} timer{'s' if n != 1 else ''}" if n else "no such timer"
