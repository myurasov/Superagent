# Run-escalated: known-escalation registry (always-on)

[Do not change manually — managed by Superagent]

Some command classes are **known** to require escalated execution on a given
harness — the denial is deterministic, documented, and re-derived every
session at the cost of one failed attempt each time. This rule replaces the
re-derivation with a registry: `workspace/_memory/escalate.yml` records which
command classes need escalation, and on a registry match the agent escalates
on the FIRST attempt instead of discovering the denial again.

## The registry

`workspace/_memory/escalate.yml` (lazy-created on first use; seeded by
migration 0.11.0):

```yaml
# [Managed by agents] Command classes known to require escalated execution.
# Read once per session (AGENTS.md floor). One row per class.
entries:
  - id: esc-2026-08-11-001
    class: "defaults write"          # command class (prefix match on argv)
    harness: "codex-cli"             # harness class the denial applies to
    error_shape: "Operation not permitted (sandbox deny)"
    mechanics: "Codex: approval_policy on-request; Cursor: required_permissions; Claude: permission prompt"
    verified: 2026-08-11             # last date the escalation was confirmed working
    note: "writing a plist outside the sandbox always denies"
```

## The rule

1. **Check before running.** The registry is read once per session (AGENTS.md
   floor item 1). Before running a command whose class matches a row for the
   current harness class, invoke the harness's escalation mechanism on the
   FIRST attempt — do not run it plain first to "see if it still fails".
2. **Record new classes.** When a command fails with a sandbox denial AND
   the retry with escalation succeeds, append a row (new `id`,
   `verified: <today>`). One row per command class, not per invocation.
3. **Re-verify, don't accumulate.** When a registered escalation is used and
   works, update `verified` to today. When a registered class starts working
   WITHOUT escalation (harness fixed its policy), remove the row and note the
   removal in the turn's summary.
4. **Harness-class language.** Rows name a harness *class* (`cursor`,
   `claude-code`, `codex-cli`, `unknown-cli`), never a machine. The agent
   self-identifies its class per `AGENTS.md` § "Harness setup".

## Boundaries

- The registry records *that* escalation works and *how* to request it per
  harness — never credentials, never approval on the user's behalf.
- Escalation posture stays as set by the AGENTS.md floor: allowed and
  encouraged, but each escalated call still goes through the harness's own
  approval surface.
- Name-blocked commands are NOT registered here — those belong in
  `workspace/_memory/unboxed.yml` per `rules/unbox.md`.

## Enforcement

The AGENTS.md floor (item 3) cross-references this file. The Supertailor's
hygiene pass flags repeated deny-then-escalate sequences in
`user-queries.jsonl` whose command class never got a registry row.

## Override / user overlay

Users may extend this policy at `workspace/_custom/rules/run-escalated.md`
(additive; read after the framework file).
