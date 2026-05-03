# Workspace Path Configuration

<!-- Migrated from `procedures.md § 10`. Citation form: `contracts/workspace-path.md`. -->

`workspace/` is the default workspace root. The user can override via `_memory/config.yaml.preferences.workspace_path` if they want the workspace somewhere else (e.g. on an encrypted external disk). Skills resolve the workspace root from `config.yaml` on every run; nothing is hardcoded except the default.
