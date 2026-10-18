"""Tool groups. Importing this package registers every tool in REGISTRY."""

from . import clipboard, files, memory, shell, system, timers, weather, web, windows  # noqa: F401
from .registry import REGISTRY, ToolContext, ToolRegistry, ToolSpec, tool  # noqa: F401
