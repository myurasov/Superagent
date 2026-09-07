# Sensitive Tier Contract

<!-- Migrated from `procedures.md § 21`. Citation form: `contracts/sensitive-tier.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #18.

**What is implemented today** (the floor every skill can rely on):

- **The tier root exists.** `init` (via `tools/workspace_init.py`) eagerly creates `_memory/sensitive/`. It is the home for credential and identifier files that tools write themselves — e.g. `tools/simplefin_claim.py` writes `_memory/sensitive/simplefin-credentials.yaml` with mode `600`.
- **The flagged files stay where they are.** `health-records.yaml` and `accounts-index.yaml` live at `_memory/` top level, and every skill and tool reads them there. Nothing relocates them.
- **Per-row sensitive flag.** Any row may carry `sensitive: true` to opt that single row into outbound redaction (`contracts/outbound-surface.md`) regardless of which file it lives in.
- **User-driven relocation.** The user may symlink the whole `_memory/sensitive/` directory (or any individual file) to an encrypted disk-image mount; tools open paths through the symlink transparently. This is a manual step, not something the framework performs.
- **Backup convenience.** `rsync --exclude=sensitive/` excludes the tier root in one shot.
- **No built-in encryption in MVP.** macOS FileVault is the assumed underlying encryption. Roadmap M-01 tracks first-class encryption support.

**Configuration** — declared in `config.yaml` but only partially consumed:

```yaml
preferences:
  sensitive:
    enabled: true
    path: null                       # reserved: null = workspace/_memory/sensitive
    auto_route_files:                # reserved: declared, NOT yet enforced
      - "health-records.yaml"
      - "accounts-index.yaml"
```

- `path` and `auto_route_files` are **reserved / declarative**. No tool under `superagent/tools/` reads `auto_route_files`, no init or migration step moves the listed files into the tier, and no symlink is created on their behalf. The keys document intent so the eventual implementation has a stable config surface; they do not change behaviour today.
- `tools/validate.py` is **not tier-aware**: it validates memory files at their `_memory/` top-level paths and does not resolve `sensitive.path`.

**Roadmap (not implemented)** — tracked as roadmap row S-33, alongside M-01:

1. Physically relocate each `auto_route_files` entry into `<sensitive.path>/` at init (or first creation) and leave a symlink at the top-level path so existing readers keep working.
2. Make `tools/validate.py` resolve `auto_route_files` through the configured `sensitive.path` before checking schemas.

Until those land, skills MUST NOT assume a flagged file lives under `sensitive/`; read it at `_memory/<file>` and follow whatever symlink the user may have placed there.
