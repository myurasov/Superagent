# MCP / CLI-tool Preflight Protocol

<!-- Migrated from `procedures.md § 1`. Citation form: `contracts/mcp-preflight.md`. -->

Any skill that depends on MCP servers OR shell-installed CLI tools (`rem`, `ekctl`, `exiftool`, `healthsync`, `monarch-cli`, `plaid-cli`, `osascript` for AppleScript, etc.) runs this protocol before starting its main work.

### Steps

1. **Probe each required source** with a lightweight call.
   - For MCPs: a health check or list-tools call.
   - For CLI tools: `which <tool>` followed by `<tool> --version` (or the source's documented "smoke test" command in `data-sources.yaml`).
   - Record each as **available** or **blocked** with the failure mode (auth, missing binary, timeout, permission denied, server error).

2. **Probe each optional source** the skill intends to use.
   - Optional sources are never blockers — record status and skip gracefully if unavailable.

3. **Report to user** at the start of the skill output:
   - All sources available → no report (proceed silently).
   - Any source blocked → state which sources are available and which are blocked, e.g.:
     > "Apple Health (CLI: `healthsync` not installed) and Google Calendar (auth expired) unavailable. Proceeding with bills and todo only."

4. **Adapt skill execution.**
   - Skip steps that depend entirely on a blocked source.
   - Mark affected output sections as "[Source unavailable]" rather than omitting them silently.
   - If a **required** source is blocked, halt the skill and surface the error.

5. **On user retry** ("I installed `healthsync`, try again" / "I re-authed Gmail"):
   - Re-run preflight for previously blocked sources only.
   - Resume the skill from where it left off when possible, using `_memory/context.yaml.last_check` as the time-bounded checkpoint for delta queries.

### Source error classification

| Error type | Meaning | Retry strategy |
|---|---|---|
| `binary_missing` | CLI tool not on `$PATH` | User installs (skill suggests the install one-liner) |
| `auth_expired` | OAuth token expired or revoked | User re-authenticates per source's docs |
| `permission_denied` | macOS Full Disk Access / TCC, Keychain, etc. | User grants permission (skill cites the exact System Settings path) |
| `timeout` | MCP / API slow or unreachable | Auto-retry once after 5 s |
| `server_error` (5xx) | Upstream service issue | Auto-retry once; if still failing, skip |
| `tool_not_found` | MCP doesn't expose expected tool | Skip source, note in output |
| `quota_exceeded` | Rate-limit hit | Skip remainder of run, retry on next cadence |

### Skill preamble convention

MCP / CLI-dependent skills should begin their execution with:

```
Run the MCP / CLI-tool Preflight Protocol (`contracts/mcp-preflight.md`) for this skill's required and optional sources.
```

This single line replaces ad-hoc error handling per skill.
