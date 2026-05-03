# Custom Overlay Discovery

<!-- Migrated from `procedures.md § 11`. Citation form: `contracts/custom-overlay.md`. -->

Per `AGENTS.md` § "Custom overlay". The full reference is in `docs/custom-overlay.md`. Summary:

- Overlay lives at `workspace/_custom/`.
- Subdirectories: `rules/`, `skills/`, `agents/`, `templates/`, `tools/`.
- Read on every Superagent turn, alphabetical by filename.
- Custom rules apply additively on top of `AGENTS.md`.
- Custom skills are first-class. Same-name collision: framework first, then custom as an addendum, with a chat banner.
- Custom templates override framework templates on same-name match, with a chat banner announcing the override.
