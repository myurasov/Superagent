# Outbox Lifecycle Contract

<!-- Migrated from `procedures.md § 36`. Citation form: `contracts/outbox-lifecycle.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #13. Backed by `_memory/outbox-log.yaml` + lazy sub-folders under `Outbox/`.

**Lifecycle stages** (conventional sub-folders under `Outbox/` — created lazily per § "Lazy sub-directory creation" below):

- `drafts/` — in-progress; agent may revise; mutable.
- `staging/` — finalized; awaiting user "send"; mutable until sent.
- `sent/` — user marked sent; immutable thereafter.
- `sealed/` — snapshots (e.g. handoff packet versions); immutable on creation.

**Every artifact tracked**: append a row to `_memory/outbox-log.yaml.artifacts[]` on create + on each stage transition. The `artifact.path` field advances with the file as it moves between sub-folders.

**Sealing**: on `seal`, the file is moved to `sealed/`, the row's `sealed: true` + `sealed_hash: <sha256>` is recorded. Future writes to the same path are refused.

**Stale-drafts surfacing**: drafts older than `config.preferences.outbox.drafts_stale_days: 14` surface in `weekly-review`.

## Lazy sub-directory creation (no empty stages)

Per the user's "no empty folders" principle (parallel to
`contracts/domains-and-assets.md` § 6.4a for `Domains/`), every sub-folder
under `Outbox/` is **created on first write** — never speculatively at init
time. Init ships `Outbox/` + `Outbox/README.md` flat; the conventional
lifecycle sub-folders (and any artifact-kind sub-folders like `emails/`,
`handoff/`, `contractors/`, `taxes/`) appear when (and only when) the agent
first writes an artifact at that location.

The contract:

- `init` does NOT pre-create `drafts/` / `staging/` / `sent/` / `sealed/`
  (or any other Outbox sub-folder). The user opens `Outbox/` and sees only
  what the workspace has actually accumulated content for — no clutter.
- The first time any skill is about to write to `Outbox/<subdir>/<file>`
  (any depth — `Outbox/drafts/foo.md`, `Outbox/drafts/emails/2026-05-13-x.md`,
  `Outbox/handoff/handoff-20260513.md`, …) it MUST first call:

      uv run python -m superagent.tools.outbox ensure <subdir>[/<sub>...]

  …or, when the skill is itself a Python tool, import the helper directly:

      from superagent.tools.outbox import ensure
      ensure(workspace, "drafts", "emails")

- `ensure` is **idempotent**: when the directory already exists it is a
  near-no-op. When missing, it `mkdir -p`s the leaf. Path components that
  try to escape `Outbox/` (absolute paths, `..` traversal) raise
  `ValueError`.
- After `ensure` returns, the skill proceeds with its normal write. The
  newly-materialized sub-directory is now permanent (until explicitly
  removed by a future `purge-empty` sweep, which only deletes
  sub-directories with no files).
- **`purge-empty`** (CLI: `uv run python -m superagent.tools.outbox purge-empty`)
  is the housekeeping pass. Walks `Outbox/` bottom-up, removing every
  empty sub-directory. Sub-directories with files OR with non-empty
  sub-sub-directories are kept. The `Outbox/` root + `Outbox/README.md`
  are always preserved.

Skills MUST cite this section in their first step when they write into
`Outbox/`. Implicated skills (in MVP): `draft-email` (`Outbox/emails/`),
`handoff` (`Outbox/handoff/`), and any future skill that emits a
publicly-shareable artifact (per `AGENTS.md` § "Public artifact
destination"). The agent may also call `ensure` proactively when it's
about to drop a one-off artifact into `Outbox/drafts/` (the most common
case for ad-hoc drafts and proposals).
