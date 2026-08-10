# Machine-local transient-state root — `~/.superagent/` (always-on)

[Do not change manually — managed by Superagent]

This repository lives **inside iCloud Drive**
(`~/Library/Mobile Documents/com~apple~CloudDocs/...`). Transient, high-churn,
machine-specific files must **never** enter the iCloud sync queue — they change
constantly, are meaningless on another machine, waste sync bandwidth, and
generate the very `name 2.ext` conflict copies that
`tools/icloud_dup_check.py` exists to catch.

## The convention

Every such file's **real bytes live under one machine-local root** —
`$SUPERAGENT_HOME` (default `~/.superagent/`) — and every consumer references
that location directly. There are **no** compatibility symlinks back into the
checkout. `~/.superagent/` is machine-local (outside any synced tree) and
**disposable**: everything under it is reconstructible. Nothing under it is a
source of truth (that would also violate `rules/memory-routing.md` — durable
memory lives in `workspace/_memory/`).

### Managed directories

| `~/.superagent/` dir | What it holds | Who points at it |
| --- | --- | --- |
| `tmp/` | The canonical transient scratch tree (replaces the former repo-root `./.tmp/`). | Any tool or skill writing scratch; `tools/icloud_dup_check.py` sentinel; `tools/skill_loader.py` session markers. |
| `tools/` | Installed non-Python binaries / CLIs / helpers, one folder per tool (replaces the former repo-root `./.tools/`). | `rules/development-tooling.md` § 2 and any skill installing a tool. |

Any **new** transient directory a tool needs follows the same pattern: real
dir at `~/.superagent/<name>`, referenced by its absolute path, added to
`SUBDIRS` in the helper.

### Deliberate exclusions

- **The uv venv** stays at the repo-root `./.venv/` (the operative venv `uv
  run` uses) — per `rules/development-tooling.md` § 1.
- **browserctl state** lives at `~/.superagent/browserctl/` since 0.10.0
  (managed by browserctl itself, not by the `home.py` SUBDIRS helper; the
  pre-0.10.0 machine-shared `~/.browserctl/` is honored via
  $BROWSERCTL_HOME).
- **`.pytest_cache/`** stays a plain gitignored repo-root dir — small and
  regenerated on demand.

## The helper

`superagent/tools/home.py` is the single source of truth for these paths and
the canonical way to ensure they exist:

```bash
uv run python -m superagent.tools.home            # ensure root + managed subdirs (idempotent)
uv run python -m superagent.tools.home --check    # report; exit 1 if root/any subdir missing
uv run python -m superagent.tools.home --json     # machine-readable
```

Python tools import `superagent_home()` / `tmp_dir()` from
`superagent.tools.home`. `$SUPERAGENT_HOME` overrides the root (e.g.
per-checkout isolation when running multiple checkouts).

## Don't

- **Don't** recreate `./.tmp/` or `./.tools/` at the repo root. Both stay
  gitignored as safety nets (forward-only: pre-existing content is not
  migrated), but new scratch goes to `~/.superagent/tmp/` and new tool
  installs to `~/.superagent/tools/` — anything at the repo root re-enters
  the iCloud sync queue.
- **Don't** put anything under `~/.superagent/` that is a source of truth —
  it is disposable by definition.
- **No file lands in the repo root, ever — not even "temporarily".** Bare
  filenames passed to shell redirects or script outputs resolve relative to
  the CWD (= repo root). Pass an absolute path into `~/.superagent/tmp/` (or
  the harness-provided session scratchpad), or a path inside the owning
  workspace entity folder when the file is a keeper.

## Relationship to scope discipline

`rules/development-tooling.md` § 4 forbids writes outside the project folder.
`~/.superagent/` (including browserctl's `~/.superagent/browserctl/`) is a
**sanctioned, user-authorized exception**, limited to disposable transient
state managed by the helper above. It does not widen the rule for any other outside-repo path.

## Override / user overlay

Users may extend this policy at
`workspace/_custom/rules/machine-local-home.md` (additive).
