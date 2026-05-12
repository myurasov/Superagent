---
name: superagent-inbox-triage
description: >-
  Walk every file in `Inbox/`, classify it by extension + filename, propose
  a destination under `Sources/<category>/` (the user can always override;
  layout under Sources/ is user-defined per `contracts/sources.md`), and
  ask the user per file: file / discard / leave / defer. Records every
  decision in
  `Inbox/_processed.yaml` so the agent learns the user's filing patterns.
triggers:
  - inbox triage
  - drain inbox
  - file inbox
  - clean up inbox
  - what is in my inbox
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent inbox-triage skill

Implements superagent/docs/_internal/ideas-better-structure.md item #5. Classification engine: `tools/inbox_triage.py`.

## 1. Survey

Run `uv run python -m superagent.tools.inbox_triage list` — get the count and modified-at of every file currently in `Inbox/`. Also run `uv run python -m superagent.tools.inbox_triage stale --days <config.preferences.inbox_triage.stale_days>` (default 14) to surface anything sitting too long.

If the inbox is empty, end here with: "Inbox is empty. Nothing to triage."

## 2. Classify

Run `uv run python -m superagent.tools.inbox_triage classify --json` — get per-file `{filename, size, extension, kind_hint, category_suggested, confidence, suggested_path}` rows.

## 3. Walk + ask

For each file (highest-confidence first), surface:

```
[N/total] <filename>  (<size>, <kind>, modified <date>)
  Suggested: <Sources/<category>/<filename>>  (confidence: <high|medium|low>)
  Matched keywords: <list>

  What do you want to do?
    f - File at suggested location
    F - File at a different location (you specify)
    a - Add as a `.ref.md` instead of moving the file (link, don't move)
    d - Discard (move to Tmp/_inbox-discarded/<date>/)
    l - Leave (defer for next time)
    s - Skip (will re-surface)
```

Per choice:

- **f**: invoke `add-source --document <Inbox/path> --category <suggested category>`. The `add-source` skill moves the file and writes to `_memory/sources-index.yaml`.
- **F**: prompt for category; same path with the override.
- **a**: invoke `add-source --reference --kind file --source <Inbox/path>`. The original file stays where it is; a `.ref.md` is added at the user-chosen path under `Sources/` (typically `Sources/<category>/<short-slug>.ref.md`). Useful for one-off recordings or photos that the user wants indexed but not relocated.
- **d**: move to `workspace/Tmp/_inbox-discarded/<YYYY-MM-DD>/<filename>` (so discards are reversible for 30 days before `doctor` cleans them).
- **l**: leave the file in place; don't ask again until next triage run.
- **s**: same as `l` but the next triage run re-surfaces it first.

After each decision, record it:

```
uv run python -m superagent.tools.inbox_triage record --file <name> --action <filed|discarded|left|deferred> --destination <path> --note "<optional>"
```

## 4. Pattern learning

After processing the batch, scan `Inbox/_processed.yaml` for repeated `(filename-pattern, category, destination)` patterns. If the user has `filed` 3+ files matching the same pattern (e.g. `Verizon*.pdf` → `Sources/home/utilities/verizon/`), surface:

> "I notice you've filed Verizon PDFs to `home/utilities/verizon/` 4 times. Want to auto-file matching files next time? (yes / always-ask)"

If yes, append a rule to `_memory/inbox-rules.yaml` (creating it if missing) so the next triage run pre-applies the rule (still asks unless `auto-apply: true`).

## 5. Summary

```
Triaged N files: X filed, Y discarded, Z left, W deferred.
New sources added: <count> (see `_memory/sources-index.yaml`).
Pattern rules learned: <count>.
Inbox now contains <remaining count> files.
```

## 6. Logging

Append to `interaction-log.yaml`:

```yaml
- timestamp: <now>
  type: skill_run
  subject: "inbox-triage"
  summary: "Triaged N files: X filed, Y discarded, Z left."
  related_domain: null
```
