# Custom Overlay Discovery

<!-- Migrated from `procedures.md § 11`. Citation form: `contracts/custom-overlay.md`. -->

Per `AGENTS.md` § "Custom overlay — read on every superagent turn" (the authoritative four-step merge rule, loaded on every turn). This contract is the citable summary; there is no separate `docs/` page. Summary:

- Overlay lives at `workspace/_custom/`.
- Subdirectories: `rules/`, `skills/`, `agents/`, `templates/`, `tools/`, `watchers/`.
- Read on every Superagent turn, alphabetical by filename.
- Custom rules apply additively on top of `AGENTS.md`.
- Custom skills are first-class. Same-name collision: framework first, then custom as an addendum, with a chat banner.
- Custom templates override framework templates on same-name match, with a chat banner announcing the override.
- Custom watcher packs in `_custom/watchers/<id>/` are discovered alongside `superagent/watchers/<id>/` (per `contracts/watchlist.md`); on an `id` collision the custom pack wins (override, not merge), with a chat banner announcing the override.
