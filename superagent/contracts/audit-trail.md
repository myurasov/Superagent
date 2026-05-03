# Audit Trail Contract

<!-- Migrated from `procedures.md § 30`. Citation form: `contracts/audit-trail.md`. -->

Implements ideas-better-structure.md item #17. Backed by `<file>.history.jsonl` siblings + `tools/audit.py`.

**Every entity-mutating skill SHOULD** call:
```python
from superagent.tools.audit import record_change
record_change(workspace, file_path, kind="update",
              row_id=row_id, old=old_dict, new=new_dict,
              who="user", source="<skill-name>", note="<optional>")
```
BEFORE persisting the new YAML state, so the diff captures the actual transition.

**Skipped files** (per `config.preferences.audit.skip_files`): time-shape logs that audit themselves — defaults to `interaction-log.yaml`, `ingestion-log.yaml`, `user-queries.jsonl`, `events.yaml`.

**Rotation**: when `config.preferences.audit.rotate_yearly: true`, `doctor` rotates `<file>.history.jsonl` to `Archive/<YYYY>/<file>.history.jsonl` at year flip.

**Reading**: `audit history --file <path> --row <id>` returns the chronological transitions. The `audit` skill is the user-facing front-end.
