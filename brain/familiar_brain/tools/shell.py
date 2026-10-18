"""Shell tool: blocked by default; when enabled it always asks for confirmation."""

from __future__ import annotations

import subprocess

from .registry import P, ToolContext, tool


@tool("run_command", group="shell", description="Run a PowerShell command and return its output. Only for explicit user requests.",
      parameters=P(command=("string", "PowerShell command line", True)), permission="blocked", risk="high",
      describe=lambda a: f"run the command: {a.get('command')}", timeout_s=60)
def run_command(ctx: ToolContext, command: str):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True,
                       timeout=55, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    out = (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
    return {"exit_code": r.returncode, "output": out[:6000]}, (out[:200] or f"exit code {r.returncode}")
