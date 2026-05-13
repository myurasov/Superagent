# `superagent/migrations/`

This directory holds **migration files** — one per framework version that
requires changes to existing user workspaces. The full policy is in
[`contracts/versioning.md`](../contracts/versioning.md). The user-facing
entry point is the [`migrate`](../skills/migrate.md) skill.

---

## TL;DR

- Bumping the framework's MAJOR or MINOR version (when the change touches
  workspace data) requires a `.md` file here named after the
  `to_version` (e.g. `0.3.0.md`).
- PATCH bumps do not. Workspaces auto-advance silently.
- Migration files are the **sole** way to migrate. The `migrate` skill is
  the **sole** entry point for applying them.
- Migrations are **chained** (one version at a time) and **revertible by
  default**. Mark `revertible: false` only for genuinely irreversible
  changes (data loss).

---

## Layout

```
superagent/migrations/
  README.md          # this file
  _manifest.yaml     # ordered registry, derived from .md files
  _template.md       # copy this when authoring a new migration
  0.2.0.md           # one .md per to_version, named after the version
  0.3.0.md
  ...
  0.3.0/             # OPTIONAL — companion scripts, only if needed
    migrate.py
    revert.py
    validate.py
```

The `_manifest.yaml` is derived. Rebuild it after authoring a new
migration:

```bash
uv run python -m superagent.tools.version refresh-manifest
```

---

## Authoring a new migration

1. Bump the framework version in `pyproject.toml` per
   [`contracts/versioning.md`](../contracts/versioning.md) § 1.
2. Copy `_template.md` to `<to_version>.md` and fill it in.
3. (If needed) create `<to_version>/migrate.py`, `revert.py`,
   `validate.py` alongside.
4. Refresh the manifest: `uv run python -m superagent.tools.version refresh-manifest`.
5. Run the tests for `tools/version.py` (`uv run pytest superagent/tests/test_version.py`).
6. Add a one-line "Released" entry to `superagent/docs/roadmap.md`.
7. Commit per [`AGENTS.md` § "Git commits"](../../AGENTS.md#git-commits).

The Supercoder enforces the safeguard from
[`contracts/framework-artifacts.md`](../contracts/framework-artifacts.md):
**no personal data** (names, addresses, account numbers) in migration
file bodies.

---

## Applying a migration

Users run the [`migrate`](../skills/migrate.md) skill — they do not edit
files in this directory. The skill:

1. Reads `workspace/.version` and the framework's current version.
2. Computes the chain via `tools/version.py.find_chain`.
3. Applies migrations one at a time, validating + updating
   `workspace/.version` after each.
4. Logs each step to `_memory/interaction-log.yaml`.

See [`contracts/versioning.md`](../contracts/versioning.md) § 4 for the
full skill contract.
