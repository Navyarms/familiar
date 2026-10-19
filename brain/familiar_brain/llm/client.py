"""Streaming client for llama-server's OpenAI-compatible chat endpoint, with tool-call assembly."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string as produced by the model

    def args(self) -> dict:
        if not self.arguments.strip():
            return {}
        try:
            v = json.loads(self.arguments)
        except json.JSONDecodeError:
            # small models sometimes emit two objects or trailing junk; take the first object
            dec = json.JSONDecoder()
            v, _ = dec.raw_decode(self.arguments.strip())
        return v if isinstance(v, dict) else {}

    def openai(self) -> dict:
        return {"id": self.id, "type": "function", "function": {"name": self.name, "arguments": self.arguments or "{}"}}


@dataclass
class StreamStats:
    ttft_s: float | None = None
    total_s: float = 0.0
    prompt_tokens: int | None = None
    gen_tokens: int | None = None
    tok_s: float | None = None
    finish_reason: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LlmClient:
    def __init__(self, base_url: str, *, thinking: bool = False):
        self.base_url = base_url
        self.thinking = thinking
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(10, read=600))

    async def aclose(self) -> None:
        await self._client.aclose()

    async def warm(self, messages: list[dict], tools: list[dict] | None = None) -> float:
        """Process the prefix once so the server's prompt cache holds it. Returns seconds taken."""
        t0 = time.monotonic()
        body = {"messages": messages, "max_tokens": 1, "temperature": 0, "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": self.thinking}}
        if tools:
            body["tools"] = tools
        r = await self._client.post(f"{self.base_url}/v1/chat/completions", json=body)
        r.raise_for_status()
        dt = time.monotonic() - t0
        log.info("prefix warmed in %.2fs (%s prompt tokens)", dt, r.json().get("usage", {}).get("prompt_tokens"))
        return dt

    async def complete_json(self, messages: list[dict], schema: dict, *, max_tokens: int = 400, temperature: float = 0.1) -> dict:
        """Non-streamed completion constrained to a JSON schema (llama-server turns it into a grammar)."""
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "cache_prompt": True,
                "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "out", "schema": schema}}}
        r = await self._client.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=httpx.Timeout(10, read=300))
        if r.status_code >= 400:
            raise RuntimeError(f"json completion failed: {r.text[:200]}")
        text = (r.json()["choices"][0]["message"].get("content") or "").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            dec = json.JSONDecoder()
            obj, _ = dec.raw_decode(text[text.index("{"):])
            return obj

    async def ask_image(self, png: bytes, prompt: str, *, max_tokens: int = 220) -> str:
        """One non-streamed vision question (needs llama-server started with --mmproj)."""
        import base64

        b64 = base64.b64encode(png).decode("ascii")
        body = {"max_tokens": max_tokens, "temperature": 0.2, "cache_prompt": False,
                "chat_template_kwargs": {"enable_thinking": False},
                "messages": [{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    {"type": "text", "text": prompt}]}]}
        r = await self._client.post(f"{self.base_url}/v1/chat/completions", json=body, timeout=httpx.Timeout(10, read=300))
        if r.status_code >= 400:
            raise RuntimeError(f"vision request failed: {r.text[:200]}")
        return (r.json()["choices"][0]["message"].get("content") or "").strip()

    async def stream(self, messages: list[dict], *, max_tokens: int = 400, temperature: float = 0.7,
                     tools: list[dict] | None = None, stats: StreamStats | None = None,
                     tool_choice: str = "auto") -> AsyncIterator[str]:
        """Yields content deltas. Reasoning deltas (thinking) are skipped. Tool calls are assembled into `stats.tool_calls`."""
        stats = stats if stats is not None else StreamStats()
        body = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature, "stream": True,
                "cache_prompt": True, "stream_options": {"include_usage": True},
                "chat_template_kwargs": {"enable_thinking": self.thinking}}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice  # "auto" | "required"
            body["parallel_tool_calls"] = True
        t0 = time.monotonic()
        partial: dict[int, ToolCall] = {}
        dump = os.environ.get("FAMILIAR_DUMP_REQUESTS")  # dev: path of a JSON file that receives the last request body
        if dump:
            Path(dump).write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
        async with self._client.stream("POST", f"{self.base_url}/v1/chat/completions", json=body) as r:
            if r.status_code >= 400:
                err = (await r.aread()).decode(errors="replace")[:500]
                raise RuntimeError(f"llama-server {r.status_code}: {err}")
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
                    for tc in d.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        cur = partial.get(idx)
                        if cur is None:
                            cur = partial[idx] = ToolCall(id=tc.get("id") or f"call_{idx}", name="", arguments="")
                        if tc.get("id"):
                            cur.id = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            cur.name = fn["name"] if not cur.name else cur.name
                        if fn.get("arguments"):
                            cur.arguments += fn["arguments"]
                    content = d.get("content")
                    if content:
                        if stats.ttft_s is None:
                            stats.ttft_s = time.monotonic() - t0
                        yield content
        stats.tool_calls = [partial[i] for i in sorted(partial)]
        stats.total_s = time.monotonic() - t0
