---
to_version: 0.0.0
from_version: 0.0.0
title: "One-line title shown to the user"
breaking: false
revertible: true
estimated_duration: "<1 minute"
touches:
  - workspace/.version
helper_scripts: {}
---

<!--
  Author guide:
  - Replace ALL frontmatter values above before committing.
  - Mark breaking: true for MAJOR bumps OR for behaviorally-breaking MINOR.
  - Mark revertible: false ONLY for genuinely irreversible changes (data loss).
  - List EVERY workspace path the migration writes/creates/deletes in `touches`.
  - When you add helper scripts, drop them under `superagent/migrations/<to_version>/`
    and reference them in `helper_scripts` as `migrate: <to_version>/migrate.py`,
    `revert: <to_version>/revert.py`, `validate: <to_version>/validate.py`.
  - Keep this file ASCII-clean and free of personal data per
    contracts/framework-artifacts.md.
-->

## Summary

Why this migration exists, what it changes, what risks it carries.
2-4 sentences max — the user sees this before consenting.

## Pre-flight checks

What must be true before applying. The `migrate` skill enforces these
and aborts with a clear remediation message on any miss.

- [ ] Workspace exists at the resolved path.
- [ ] `_memory/config.yaml` is well-formed YAML.
- [ ] (other migration-specific checks)

## Migrate

Numbered, executable steps. Each step is one of:

1. A pure file-system operation the agent performs directly (rename,
   create, delete, touch).
2. A `uv run python superagent/migrations/<to_version>/migrate.py
   --workspace <path>` invocation.
3. A precise YAML / Markdown edit (state the file, the location, and
   the diff).

## Validate

How the skill verifies the migration succeeded. The skill (and the
optional `validate.py`) both run these.

- [ ] (validation 1)
- [ ] (validation 2)

## Revert

Numbered, executable steps that reverse the `Migrate` steps in inverse
order. If `revertible: false`, replace this section with a paragraph
explaining what is irreversibly lost and what the user should back up
before applying.
