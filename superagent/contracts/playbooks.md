# Playbooks Contract

<!-- Migrated from `procedures.md § 26`. Citation form: `contracts/playbooks.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #21. Backed by `superagent/playbooks/<name>.yaml` (framework) and `workspace/_custom/playbooks/<name>.yaml` (user overlay). Resolver: `tools/play.py`.

**A playbook is a sequence of skills with conditions**. The resolver evaluates conditions against current workspace state and yields the ordered list of skills. The agent then walks the list, invoking each skill in turn.

**Conditions** (parsed by `tools/play.py.eval_condition`):
```
<query> <op> <value>
```
Where `<query>` is one of: `bills_overdue`, `appointments_today`, `tasks_p0_open`, `projects_active`, `important_dates_today`, `subscriptions_audit_flag`. `<op>` ∈ `{== != < <= > >=}`. `<value>` is an integer.

Plus: `always`, `never`.

**Custom overlay**: `_custom/playbooks/<name>.yaml` overrides framework playbook of same name. The override is announced in chat.

**Five starter playbooks**: `start-of-day`, `end-of-week`, `tax-prep-season`, `pre-trip-week`, `health-checkup-quarter`. Schema reference: `playbooks/_schema.yaml`.
