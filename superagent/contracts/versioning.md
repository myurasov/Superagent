# Versioning and Migration Contract

<!-- Citation form: `contracts/versioning.md`. -->

This contract governs how Superagent's framework version is tracked, how
workspaces stay in sync with the framework that built them, and how
breaking or significant changes are migrated forward (and rolled back).

It is the **sole** policy for migrations. The `migrate` skill is the
**sole** entry point for applying them. The `superagent/migrations/`
directory is the **sole** registry of migration files.

---

## 1. Versioning scheme

Superagent follows **semantic versioning** (`MAJOR.MINOR.PATCH`). The
framework's authoritative version lives in `pyproject.toml` (`project.version`).

| Bump | Triggers | Migration file required? | `workspace/.version` action |
|---|---|---|---|
| **MAJOR** (X.0.0) | Breaking change to workspace data layout, schema, required config keys, public Python tool API, or any user-visible contract | **YES — required** | Stays at old value until `migrate` skill runs |
| **MINOR** (0.X.0) | Backwards-compatible **significant** change — new memory file, new top-level concept, new optional field, new skill that touches workspace data, deprecated-but-still-works behavior | **YES if it touches workspace data; otherwise OPTIONAL** | Stays at old value until `migrate` skill runs (when migration exists), else auto-advances |
| **PATCH** (0.0.X) | Bug fix, doc tweak, prompt-cache cleanup, internal refactor invisible to users, dependency bump | **NO** | Auto-advances silently on next `migrate check` or session-open |

**Rule of thumb**: if a workspace authored on the previous version would
behave incorrectly or fail validation on the new version without
intervention, the bump is at least MINOR and a migration file is required.

Each version bump is recorded in `superagent/docs/roadmap.md` under
"Released" with one line per release.

---

## 2. Workspace `.version` file

Every workspace tracks the framework version it was last migrated to in
**`workspace/.version`** — a single-line UTF-8 text file containing
exactly one semver string and a trailing newline:

```
0.2.0
```

- **Created by**: the `init` skill (sets it to the framework's current
  version) or by the first `migrate` run on a pre-existing workspace.
- **Read by**: `superagent/tools/version.py.workspace_version()`.
- **Written by**: the `migrate` skill (after each successful step) or
  by `tools/version.py.set_workspace_version()`.
- **Never edited by hand** — manual edits silently bypass migrations and
  can corrupt state. The skill warns if the file's mtime drifts away
  from the last `migrate` run recorded in `interaction-log.yaml`.

If the file is **missing** (legacy workspace), the framework treats the
workspace as **`0.1.0`** (the version before this contract existed) and
the `migrate` skill bootstraps it.

---

## 3. Migration files

Migration files live in **`superagent/migrations/`** — one file per
target version, named after the `to_version`:

```
superagent/migrations/
  README.md          # what migrations are, how to author them
  _manifest.yaml     # ordered registry (single source of truth)
  _template.md       # template for authoring a new migration
  0.2.0.md           # migration TO 0.2.0 (from 0.1.x)
  0.3.0.md           # migration TO 0.3.0 (from 0.2.x)
  1.0.0.md           # migration TO 1.0.0 (from 0.x.y)
  ...
```

Optional companion files for a given version may live alongside the `.md`
in a subfolder when the migration needs scripts:

```
superagent/migrations/0.3.0/
  migrate.py         # programmatic migration (idempotent)
  revert.py          # reverse migration (idempotent)
  validate.py        # post-migration sanity check
```

The `.md` file remains the **canonical, human-readable** instruction set.
Scripts are convenience helpers the `.md` may invoke.

### 3.1 Migration file format

YAML frontmatter + standard sections. Skills parse the frontmatter to
build the chain; the body is the agent's executable instruction set.

```markdown
---
to_version: 0.3.0          # required; semver of the version this migrates TO
from_version: 0.2.0        # required; semver of the immediately-preceding version
title: "One-line title"    # required; shown to user before applying
breaking: false            # required; true if MAJOR or behaviorally breaking
revertible: true           # required; false ONLY for genuinely irreversible changes
estimated_duration: "<1m"  # required; human estimate (used in user prompt)
touches:                   # required; list of workspace paths the migration writes
  - workspace/.version
  - workspace/_memory/...
helper_scripts:            # optional; relative paths to scripts in the same folder
  migrate: 0.3.0/migrate.py
  revert:  0.3.0/revert.py
  validate: 0.3.0/validate.py
---

## Summary

Why this migration exists, what it changes, what risks exist.
2-4 sentences max.

## Pre-flight checks

What MUST be true before migrating. The `migrate` skill enforces these
and aborts on any miss with a clear message.

- [ ] Workspace exists at the configured path.
- [ ] `_memory/config.yaml` is well-formed YAML.
- [ ] (other migration-specific checks)

## Migrate

Numbered, executable steps. Each step is either:

1. A pure file-system operation the agent performs directly, OR
2. A `uv run python superagent/migrations/X.Y.Z/migrate.py` invocation, OR
3. A YAML-edit instruction with a precise diff.

## Validate

How the skill verifies the migration succeeded. Agents and `validate.py`
both run these.

## Revert

Numbered, executable steps that reverse the migrate steps in inverse
order. Mark the migration `revertible: false` ONLY for genuinely
irreversible changes (data loss, schema-narrowing). Most migrations
should be revertible — design them that way.
```

### 3.2 Migration authoring rules

1. **One file per `to_version`.** Do not bundle multiple version bumps
   in one migration. The skill applies them one at a time (chained).
2. **Idempotent.** Re-running a migration over an already-migrated
   workspace must be a no-op — checked via the `Validate` section.
3. **Revertible by default.** Design migrations so the `Revert` section
   restores the prior workspace state byte-for-byte where possible. When
   that's impossible, document what is lost and require user
   acknowledgement before proceeding.
4. **Workspace-only.** Migrations modify `workspace/` only — never the
   framework itself. The framework code is what the user pulled; the
   migration adapts the user's data to that code.
5. **No personal data.** The migration `.md` and helper scripts go in
   `superagent/` (committed), so the Framework Artifact Creation
   Contract applies — no household-specific names, addresses, account
   numbers in the file body.
6. **Pre-flight is canonical.** The skill must run pre-flight checks and
   abort cleanly on any failure, naming the check that failed and the
   remediation. Never half-apply a migration.
7. **Touches list is exhaustive.** Every path the migration writes,
   creates, or deletes appears in `touches:`. The skill uses this for
   the dry-run report and for revert validation.

### 3.3 The `_manifest.yaml`

`superagent/migrations/_manifest.yaml` is the ordered registry.
`tools/version.py` reads it to compute migration chains. Format:

```yaml
schema_version: 1
generated_at: <iso datetime>
migrations:
  - to_version: 0.2.0
    from_version: 0.1.0
    file: 0.2.0.md
    breaking: false
    revertible: true
    title: "Bootstrap workspace .version tracking"
  - to_version: 0.3.0
    from_version: 0.2.0
    file: 0.3.0.md
    ...
```

The manifest is **derived** from the migration files — `tools/version.py
refresh-manifest` rebuilds it by scanning `superagent/migrations/*.md`
and parsing each frontmatter. Hand-editing is discouraged; the
sub-command is cheap to re-run.

---

## 4. The `migrate` skill

`superagent/skills/migrate.md` is the **sole** user-facing entry point
for migrations. It is invoked:

- Explicitly by the user (`migrate`, `upgrade superagent`, etc.).
- Implicitly by `AGENTS.md` § "On workspace open" when it detects a
  version mismatch between `workspace/.version` and the framework.

The skill's contract:

1. **Read state.** Compare `workspace/.version` (default `0.1.0`) to the
   framework's current version (from `pyproject.toml`).
2. **Compute chain.** Use `tools/version.py find-chain` to build the
   ordered list of migrations to apply. PATCH-only differences are not
   migrations — `.version` advances silently in step 8 below.
3. **Show summary.** Display the chain to the user (each step's title,
   `breaking` flag, `estimated_duration`, `touches`). Pick the right
   message:
   - **No migrations needed**: report and exit.
   - **PATCH-only mismatch**: offer to bump `.version` silently, exit.
   - **MINOR/MAJOR migrations needed**: continue.
4. **Ask for confirmation.** One `AskQuestion`:
   `proceed` / `dry-run` / `cancel`. Dry-run prints the steps without
   applying them.
5. **Apply chained, one at a time.** For each migration in order:
   1. Re-read the file's `Pre-flight checks`. Run them. Abort on any
      failure with the exact remediation message and the revert
      command for any migrations already applied in this run.
   2. Apply the `Migrate` steps. Helper scripts are invoked via
      `uv run python superagent/migrations/<X.Y.Z>/migrate.py`
      with `--workspace <path>` and `--dry-run` flags supported.
   3. Run the `Validate` steps. On any failure, halt and surface the
      revert command for THIS migration plus all prior ones in the run.
   4. Update `workspace/.version` to the migration's `to_version`.
   5. Append a row to `_memory/interaction-log.yaml` referencing the
      migration file, the from/to versions, the duration, and any
      notes.
6. **Final summary.** Report the new version, the count of migrations
   applied, the total duration, and a one-line reminder that
   `migrate revert` rolls back to the prior state.
7. **On any failure mid-chain**: leave `workspace/.version` at the
   highest successfully-applied value. Surface the exact revert
   command. Never half-apply.
8. **PATCH-only auto-bump**: if the only difference between current and
   workspace is in PATCH digits, the skill writes the new version to
   `.version` without further prompting and exits.

### 4.1 Reverting

`migrate revert [--to <version>]`:

- Without `--to`: reverts the most recent migration only.
- With `--to`: reverts all migrations applied above the target version,
  one at a time, in **reverse** order.
- Each revert runs the migration file's `Revert` section.
- Migrations marked `revertible: false` halt the revert chain. The
  skill surfaces the exact list of files affected and asks the user to
  approve a manual restore from backup or to accept the partial state.
- Logged to `_memory/interaction-log.yaml` like a forward migration.

### 4.2 Downgrade scenario

If `workspace/.version` is **higher** than the framework's current
version (user pulled an older framework), the skill warns and offers:

- `upgrade-framework` — recommended; the user pulls the matching
  framework version with their VCS.
- `revert-workspace` — applies the revert chain down to the framework's
  version. Requires every step in the chain to be `revertible: true`.
- `cancel` — leave both alone; subsequent skills may misbehave.

---

## 5. Tools

**`superagent/tools/version.py`** is the single Python module backing
the migrate skill. Public API:

| Function | Purpose |
|---|---|
| `current_version()` | Read framework version from `pyproject.toml`. |
| `workspace_version(workspace_path)` | Read `workspace/.version`; defaults to `"0.1.0"` when missing. |
| `set_workspace_version(workspace_path, version)` | Write `.version` (with newline). |
| `parse(s)` | Parse a semver string into a `Version(major, minor, patch)` namedtuple. |
| `compare(a, b)` | `-1 / 0 / +1` for `a vs b`. |
| `bump_kind(from_v, to_v)` | Returns `"major" / "minor" / "patch"`. |
| `find_chain(from_v, to_v, manifest_path=None)` | Returns the ordered list of migration entries from the manifest. Empty list if same version or PATCH-only. Raises if a step is missing. |
| `refresh_manifest(migrations_dir=None)` | Rebuild `_manifest.yaml` from the `.md` files in the directory. |

CLI sub-commands (all `uv run python -m superagent.tools.version <cmd>`):

| Command | Purpose |
|---|---|
| `current` | Print the framework's current version. |
| `workspace [--workspace PATH]` | Print the workspace's `.version`. |
| `check [--workspace PATH]` | Compare; exit 0 if matched, 1 if migration needed, 2 if downgrade. |
| `chain [--workspace PATH]` | Print the migration chain that would be applied. |
| `set [--workspace PATH] VERSION` | Write `.version` directly (admin escape hatch). |
| `refresh-manifest` | Rebuild `superagent/migrations/_manifest.yaml`. |

The skill **does not** apply migrations via this tool — the tool only
inspects state and computes chains. Application is the skill's job
(driven by reading the migration `.md` step by step).

---

## 6. Integration with other contracts

- **`contracts/init-flow.md`** — `init` writes `workspace/.version`
  matching the current framework version after scaffolding succeeds.
- **`contracts/local-first-read-order.md`** — workspace-open hook
  in `AGENTS.md` checks `.version` BEFORE step 1 of the read order, so
  a stale workspace doesn't get queried with new schemas.
- **`contracts/audit-trail.md`** — every migration row appended to
  `interaction-log.yaml` references the migration file path, from/to
  versions, and the helper script versions used.
- **`contracts/framework-artifacts.md`** — migration files are
  framework artifacts; the safeguard against personal data in the body
  applies.
- **`docs/roadmap.md`** — every released version gets a one-line entry
  under "Released" linking to the migration file (when one exists).

---

## 7. Examples

### Example A — A breaking schema rename (MAJOR bump 0.9.0 → 1.0.0)

You rename `_memory/contacts.yaml` to `_memory/people.yaml` because
"people" better captures households + acquaintances.

1. Bump `pyproject.toml` to `1.0.0`.
2. Author `superagent/migrations/1.0.0.md`:
   - `breaking: true`, `revertible: true`
   - Migrate: rename the file; run `tools/world.py rebuild` to update
     references in `world.yaml`.
   - Revert: rename back; rebuild world graph.
3. Run `tools/version.py refresh-manifest`.
4. Append release line to `docs/roadmap.md`.
5. Commit.

User pulls the new framework, runs `migrate`, gets the rename applied
to their workspace, `.version` advances to `1.0.0`.

### Example B — A new optional memory file (MINOR bump 0.5.0 → 0.6.0)

You add `_memory/preferences.yaml` for a new "soft preferences"
subsystem. Existing workspaces work fine without the file (skills
default-fill on read).

- **No migration required** — just a version bump. `.version`
  auto-advances on the next `migrate check`.

### Example C — A bug fix (PATCH bump 0.6.0 → 0.6.1)

You fix a bug in `tools/sources_index.py` that double-counted some
entries on rebuild.

- **No migration**. `.version` auto-advances silently. Users may want
  to run `tools/sources_index.py refresh` manually if they were bitten
  by the bug; that's a release-notes line in `docs/roadmap.md`, not a
  migration.

---

## 8. Out of scope (for now)

- **Concurrent multi-workspace migration.** The skill operates on one
  workspace at a time. Households with shared workspaces (`docs/architecture.md`
  § "Multi-user options") run `migrate` on the shared copy after
  pulling.
- **Pre-release identifiers** (`1.0.0-rc.1`, `0.5.0-beta`). The MVP
  parser accepts only `MAJOR.MINOR.PATCH`. Extending to pre-releases
  is a future MINOR bump (with its own migration if `.version`
  semantics shift).
- **Auto-apply migrations**. The `migrate` skill is always interactive
  by default. Future scripted flows may opt into a `--yes` mode but
  that mode is not the default for any user-facing trigger.
