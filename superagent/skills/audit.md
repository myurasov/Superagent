---
name: superagent-audit
description: >-
  Read the per-row audit history of any entity. Backed by sibling
  `<file>.history.jsonl` append-only logs maintained by the audit-trail
  contract (contracts/memory-taxonomy.md).
triggers:
  - audit
  - audit history
  - when did X change
  - history of
  - row history
mcp_required: []
mcp_optional: []
cli_required: []
cli_optional: []
---

# Superagent audit skill

Implements ideas-better-structure.md item #17. Engine: `tools/audit.py`.

## 1. Branch on intent

### History (default)

User said "when did X change" / "audit history of Y":

1. Resolve the file: which entity index does this query touch (contacts.yaml? bills.yaml? assets-index.yaml? ...).
2. Resolve the row id (if specified) — accepts canonical handles via `tools/handles.py parse`.
3. Run:
   ```
   python3 -m superagent.tools.audit history --file <_memory/<file>.yaml> --row <id> --limit 50
   ```
4. Render entries:
   ```
   2026-04-28T08:30:14  [update]  contact:dr-smith-dentist  by user
       note: ""
       changed: email
       old: smith.j@oldclinic.com
       new: jane.smith@newclinic.com
   ```

### List

Show every audit-trail file in the workspace:

```
python3 -m superagent.tools.audit list
```

Outputs each `<file>.history.jsonl` with row count.

## 2. Audit-trail contract

Per `contracts/audit-trail.md`, every entity-mutating skill SHOULD call:

```python
from superagent.tools.audit import record_change
record_change(workspace, file_path, kind="update",
              row_id=row_id, old=old_dict, new=new_dict,
              who="user", source="<skill-name>", note="<optional>")
```

BEFORE persisting the new YAML state, so the diff captures the actual transition. If a skill doesn't, no audit row is written for that change — but the next change to the same row IS captured.

Skipped files (loud-write logs that audit themselves): per `config.preferences.audit.skip_files` — defaults to `interaction-log.yaml`, `ingestion-log.yaml`, `user-queries.jsonl`.

## 3. Rotation

When `config.preferences.audit.rotate_yearly` is true, `doctor` rotates each `<file>.history.jsonl` to `Archive/<YYYY>/<file>.history.jsonl` at year flip. The current year's file stays live.

## 4. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "audit (history)"
  summary: "Showed audit history of <file>:<row>"
```
