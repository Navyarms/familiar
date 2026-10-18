"""Memory tools: remember / recall / forget on the Brain's MemoryStore (semantic when embeddings are loaded)."""

from __future__ import annotations

from .registry import P, ToolContext, tool


def store(ctx: ToolContext):
    m = ctx.extra.get("memory")
    if m is None:
        raise RuntimeError("memory is not available")
    return m


@tool("remember", group="memory", description="Save a fact about the user or their preferences for future conversations.",
      parameters=P(fact=("string", "One short sentence, e.g. 'The user's dog is named Biscuit'", True)),
      describe=lambda a: f"remember: {a.get('fact')}")
def remember(ctx: ToolContext, fact: str):
    _id, created = store(ctx).remember(fact, source="user")
    return {"id": _id, "new": created}, "I'll remember that" if created else "I already had that noted"


@tool("recall", group="memory", description="Look up what you remember about a topic (facts and past conversations).",
      parameters=P(query=("string", "Topic or keywords", True)))
def recall(ctx: ToolContext, query: str):
    s = store(ctx)
    hits = s.recall(query, limit=6, min_score=0.45) + s.recall_episodes(query)
    if not hits:
        return [], f"nothing remembered about {query}"
    return hits, "; ".join(h["text"] for h in hits[:5])


@tool("forget", group="memory", description="Delete remembered facts matching a topic.",
      parameters=P(query=("string", "Topic or keywords", True)), permission="confirm", risk="low",
      describe=lambda a: f"forget what I know about {a.get('query')}")
def forget(ctx: ToolContext, query: str):
    n = store(ctx).forget(query)
    return {"deleted": n}, f"forgot {n} thing{'s' if n != 1 else ''}" if n else "nothing to forget"
