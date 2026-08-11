# Sandbox ladder (always-on)

[Do not change manually — managed by Superagent]

Every harness that can host Superagent (Cursor, Claude Code, Codex CLI, and
any future `AGENTS.md`-reading CLI) runs shell commands inside some sandbox.
A sandbox denial is a **routing signal, not a stop sign**: the correct
response is to climb one rung and keep going, not to report failure. This
rule is the canonical ladder. For denials that are name blocks rather than
sandbox policy, use `rules/unbox.md` instead.

## The ladder

Climb in order; stop at the first rung that works.

1. **Harness-native tools.** Use the harness's own file tools (Read / Write /
   StrReplace-style edit tools) for file reads and edits. They are mediated
   by the harness and almost never hit the sandbox. Shell is for execution,
   not for reading files.
2. **Per-command escalation.** Re-run the same command with the harness's
   escalation mechanism (Cursor: `required_permissions`; Claude Code:
   permission prompt; Codex CLI: `approval_policy = "on-request"`). The
   standing posture (AGENTS.md floor) is that escalation is **allowed and
   encouraged** — ask for it freely rather than abandoning the task. If the
   command class is already registered in `workspace/_memory/escalate.yml`,
   escalate on the FIRST attempt (`rules/run-escalated.md`).
3. **Relocate to writable scratch.** If escalation is unavailable or denied,
   redo the work under the machine-local transient root `~/.superagent/tmp/`
   (`rules/machine-local-home.md`) — sandboxes that block the repo path
   usually permit the machine-local roots. Copy results back through
   harness-native file tools (rung 1).
4. **Report the limit.** Only after rungs 1–3 all fail: state exactly what
   was denied (command, error text), which rungs were tried, and what single
   approval would unblock the work. Never silently degrade to a worse
   answer, and never report a task done that a denial actually blocked.

## Hard rules

- **One denied command never ends a task.** A task ends when the deliverable
  exists or when rung 4 has been reached with a precise report.
- **Never retry the identical denial blindly.** Same command, same error →
  climb a rung; do not loop.
- **Never write outside the project folder or the sanctioned
  `~/.superagent/` roots** to escape a sandbox (`rules/development-tooling.md`
  scope discipline still applies on every rung).
- **Denials worth remembering are recorded.** A sandbox quirk that will recur
  (e.g. "Seatbelt kills Chromium on launch") is written where the next
  session finds it: the owning skill's doc, `docs/harness-matrix.md`, or
  `workspace/_memory/escalate.yml` — not left in chat.

## Enforcement

The AGENTS.md floor (item 3) routes every denied command here. Skills that
run shell-heavy flows cite this rule in their constraints. The Supertailor's
hygiene pass flags sessions where a denial was followed by task abandonment
without a rung-4 report.

## Override / user overlay

Users may extend this policy at `workspace/_custom/rules/sandbox-ladder.md`
(additive; read after the framework file) — e.g. machine-specific rungs such
as a local seatbelt bridge.
