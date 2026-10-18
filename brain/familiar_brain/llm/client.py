"""Streaming client for llama-server's OpenAI-compatible chat endpoint."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import httpx

log = logging.getLogger(__name__)


@dataclass
class StreamStats:
    ttft_s: float | None = None
    total_s: float = 0.0
    prompt_tokens: int | None = None
    gen_tokens: int | None = None
    tok_s: float | None = None
    finish_reason: str | None = None
    tool_calls: list[dict] = field(default_factory=list)


class LlmClient:
    def __init__(self, base_url: str, *, thinking: bool = False):
        self.base_url = base_url
        self.thinking = thinking
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=600))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warm(self, messages: list[dict]) -> float:
        """Process the prefix once so the server's prompt cache holds it. Returns seconds taken."""
        t0 = time.monotonic()
        body = {"messages": messages, "max_tokens": 1, "temperature": 0, "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": self.thinking}}
        r = await self._client.post(f"{self.base_url}/v1/chat/completions", json=body)
        r.raise_for_status()
        dt = time.monotonic() - t0
        log.info("prefix warmed in %.2fs (%s prompt tokens)", dt, r.json().get("usage", {}).get("prompt_tokens"))
        return dt

    async def stream(self, messages: list[dict], *, max_tokens: int = 400, temperature: float = 0.7,
                     tools: list[dict] | None = None, stats: StreamStats | None = None) -> AsyncIterator[str]:
        """Yields content deltas. Reasoning deltas (thinking) are skipped. Fills `stats` if given."""
        stats = stats if stats is not None else StreamStats()
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": True,
                "cache_prompt": True, "stream_options": {"include_usage": True},
                "chat_template_kwargs": {"enable_thinking": self.thinking}}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        t0 = time.monotonic()
        async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload.strip() == "[DONE]":
                    break
                ev = json.loads(payload)
                if "timings" in ev:
                    t = ev["timings"]
                    stats.prompt_tokens = t.get("prompt_n")
                    stats.gen_tokens = t.get("predicted_n")
                    stats.tok_s = t.get("predicted_per_second")
                for ch in ev.get("choices", []):
                    d = ch.get("delta", {})
                    if ch.get("finish_reason"):
                        stats.finish_reason = ch["finish_reason"]
                    if d.get("tool_calls"):
                        stats.tool_calls.extend(d["tool_calls"])
                    content = d.get("content")
                    if content:
                        if stats.ttft_s is None:
                            stats.ttft_s = time.monotonic() - t0
                        yield content
        stats.total_s = time.monotonic() - t0
