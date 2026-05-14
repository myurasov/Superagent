# Skill-anti-pattern Catalogue

<!-- Migrated from `procedures.md § 39`. Citation form: `contracts/anti-patterns.md`. -->

Implements superagent/docs/_internal/perf-improvement-ideas.md § "Anti-patterns to flag in skills".

**Source of truth**: `superagent/rules/anti-patterns.yaml` (the regex catalogue, severities, mitigations). The `superagent/rules/` directory holds machine-readable rule files; this section is the human-readable summary that points at them.

**Backed by**: `tools/anti_patterns.py` (loads the YAML at import time and exposes `scan_file` / `scan_dir` / `load_rules` to skills, the Supertailor, and `doctor`).

**Catalogue summary** (see the YAML for full regex sources + flags):

| ID | Severity | Description | Mitigation |
|---|---|---|---|
| AP-1 | warning | Blanket 4-file read (info + status + history + rolodex). | Specify which sections to read. |
| AP-2 | warning | Unfiltered "all open tasks" read. | Always include the filter (related_domain / project / asset). |
| AP-3 | info | Whole-workspace grep without scope. | Scope to a Domain / Project folder. |
| AP-4 | info | Sequential "run X then Y then Z" that may be parallelizable. | Use single-message tool-call batching. |
| AP-5 | info | Full email-thread pull without checking interaction-log first. | Consult local mirror + Sources cache first. |
| AP-6 | info | Briefing regen without cache check. (DORMANT — no skill writes the cache today; see `contracts/briefing-cache.md` § Status. Re-enable once `put`/`get` is wired into the producing skills.) | Once activated: call `tools/briefing_cache.py get` first. |
| AP-7 | warning | Unbounded read of long doc (AGENTS.md or contracts/*). | Use Grep + `Read --offset --limit` against documented ranges. |
| AP-8 | warning | Manifest-bypass: "read every skill markdown". | Read `skills/_manifest.yaml` first. |
| AP-9 | info | Load-then-extract: large file loaded, single fact extracted. | Use Grep / FTS5 first. |
| AP-10 | info | Entity-side payment-history append without account-side `accounts-index.transactions[]` mirror. | Per `contracts/payment-confirmations.md § 4 step 3a`, append the symmetric account-side row in the same skill section. |

**Adding a pattern**: append a row to `rules/anti-patterns.yaml` with the next AP-N id (never renumber existing rows), then add a row to the table above. Bump `last_updated` in the YAML.

**User overlay**: `workspace/_custom/rules/anti-patterns.yaml` (same schema). The scanner concatenates framework + user rules, in that order. Use this to add personal anti-patterns without touching framework code.

**Scanning**: `tools/anti_patterns.py` walks every skill markdown, applies the regex patterns, prints findings. The Supertailor's strategic pass runs it; `doctor` runs it (when `config.preferences.anti_patterns.scan_skills: true`).

**Strict mode** (`--strict`): exit non-zero when any `warning` hit found. Suitable for CI.
