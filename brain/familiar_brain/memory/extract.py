"""Idle-time memory work: summarize the turns since the last episode and extract durable facts about the user.

Runs through llama-server with a JSON schema, so the small model cannot drift out of format. Triggered by the
Brain when the conversation has been quiet for `memory_idle_s` (and at least `memory_min_turns` happened),
and at boot if unsummarized turns were left behind by a previous run.
"""

from __future__ import annotations

import logging

from ..llm import LlmClient
from .store import MemoryStore

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 sentences: what the user wanted and what happened"},
        "facts": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                  "description": "durable facts about the user worth remembering, each a short sentence; empty if none"},
    },
    "required": ["summary", "facts"],
}

PROMPT = (
    "You maintain the long-term memory of a desktop assistant. Read the conversation and return JSON with:\n"
    "- summary: two or three plain sentences about what the user wanted and what happened.\n"
    "- facts: durable things about the USER worth remembering later: their name, people and pets, preferences, "
    "places, routines, work, projects, devices, dislikes. Write each as a short third-person sentence starting with "
    "'The user'. Do NOT include one-off requests (opening an app, a timer), the assistant's own words, the current "
    "time, or anything already in the known facts list. Return an empty facts array if nothing qualifies."
)


def _transcript(turns: list[dict]) -> str:
    lines = []
    for t in turns:
        who = "User" if t["role"] == "user" else "Assistant"
        lines.append(f"{who}: {t['text'][:600]}")
    return "\n".join(lines)[-6000:]


async def extract_and_summarize(llm: LlmClient, store: MemoryStore, turns: list[dict]) -> dict:
    """Returns {"episode_id", "summary", "new_facts": [...], "skipped": int}."""
    if not turns:
        return {"episode_id": None, "summary": "", "new_facts": [], "skipped": 0}
    known = store.recent(30)
    messages = [
        {"role": "system", "content": PROMPT},
        {"role": "user", "content": ("Known facts (do not repeat):\n" + "\n".join(f"- {k}" for k in known) + "\n\n" if known else "")
                                    + "Conversation:\n" + _transcript(turns)},
    ]
    out = await llm.complete_json(messages, SCHEMA, max_tokens=400)
    summary = " ".join(str(out.get("summary", "")).split())[:800]
    facts = [str(f).strip() for f in out.get("facts", []) if isinstance(f, str) and len(f.strip()) > 8][:8]
    new_facts, skipped = [], 0
    for f in facts:
        low = f.lower()
        if any(w in low for w in ("assistant", "wisp said", "timer was set", "opened ")):
            skipped += 1
            continue
        _id, created = store.remember(f, source="extracted")
        if created:
            new_facts.append(f)
        else:
            skipped += 1
    ids = [t["id"] for t in turns]
    episode_id = None
    if summary:
        episode_id = store.add_episode(summary, ids, turns[0]["ts"], turns[-1]["ts"])
    else:
        store.mark_summarized(ids)
    log.info("memory: episode %s, %d new facts (%d skipped) from %d turns", episode_id, len(new_facts), skipped, len(turns))
    return {"episode_id": episode_id, "summary": summary, "new_facts": new_facts, "skipped": skipped}
