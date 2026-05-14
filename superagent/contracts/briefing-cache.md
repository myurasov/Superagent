# Briefing Cache Contract

<!-- Migrated from `procedures.md § 32`. Citation form: `contracts/briefing-cache.md`. -->

> **Status: infrastructure-only.** `tools/briefing_cache.py` ships (`get` / `put` / `list` / `evict` all work and are covered by tests), and `_memory/_artifacts/<skill>/<key>.md` is created lazily by `put`. **No production skill currently calls `put` after rendering**, so the cache stays empty under normal use. AGENTS.md no longer instructs the agent to consult the cache. To activate this contract: wire `put(workspace, "<skill>", key, body, input_paths=[...])` into `daily-update`, `weekly-review`, `monthly-review` (and any other re-readable-output skill), and add a `get(...)` short-circuit at the top of the same skill. Until then, treat the rest of this contract as a spec.

Implements superagent/docs/_internal/perf-improvement-ideas.md QW-5 + MI-5. Backed by `_memory/_artifacts/<skill>/<key>.{md,meta.yaml}` + `tools/briefing_cache.py`.

**Pattern (when wired up)**: any skill whose output is a candidate for re-read within the same day (briefings, summaries, dashboard renders) writes through the cache.

**Cache write** (after producing fresh content):
```python
from superagent.tools.briefing_cache import put
put(workspace, "daily-update", "2026-04-28",
    body=rendered_body, input_paths=[bills_path, todo_path, ...],
    ttl_minutes=720)
```

**Cache read** (before regenerating):
```python
from superagent.tools.briefing_cache import get
result = get(workspace, "daily-update", "2026-04-28",
             input_paths=[bills_path, todo_path, ...])
if result is not None:
    # cache hit
    body = result["body"]
```

**Invalidation**: TTL (per-skill, default 720 min from `config.preferences.briefing_cache.ttl_minutes`) + input-hash (sha256 over input file paths' size + mtime, if `invalidate_on_source_mtime: true`). Force refresh by passing `--refresh` or by calling `put` again.

**Eviction**: `tools/briefing_cache.py evict --skill <name> --older-than-minutes N` clears stale.
