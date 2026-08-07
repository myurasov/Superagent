# Tool-usage corrections are persisted, not just fixed (always-on)

[Do not change manually — managed by Superagent]

When a tool invocation fails on the **first try** because the agent guessed the
interface wrong — a nonexistent flag, a wrong subcommand, a wrong argument
order, a path form the tool doesn't accept — the agent MUST do two things, not
one:

1. **Fix it in the moment** — consult `--help` / the tool source, rerun
   correctly, continue the task.
2. **Persist the corrected usage in the same turn** — so no future session
   pays the same round-trip.

## Where to persist

Pick the **highest-leverage surface the mistake would have been prevented by**,
in this order:

1. **AGENTS.md** — if the tool is used across many skills (e.g.
   `tools/log_window.py`, `tools/sources_index.py`, the email archive
   helpers), add or extend a compact one-liner in the section that governs
   the tool. AGENTS.md is in context every turn; this is the strongest fix.
2. **The governing rule or contract** — if the tool already has a canonical
   rule (`rules/large-file-reads.md`, `contracts/email-capture.md`, …), add
   or correct the example invocation there.
3. **The skill body** — if the wrong usage came from a stale example inside a
   skill, fix the skill, re-run `tools/add_step_index.py` on it, and
   regenerate the manifest (`tools/build_skill_manifest.py`).
4. **The tool's `--help` / argparse text** — if the interface itself is
   misleading (a flag name that invites the wrong guess), improve the tool's
   help string or add an alias flag.

User-specific tools or user-specific invocation quirks go to
`workspace/_custom/rules/corrections.md` instead — the Framework Artifact
Creation Contract applies; never write user-specific content under
`superagent/`.

## Scope

- Applies to framework CLI tools, MCP tools (record the working call shape
  where the relevant rule/skill documents that MCP), and third-party CLIs the
  framework standardizes on (`uv`, `gh`, `sips`, `browserctl`, …).
- Transient failures (network, auth expiry, rate limits) are **not** usage
  errors — do not document those beyond what the MCP preflight contract
  already covers.
- One-off exploratory commands that no skill will ever repeat may be skipped —
  the bar is "would a future session plausibly make the same wrong guess?"

## Override / user overlay

Users may extend this policy at
`workspace/_custom/rules/tool-usage-corrections.md` (additive).
