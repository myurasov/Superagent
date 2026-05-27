# Task Management (todo skill)

<!-- Migrated from `procedures.md § 5`. Citation form: `contracts/task-management.md`. -->

The full skill is in `skills/todo.md`. The contract:

- **Single source of truth:** `_memory/todo.yaml`. Every task has `id`, `title`, `description`, `priority` (P0–P3), `status` (open / in_progress / done / cancelled), `created`, `due_date`, `completed_date`, `related_domain`, `related_asset`, `tags`, `source` (skill name or ingestor that created it; `null` for user-added).
- **Priority rules:** P0 = today / urgent (a bill due today, a health-critical refill); P1 = this week (a doctor's appointment in 4 days, a tax deadline this Friday); P2 = active but not urgent (general "should do this month"); P3 = future / aspirational.
- **ID format:** `task-YYYYMMDD-NNN` (e.g. `task-20260428-001`) for human readability and natural sort order.

### 5.1 Sync to status.md files

After any add / complete / update operation:

1. For each affected task, resolve scope in precedence order — project first, then domain:
   - `related_project` set → rewrite `workspace/Projects/<slug>/status.md` with all open tasks for that project (priority then due date), plus recently completed tasks in the Done section.
   - `related_domain` set (and no project) → rewrite `workspace/Domains/<domain>/status.md` the same way.
2. ALWAYS rewrite `workspace/todo.md` regardless of scope — it is the unified all-tasks view. Tasks from every project and domain appear in priority-grouped tables; the Scope column uses the operational-handle form `project:<slug>` or `domain:<id>` to disambiguate.
3. Format as markdown tables, grouped by priority, with a separate Done section at the bottom (see `templates/todo.md` for the canonical shape).
4. Update the `_Last updated:_` timestamp on each modified file.
5. If a `status.md` file does not exist for a domain, create it from `superagent/templates/domains/status.md`.
