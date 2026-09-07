# Email capture without hooks — the harness-independent floor

[Do not change manually — managed by Superagent]

This rule makes `contracts/email-capture.md`'s capture-on-touch guarantee hold on
**every** harness, including harnesses with no tool-call hook surface at all. It
is the email-archive specialization of the `AGENTS.md` § "Non-Negotiables (Every
Harness)" principle that hooks are optional enhancements and **nothing critical
may depend on them**.

## 1. The invariant

Every successful Gmail MCP call leaves a record in `workspace/_memory/email/`.
This is a property of the **agent's behavior**, not of the harness's
configuration. A harness without hooks changes HOW the record gets written, never
WHETHER it gets written.

The three triggering calls, per `contracts/email-capture.md` § 3:

| MCP tool | Capture kind | Result |
|---|---|---|
| `read_email` | `inbound` | full record (upgrades an existing stub) |
| `send_email` | `sent` | full record of what was sent |
| `search_emails` | `stubs` | one cheap stub per hit |

## 2. Always capture explicitly — do not try to detect the hook

**Run the capture command after every Gmail MCP call, unconditionally.** Do not
branch on whether the harness has hooks, and do not try to detect whether a hook
already fired.

This is safe because **capture is idempotent** (`contracts/email-capture.md` § 4):
the sidecar is keyed by Gmail `message.id`, so a second capture of the same
message reports `existing` (or `upgraded`, stub to full) and writes no duplicate
row. The cost of a redundant call is one fast local process; the cost of a skipped
one is a silently missing archive record that nobody notices for weeks.

Detection was tried and is a trap. Hook wiring can be present, well-formed for
one harness, and completely inert on another — the failure is silent by design,
because the bridge never blocks the parent tool call. On 2026-09-06 the Cursor
side of this framework was found to have been inert since it was written: the
hooks file used Claude Code's event names, which Cursor ignores in
`.cursor/hooks.json`. Nothing surfaced an error; emails simply were not archived.

## 3. The commands

`--raw` means "stdin is the MCP tool response itself, not a hook envelope". Pipe
in the response text exactly as the MCP returned it.

After `read_email`:

```bash
export UV_PROJECT_ENVIRONMENT="${PWD}/.venv.noSync"
uv run python -m superagent.tools.email.archive_hook --kind=inbound --raw <<'RESPONSE'
<paste the read_email response verbatim>
RESPONSE
```

After `search_emails`:

```bash
uv run python -m superagent.tools.email.archive_hook --kind=stubs --raw <<'RESPONSE'
<paste the search_emails response verbatim>
RESPONSE
```

After `send_email` — the response is minimal, so the request body has to be
supplied separately or the archived record has no content:

```bash
uv run python -m superagent.tools.email.archive_hook --kind=sent --raw \
  --request-json '{"to":["<addr>"],"subject":"<subj>","body":"<body>"}' <<'RESPONSE'
<paste the send_email response verbatim>
RESPONSE
```

`--kind=sent --raw` exits 2 with a one-line stderr message and archives
nothing when `--request-json` is missing or malformed (not a JSON object) —
fix the JSON and re-run.

Use a quoted heredoc (`<<'RESPONSE'`) so the shell does not interpolate `$`,
backticks, or quotes inside the message body.

## 4. Verification ritual

Before declaring any email-touching task complete, confirm the records exist:

```bash
uv run python -m superagent.tools.email.archive find <message-id>
```

`no record for id=...` means the capture did not happen — re-run § 3. When a task
touched several messages, check each id. This is the same class of end-of-task
self-check as the `rules/file-naming.md` scan for spaces.

## 5. Harness matrix

The `--raw` path in § 3 works identically on all three rows. The hook column is
only about whether the write happens automatically as a side-effect.

| Harness | Hook surface | Wiring | Fallback still required? |
|---|---|---|---|
| Claude Code | `PostToolUse` | `.claude/settings.json`, three matchers on `mcp__gmail__<tool>` | No, but harmless |
| Cursor | `afterMCPExecution` | `.cursor/hooks.json`, one entry, no matcher, `--kind=auto` | No, but harmless |
| Generic `AGENTS.md` CLI | none | n/a | **YES — this is the only path** |

Two things make the hook rows unreliable enough to keep capturing explicitly: a
harness can silently stop matching after a config or version change, and Cursor's
`afterMCPExecution` does not run in cloud agents.

## 6. Diagnosing a hook that should be firing but is not

1. Read the bridge's own log: `~/.superagent/tmp/email_archive_hook.log`. Every
   invocation logs one line. **No line at all means the hook never ran** — the
   problem is the harness's config, not the bridge.
2. Dump the envelope the harness actually sends:
   `SUPERAGENT_EMAIL_HOOK_DEBUG=1`. Field names differ per harness and per
   version (`tool_response` vs `result_json` vs `tool_output`) and are thinly
   documented; the dump is the ground truth.
3. Confirm the harness uses its own event names. Cursor requires camelCase
   Cursor-native names in `.cursor/hooks.json` (`beforeSubmitPrompt`,
   `postToolUse`, `afterMCPExecution`); it maps Claude Code's `PascalCase` names
   only when reading `.claude/settings.json`. Cursor reloads `hooks.json` on
   save, but a restart is sometimes needed; the Hooks tab and Hooks output
   channel show what actually loaded.
4. Check the privacy gate:
   `_memory/config.yaml.preferences.privacy.archive_emails` (default true).

## 7. Read-side symmetry

The fallback covers the WRITE side. The READ side is unchanged and equally
harness-independent: every skill that needs an email scans the local archive
first (`archive.find` / `find_by_query`, or
`uv run python -m superagent.tools.email.archive query ...`) and only goes to a
live MCP read for the strictly-newer slice the archive does not cover, per
`contracts/local-first-read-order.md`.

## Related rules and contracts

- `contracts/email-capture.md` — the capture-on-touch contract this rule keeps
  true without hooks; § 8.3 documents the no-hook path.
- `AGENTS.md` § "Non-Negotiables (Every Harness)" — hooks are enhancements;
  missing capability changes HOW a step runs, never WHETHER.
- `contracts/local-first-read-order.md` — the read-side counterpart.
- `rules/machine-local-home.md` — why the bridge's diagnostic log lives under
  `~/.superagent/tmp/`.
