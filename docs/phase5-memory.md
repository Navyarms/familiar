# Phase 5 — Memory (facts, episodes, embeddings, retrieval, idle consolidation, viewer)

Status 2026-10-18: working from source. Exit criterion ("tell it your dog's name, restart, it remembers")
passes across two separate brain processes.

## What exists

```
brain/familiar_brain/memory/
  embed.py    bge-small-en-v1.5 int8 ONNX (34 MB, pinned in the manifest) via onnxruntime + tokenizers;
              CLS pooling, L2-normalized 384-d vectors; ~2.5 ms per sentence on CPU
  store.py    SQLite %LOCALAPPDATA%\Familiar\memory.sqlite:
                facts(text, created, source, embedding, last_used, uses)  — atomic statements
                episodes(started, ended, summary, turns, embedding)       — conversation summaries
                turns(ts, role, text, summarized)                          — raw turns waiting to be summarized
                preferences(key, value)
              remember() dedupes at cosine ≥ 0.92; recall() / recall_episodes() semantic (keyword fallback
              without the embedder); context_for() picks ≤6 relevant facts (≥0.55) + 3 most recent + ≤2 related
              episodes and bumps last_used/uses; forget() by similarity ≥ 0.6; list/delete/delete_all for the UI;
              upgrades the Phase 3 facts table in place and backfills embeddings
  extract.py  extract_and_summarize(): one JSON-schema-constrained llama-server call over the pending turns ->
              {summary, facts[]}; filters assistant-side lines, dedupes against known facts, stores an episode,
              marks turns summarized
```

Brain: after the engines are up it loads the embedder and re-opens the store with it; every completed
turn is appended to `turns`; a background loop runs consolidation when the conversation has been idle
for `memory_idle_s` (120 s) with at least `memory_min_turns` (2) new turns, or at boot when a previous
run left turns behind. Retrieval happens per user turn: `context_for(text)` is appended to the **user**
message as a short private note (the system prompt stays static so the prompt cache holds).

Tools: `remember` (now dedupes), `recall` (facts + episodes), `forget` (confirm; similarity-based).

Protocol: `memory.list {query, limit}` / `memory.delete {kind, id | all}` → `memory.items {items, counts}`.
Settings → **Memory**: counts, search box (semantic), list with Delete per item, Forget all (confirm).
`memory_enabled: false` in settings.json turns the whole thing off.

## Restart test (dev machine, CPU, T1)

Session A (brain process 1): "My dog's name is Biscuit, and I just moved to Austin." → the model called
`remember` twice. Process terminated. Session B (fresh process, 40 s idle): the loop summarized the 4
pending turns into episode 1 (0 new facts: both were already stored), then "What's my dog's name?" →
"Your dog's name is Biscuit." and "Where do I live these days?" → "You're in Austin these days.", both
answered from the retrieved note with no tool call, first token ~0.9 s.

## Measured

| Step | Time |
|---|---|
| Embedder load | ~0.3 s |
| Embed 4 sentences | 10 ms |
| context_for() on a few hundred facts | < 20 ms (NumPy dot product over all rows) |
| Consolidation call (4 turns, JSON schema, 4B on CPU) | ~13 s |

## Decisions

- **Retrieval goes on the user turn, not the system prompt.** Putting facts in the system prompt
  invalidated the prompt cache after every `remember` (15 s re-prefill, found in Phase 3).
- **Two paths into memory**: the model's explicit `remember` during the turn, and the idle extractor
  for things the model did not flag. Dedupe makes the overlap harmless.
- **JSON schema output** for the extractor instead of free text: the 4B model stays in format.
- **Thresholds**: bge-small puts unrelated sentences around 0.45 cosine, so facts need ≥ 0.55 to be
  injected and ≥ 0.6 to be forgotten; a near-duplicate is ≥ 0.92.
- **Time note only when the message mentions time words** (the model once read the hidden note aloud).
- Brute-force vector search is fine to a few thousand facts; no vector index needed yet.

## Phase 5 checklist (PLAN.md)

- [x] SQLite schema, embedding model download, retrieval into prompt
- [x] Idle-time summarization and fact extraction
- [x] Settings → Memory viewer with delete
- [x] Exit: tell it your dog's name, restart the PC, it remembers (verified across brain processes; a full
      PC restart changes nothing since everything is on disk)

## Known gaps

- Episodes are not shown to the model unless semantically related (≥ 0.5); there is no "what did we talk
  about yesterday" tool beyond `recall`.
- Consolidation holds llama-server's single slot for ~10–15 s; a user turn during that time waits.
  The loop only starts it after idle, so it is rarely noticeable.
- Facts never expire; `uses`/`last_used` are recorded for a future ranking/aging pass.
- No export/import of memory yet (Phase 6 settings work).
