# Notification Policy Contract

<!-- Migrated from `procedures.md § 31`. Citation form: `contracts/notification-policy.md`. -->

Implements ideas-better-structure.md item #25. Backed by `_memory/notification-policy.yaml`.

**Cadence skills consume rules** to decide what to surface where:

```yaml
rules:
  - id: "bill-due-today"
    when: "next_due == now"
    over: "bills"
    filter: { status: "active" }
    where: ["daily-update", "whatsup"]
    severity: "alert"
    auto_create_task: true
    task_template: { priority: "P0", title: "Pay {name} ({amount} {currency})" }
```

**Default rules** seeded by `init` (see `templates/memory/notification-policy.yaml.default_rules`): bill-due-today, bill-due-soon, bill-overdue, appt-today, appt-tomorrow-needs-prep, doc-expiring-30d, doc-expiring-90d, important-date-today, important-date-soon, task-overdue-p0p1, subscription-trial-ending, med-refill-due, lab-result-abnormal, project-deadline-close, project-overdue.

**Edit the policy without editing skill code**: change the cutoff in YAML, the next cadence-skill run reflects it.

**Auto-create task**: when a rule sets `auto_create_task: true` and matches an entity with no corresponding open task, the cadence skill creates one with the specified priority + a deterministic title (idempotent across runs).
