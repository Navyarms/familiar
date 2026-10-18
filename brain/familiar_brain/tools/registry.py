"""Tool registry: declares tools once, derives the OpenAI tool schema, and resolves permissions.

A tool is an async or sync function registered with @tool(...). It receives a ToolContext (brain
services: paths, settings, event sink, announce) and keyword arguments validated against its JSON
schema. Permission levels: auto (run), confirm (ask the user first), blocked (never run).
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

Permission = str  # "auto" | "confirm" | "blocked"
Risk = str  # "low" | "medium" | "high"


@dataclass
class ToolContext:
    paths: Any
    settings: Any
    send: Callable[[dict], Awaitable[None]]
    announce: Callable[[str], Awaitable[None]]  # speak something unprompted (timers)
    loop: asyncio.AbstractEventLoop
    kill: asyncio.Event  # set by the kill switch; long tools must check it
    extra: dict = field(default_factory=dict)  # e.g. persona, memory db


@dataclass
class ToolSpec:
    name: str
    group: str
    description: str
    parameters: dict  # JSON schema (object)
    handler: Callable[..., Any]
    permission: Permission = "auto"
    risk: Risk = "low"
    describe: Callable[[dict], str] | None = None  # human sentence for the confirm card
    timeout_s: float = 30.0
    narrate: bool = False  # speak the description when the tool starts (slow tools, multi-step automation)
    permission_for: Callable[[dict], Permission | None] | None = None  # per-call override, e.g. close actions need confirm

    def openai(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    def human(self, args: dict) -> str:
        try:
            return self.describe(args) if self.describe else f"{self.name.replace('_', ' ')} {args}"
        except Exception:
            return f"{self.name} {args}"


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, ToolSpec] = {}
        self.overrides: dict[str, Permission] = {}  # from settings.tool_permissions
        self.disabled_groups: set[str] = set()

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self.tools:
            raise ValueError(f"duplicate tool {spec.name}")
        self.tools[spec.name] = spec

    def configure(self, settings) -> None:
        self.overrides = dict(getattr(settings, "tool_permissions", {}) or {})
        self.disabled_groups = set()
        if not getattr(settings, "web_enabled", True):
            self.disabled_groups.add("web")

    def permission(self, name: str, args: dict | None = None) -> Permission:
        spec = self.tools.get(name)
        if not spec:
            return "blocked"
        if spec.group in self.disabled_groups:
            return "blocked"
        p = self.overrides.get(name) or self.overrides.get(f"group:{spec.group}") or spec.permission
        p = p if p in ("auto", "confirm", "blocked") else spec.permission
        if args is not None and spec.permission_for and p == "auto":
            p = spec.permission_for(args) or p
        return p

    def available(self) -> list[ToolSpec]:
        """Tools exposed to the model (blocked ones are hidden so it does not keep trying them)."""
        return [t for t in self.tools.values() if self.permission(t.name) != "blocked"]

    def openai_tools(self) -> list[dict]:
        return [t.openai() for t in self.available()]

    def info(self) -> list[dict]:
        return [{"name": t.name, "group": t.group, "description": t.description, "permission": self.permission(t.name),
                 "default_permission": t.permission, "risk": t.risk} for t in self.tools.values()]

    async def run(self, name: str, args: dict, ctx: ToolContext) -> dict:
        """Execute. Returns {"ok": bool, "result": any, "summary": str, "elapsed_s": float}."""
        spec = self.tools[name]
        t0 = time.monotonic()
        try:
            args = _coerce(spec.parameters, args)
            if inspect.iscoroutinefunction(spec.handler):
                res = await asyncio.wait_for(spec.handler(ctx, **args), timeout=spec.timeout_s)
            else:
                res = await asyncio.wait_for(asyncio.to_thread(spec.handler, ctx, **args), timeout=spec.timeout_s)
            if isinstance(res, tuple) and len(res) == 2:
                result, summary = res
            else:
                result, summary = res, (res if isinstance(res, str) else "done")
            # a tool may report a soft failure in its result dict (not found, nothing opened) without raising
            ok = not (isinstance(result, dict) and any(result.get(k) is False for k in ("ok", "found", "opened")))
            return {"ok": ok, "result": result, "summary": str(summary)[:300], "elapsed_s": round(time.monotonic() - t0, 3)}
        except TimeoutError:
            return {"ok": False, "result": None, "summary": f"{name} timed out after {spec.timeout_s:.0f}s", "elapsed_s": round(time.monotonic() - t0, 3)}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("tool %s failed: %s", name, e, exc_info=True)
            return {"ok": False, "result": None, "summary": f"{name} failed: {str(e)[:200]}", "elapsed_s": round(time.monotonic() - t0, 3)}


def _coerce(schema: dict, args: dict) -> dict:
    """Small models send numbers as strings and vice versa; coerce to the declared types and drop unknown keys."""
    props = schema.get("properties", {})
    out = {}
    for k, v in (args or {}).items():
        if k not in props:
            continue
        t = props[k].get("type")
        try:
            if t == "integer" and not isinstance(v, bool):
                v = int(float(v))
            elif t == "number":
                v = float(v)
            elif t == "boolean" and isinstance(v, str):
                v = v.strip().lower() in ("true", "1", "yes", "on")
            elif t == "string" and not isinstance(v, str):
                v = str(v)
            elif t == "array" and isinstance(v, str):
                v = [s.strip() for s in v.split(",") if s.strip()]
        except (TypeError, ValueError):
            pass
        out[k] = v
    for k in schema.get("required", []):
        if k not in out:
            raise ValueError(f"missing required argument '{k}'")
    return out


REGISTRY = ToolRegistry()


def tool(name: str, *, group: str, description: str, parameters: dict | None = None, permission: Permission = "auto",
         risk: Risk = "low", describe: Callable[[dict], str] | None = None, timeout_s: float = 30.0, narrate: bool = False,
         permission_for: Callable[[dict], Permission | None] | None = None):
    """Decorator. `parameters` is the JSON schema for the arguments (object); default = no arguments."""

    def deco(fn):
        REGISTRY.register(ToolSpec(name=name, group=group, description=description,
                                   parameters=parameters or {"type": "object", "properties": {}},
                                   handler=fn, permission=permission, risk=risk, describe=describe, timeout_s=timeout_s,
                                   narrate=narrate, permission_for=permission_for))
        return fn

    return deco


def P(**props) -> dict:
    """Shorthand for an object schema. P(name=("string", "App name", True), level=("integer", "0-100", True))."""
    properties, required = {}, []
    for k, v in props.items():
        typ, desc, *rest = v
        prop = {"type": typ, "description": desc}
        if rest and isinstance(rest[0], list):
            prop["enum"] = rest[0]
        req = rest[-1] if rest and isinstance(rest[-1], bool) else False
        properties[k] = prop
        if req:
            required.append(k)
    return {"type": "object", "properties": properties, "required": required}
