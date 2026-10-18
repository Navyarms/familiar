"""System tools: apps, volume, media keys, brightness, lock/sleep/shutdown, time, machine info."""

from __future__ import annotations

import ctypes
import datetime as dt
import os
import platform
import subprocess
from pathlib import Path

import psutil

from .registry import P, ToolContext, tool

START_MENUS = [
    Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft/Windows/Start Menu/Programs",
]
ALIASES = {
    "calculator": "calc.exe", "calc": "calc.exe", "notepad": "notepad.exe", "paint": "mspaint.exe",
    "explorer": "explorer.exe", "file explorer": "explorer.exe", "files": "explorer.exe",
    "settings": "ms-settings:", "windows settings": "ms-settings:", "bluetooth settings": "ms-settings:bluetooth",
    "wifi settings": "ms-settings:network-wifi", "display settings": "ms-settings:display", "sound settings": "ms-settings:sound",
    "task manager": "taskmgr.exe", "terminal": "wt.exe", "command prompt": "cmd.exe", "powershell": "powershell.exe",
    "snipping tool": "ms-screenclip:", "camera": "microsoft.windows.camera:", "store": "ms-windows-store:",
    "control panel": "control.exe", "edge": "msedge.exe", "chrome": "chrome.exe", "firefox": "firefox.exe",
    "spotify": "spotify:", "steam": "steam://open/main", "discord": "discord:", "vs code": "code", "vscode": "code", "code": "code",
}
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _shortcuts() -> list[tuple[str, Path]]:
    out = []
    for base in START_MENUS:
        if base.is_dir():
            for p in base.rglob("*.lnk"):
                out.append((p.stem.lower(), p))
    return out


def resolve_app(name: str) -> tuple[str, str] | None:
    """Returns (how, target): how in {'alias', 'shortcut', 'exe'}."""
    q = name.strip().lower()
    if q in ALIASES:
        return "alias", ALIASES[q]
    cuts = _shortcuts()
    for stem, p in cuts:  # exact, then prefix, then substring, then token overlap
        if stem == q:
            return "shortcut", str(p)
    for stem, p in cuts:
        if stem.startswith(q):
            return "shortcut", str(p)
    for stem, p in cuts:
        if q in stem and "uninstall" not in stem:
            return "shortcut", str(p)
    qt = set(q.split())
    best = max(cuts, key=lambda sp: len(qt & set(sp[0].replace("-", " ").split())), default=None)
    if best and qt & set(best[0].replace("-", " ").split()):
        return "shortcut", str(best[1])
    for k, v in ALIASES.items():
        if q in k or k in q:
            return "alias", v
    return None


@tool("open_app", group="system", description="Open or launch an application by its name, e.g. 'Spotify', 'Calculator', 'Settings', 'Chrome'.",
      parameters=P(name=("string", "Application name as a user would say it", True)),
      describe=lambda a: f"open {a.get('name')}")
def open_app(ctx: ToolContext, name: str):
    r = resolve_app(name)
    if not r:
        return {"opened": False}, f"I couldn't find an app called {name}"
    how, target = r
    if target.endswith(".exe") and "\\" not in target:
        try:
            subprocess.Popen([target], creationflags=_NOWIN | getattr(subprocess, "DETACHED_PROCESS", 0), close_fds=True)
        except FileNotFoundError:
            os.startfile(target)  # let the shell resolve App Paths
    else:
        os.startfile(target)
    # A launch call succeeding does not mean the app is up (disabled apps, broken shortcuts). Wait for a window.
    import time as _t

    from .windows import find_window

    for _ in range(12):
        _t.sleep(0.25)
        w = find_window(name) or find_window(Path(target).stem if "\\" in target else target.split(":")[0])
        if w:
            return {"opened": True, "via": how, "target": target, "window": w["title"]}, f"opened {name} ({w['title']})"
    return {"opened": None, "via": how, "target": target}, f"I launched {name}, but no window appeared yet; it may be disabled or still starting"


@tool("close_app", group="system", description="Close an application by name (asks it to quit; does not force-kill).",
      parameters=P(name=("string", "Application or process name, e.g. 'Spotify'", True)), permission="confirm", risk="medium",
      describe=lambda a: f"close {a.get('name')}")
def close_app(ctx: ToolContext, name: str):
    from .windows import close_windows_of

    q = name.lower().replace(".exe", "")
    n = close_windows_of(q)
    if n:
        return {"closed_windows": n}, f"asked {name} to close"
    hits = [p for p in psutil.process_iter(["name"]) if q in (p.info["name"] or "").lower()]
    for p in hits:
        try:
            p.terminate()
        except Exception:
            pass
    return {"terminated": len(hits)}, (f"closed {name}" if hits else f"{name} isn't running")


def _endpoint():
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    return dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)


@tool("set_volume", group="system", description="Set the system output volume, 0 to 100.",
      parameters=P(level=("integer", "0 (silent) to 100 (max)", True)), describe=lambda a: f"set the volume to {a.get('level')}%")
def set_volume(ctx: ToolContext, level: int):
    level = max(0, min(100, int(level)))
    ep = _endpoint()
    ep.SetMasterVolumeLevelScalar(level / 100, None)
    if level > 0 and ep.GetMute():
        ep.SetMute(0, None)
    return {"level": level}, f"volume set to {level}%"


@tool("get_volume", group="system", description="Read the current system volume and mute state.")
def get_volume(ctx: ToolContext):
    ep = _endpoint()
    lvl, muted = round(ep.GetMasterVolumeLevelScalar() * 100), bool(ep.GetMute())
    return {"level": lvl, "muted": muted}, f"volume is {lvl}%{' and muted' if muted else ''}"


@tool("set_mute", group="system", description="Mute or unmute the system audio.",
      parameters=P(muted=("boolean", "true to mute, false to unmute", True)), describe=lambda a: ("mute" if a.get("muted") else "unmute") + " the audio")
def set_mute(ctx: ToolContext, muted: bool):
    _endpoint().SetMute(1 if muted else 0, None)
    return {"muted": muted}, "muted" if muted else "unmuted"


_VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "volume_up": 0xAF, "volume_down": 0xAE, "volume_mute": 0xAD}


@tool("media_control", group="system", description="Control whatever is playing media (music, video) with the system media keys.",
      parameters=P(action=("string", "play_pause | next | previous | stop", ["play_pause", "next", "previous", "stop"], True)),
      describe=lambda a: f"press media {a.get('action')}")
def media_control(ctx: ToolContext, action: str):
    vk = _VK[action]
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
    ctypes.windll.user32.keybd_event(vk, 0, 2, 0)
    return {"action": action}, f"media {action.replace('_', '/')}"


@tool("set_brightness", group="system", description="Set the screen brightness 0-100 (laptops and some monitors only).",
      parameters=P(level=("integer", "0-100", True)), describe=lambda a: f"set brightness to {a.get('level')}%")
def set_brightness(ctx: ToolContext, level: int):
    level = max(0, min(100, int(level)))
    cmd = ["powershell", "-NoProfile", "-Command",
           f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods).WmiSetBrightness(1,{level})"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=_NOWIN)
    if r.returncode != 0 or "Exception" in (r.stderr + r.stdout):
        return {"ok": False}, "this display doesn't support software brightness control"
    return {"level": level}, f"brightness set to {level}%"


@tool("lock_pc", group="system", description="Lock the computer (Windows lock screen).", permission="auto", risk="low",
      describe=lambda a: "lock the PC")
def lock_pc(ctx: ToolContext):
    ctypes.windll.user32.LockWorkStation()
    return {"locked": True}, "locked"


@tool("sleep_pc", group="system", description="Put the computer to sleep.", permission="confirm", risk="high", describe=lambda a: "put the PC to sleep")
def sleep_pc(ctx: ToolContext):
    subprocess.Popen(["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], creationflags=_NOWIN)
    return {"sleeping": True}, "going to sleep"


@tool("shutdown_pc", group="system", description="Shut down or restart the computer after a short countdown (can be cancelled with cancel_shutdown).",
      parameters=P(restart=("boolean", "true = restart, false = shut down", False), delay_s=("integer", "seconds before it happens, default 30", False)),
      permission="confirm", risk="high", describe=lambda a: ("restart" if a.get("restart") else "shut down") + f" the PC in {a.get('delay_s', 30)} seconds")
def shutdown_pc(ctx: ToolContext, restart: bool = False, delay_s: int = 30):
    subprocess.run(["shutdown", "/r" if restart else "/s", "/t", str(max(5, int(delay_s)))], creationflags=_NOWIN)
    return {"scheduled": True}, f"{'restart' if restart else 'shutdown'} in {delay_s} seconds; say cancel to stop it"


@tool("cancel_shutdown", group="system", description="Cancel a pending shutdown or restart.", describe=lambda a: "cancel the shutdown")
def cancel_shutdown(ctx: ToolContext):
    r = subprocess.run(["shutdown", "/a"], capture_output=True, text=True, creationflags=_NOWIN)
    return {"cancelled": r.returncode == 0}, "shutdown cancelled" if r.returncode == 0 else "there was no shutdown pending"


@tool("get_datetime", group="system", description="Current local date, time and weekday. Use this whenever the user asks what time or day it is.")
def get_datetime(ctx: ToolContext):
    now = dt.datetime.now()
    s = now.strftime("%A, %B %d %Y, %I:%M %p").replace(" 0", " ")
    return {"iso": now.isoformat(timespec="minutes"), "text": s}, s


@tool("system_info", group="system", description="Battery, CPU load, memory use, OS version and uptime of this PC.")
def system_info(ctx: ToolContext):
    bat = psutil.sensors_battery()
    vm = psutil.virtual_memory()
    up = dt.timedelta(seconds=int(psutil.boot_time() and (dt.datetime.now().timestamp() - psutil.boot_time())))
    info = {"os": platform.platform(), "cpu_percent": psutil.cpu_percent(interval=0.3), "ram_percent": vm.percent,
            "ram_total_gb": round(vm.total / 2**30, 1), "battery_percent": bat.percent if bat else None,
            "plugged_in": bat.power_plugged if bat else None, "uptime": str(up)}
    s = f"CPU {info['cpu_percent']}%, RAM {vm.percent}% of {info['ram_total_gb']} GB"
    s += f", battery {bat.percent}%{' charging' if bat.power_plugged else ''}" if bat else ", no battery (desktop)"
    return info, s
