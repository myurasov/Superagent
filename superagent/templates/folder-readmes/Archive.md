# `Archive/` — frozen-but-retained content

Everything `doctor` (workspace hygiene) and the per-skill auto-archive rules move here. **Nothing in `Archive/` is deleted — moves are reversible.** A single `mv` brings any item back.

## Layout

```
Archive/
  <YYYY-MM>/
    Domains/
      <domain>/                  # whole-domain archive
    bills-<year>.yaml            # rotated yearly bill log
    subscriptions-<year>.yaml    # rotated yearly subscription log
    appointments-<year>.yaml     # rotated yearly appointment log
    interaction-log-<year>.yaml  # rotated yearly interaction log
    _doctor-deletions/           # simplification candidates (template-only files,
                                  # one-line notes, abandoned drafts)
```

Archives are organized by year-month so that "what did I have running in 2024-Q3?" is easy to answer.

## When things move here

- A **domain** with no `history.md` entry in 12 months and no open tasks → `doctor` proposes archive.
- An **asset** marked `status: disposed` → moved to `assets-index.yaml.disposed[]` (kept in the live index for tax / insurance history); the corresponding `Resources/` sub-folder is moved to `Archive/` (the canonical source documents in `Sources/documents/<category>/<asset-slug>/` stay where they are — they're immutable per the Sources contract; user moves them manually if desired).
- **Bills / subscriptions / appointments / dates** older than 12 months → rotated yearly into `Archive/<YYYY-MM>/<kind>-<year>.yaml`.
- **Resources/ files** older than 24 months and never referenced → flagged by `doctor` for archive (move-to-Archive proposed; user confirms).
- **Sources/ documents and references** are NEVER auto-archived. They are immutable. The user may manually move outdated entries to `Archive/` if desired, but the framework refuses to do this on its own.

## Privacy

Same gitignore as the rest of `workspace/`. Stays local.

## Restoring

- For a whole domain: `mv Archive/<YYYY-MM>/Domains/<slug>/ Domains/<slug>/` and re-add the row to `domains-index.yaml`.
- For a single yearly index file: `mv Archive/<YYYY-MM>/<file>.yaml _memory/` (then re-merge if needed).
- For a Resources/ sub-folder: `mv` it back to `Domains/<domain>/Resources/`.
