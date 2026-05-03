# Briefing Cache Contract

<!-- Migrated from `procedures.md § 32`. Citation form: `contracts/briefing-cache.md`. -->

Implements perf-improvement-ideas.md QW-5 + MI-5. Backed by `_memory/_artifacts/<skill>/<key>.{md,meta.yaml}` + `tools/briefing_cache.py`.

**Pattern**: any skill whose output is a candidate for re-read within the same day (briefings, summaries, dashboard renders) writes through the cache.

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
