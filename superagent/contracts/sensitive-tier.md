# Sensitive Tier Contract

<!-- Migrated from `procedures.md § 21`. Citation form: `contracts/sensitive-tier.md`. -->

Implements superagent/docs/_internal/ideas-better-structure.md item #18.

**Tiered storage**: a `_memory/sensitive/` sub-folder. Files routed there get stricter handling. The `config.preferences.sensitive.path` setting can override the location (e.g. point at an encrypted disk-image mount):

```yaml
preferences:
  sensitive:
    enabled: true
    path: "/Volumes/SuperagentVault/_memory/sensitive"   # null = in-workspace
    auto_route_files:
      - "health-records.yaml"
      - "accounts-index.yaml"
```

**Auto-route**: files listed in `auto_route_files` are physically moved to the sensitive subdir at init (or first creation); skills that read them follow the symlink. The `tools/validate.py` schema check is sensitive-tier-aware.

**Per-row sensitive flag**: any row may carry `sensitive: true` to opt that single row into outbound redaction even if its file isn't in the sensitive tier.

**Backup convenience**: `rsync --exclude=sensitive/` excludes the entire tier in one shot.

**No built-in encryption in MVP**: macOS FileVault is the assumed underlying encryption. The roadmap M-01 adds first-class encryption support.
