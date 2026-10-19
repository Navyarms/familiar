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
    s = store(ctx)
    _id, created = s.remember(fact, source="user")
    replaced = getattr(s, "last_replaced", None)
    if replaced:
        return {"id": _id, "new": True, "replaced": replaced}, f"Updated: it replaces “{replaced.rstrip('.')}”"
    return {"id": _id, "new": created}, "I'll remember that" if created else "I already had that noted"


@tool("recall", group="memory", description="Look up what you remember about a topic, or everything you know about the user.",
      parameters=P(query=("string", "Topic or keywords; 'the user' for everything known about them", True)))
def recall(ctx: ToolContext, query: str):
    s = store(ctx)
    generic = query.strip().lower() in {"", "me", "user", "the user", "about me", "about the user", "everything", "all"}
    hits = [] if generic else s.recall(query, limit=6, min_score=0.45) + s.recall_episodes(query)
    if not hits:
        # Generic question, or nothing close enough: fall back to the most recent facts so "what do you know
        # about me?" never comes back empty while there is something remembered.
        hits = [i for i in s.list_items(limit=12) if i["kind"] == "fact"][:10]
    if not hits:
        return [], f"nothing remembered about {query}"
    return hits, "; ".join(h["text"] for h in hits[:8])


@tool("forget", group="memory", description="Delete remembered facts matching a topic.",
      parameters=P(query=("string", "Topic or keywords", True)), permission="confirm", risk="low",
      describe=lambda a: f"forget what I know about {a.get('query')}")
def forget(ctx: ToolContext, query: str):
    n = store(ctx).forget(query)
    return {"deleted": n}, f"forgot {n} thing{'s' if n != 1 else ''}" if n else "nothing to forget"
