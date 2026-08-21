# Harness capability matrix

One row per harness class that has driven Superagent, one column per
capability that matters to the framework. Seeded 2026-08-11 from sibling
frameworks' (Co-SA, Solaris) Aug 2026 cross-agent work; extend as new
harnesses are observed. Rules and skills use **harness-class language**
(`cursor`, `claude-code`, `codex-cli`, `unknown-cli`) — the agent
self-identifies its row; no code detection beyond `tools/ide.py`
(`unknown` is a first-class answer).

| Capability | Cursor | Claude Code | Codex CLI | Generic `AGENTS.md` CLI |
|---|---|---|---|---|
| Loads `AGENTS.md` | Yes (native) | Via `CLAUDE.md` `@`-import | Yes (native) | Yes (native) |
| Hooks that inject context | No (hook API cannot add context) | Yes (`UserPromptSubmit` → skill auto-loader) | No hooks | Assume none |
| Hooks that run commands | Yes (`.cursor/hooks.json`) | Yes (`.claude/settings.json`) | No | Varies |
| Shell sandbox | Sandbox + `required_permissions` escalation | Permission prompts | Seatbelt sandbox + `approval_policy = "on-request"` | Varies |
| Structured question tool | Yes (`AskQuestion`) | Yes (`AskUserQuestion`) | No — ask in prose and wait | Assume prose |
| Subagent / task tool | Yes | Yes | No | Assume none |
| Ranged file reads | Yes (`Read --offset --limit`) | Yes | Shell equivalents (`sed -n`) | Assume shell |
| MCP config file | `.cursor/mcp.json` | `.mcp.json` | TOML config (own format) | Varies |

## Known sandbox quirks

| Quirk | Harness | Evidence | Workaround |
|---|---|---|---|
| Chromium killed at launch (Seatbelt denies mach-bootstrap) | Codex CLI | Solaris `environment.md` + Co-SA harness notes, Aug 2026 | Escalate the `browserctl launch` command; drive commands then work over CDP inside the sandbox (`skills/browserctl.md` § "Sandboxed harnesses") |
| Clipboard / `open` / `sips` denied by name | Codex CLI | Co-SA `unbox` overlay, Aug 2026 | Pass-through wrappers per `rules/unbox.md`; registry `workspace/_memory/unboxed.yml` |
| `defaults write` denied by sandbox | Codex CLI | Co-SA `run-escalated` overlay, Aug 2026 | Escalate on first attempt per `rules/run-escalated.md`; registry `workspace/_memory/escalate.yml` |

## Model tiers (abstraction for rules)

Rules that need a model reference use tiers, not vendor names. Concrete
models rotate; the tier mapping lives here and is updated when the
deployment defaults change.

| Tier | Meaning | Current examples |
|---|---|---|
| **frontier** | deepest reasoning; use for judgment-heavy synthesis | Claude Opus, GPT-5-class, Gemini Pro-class |
| **balanced** | good reasoning at moderate cost; default session model | Claude Sonnet, GPT-5-mini-class |
| **fast** | cheap mechanical sweeps (enumerate, filter, extract) | Claude Haiku, GPT-5-nano-class |

### Task class → tier per delegation posture

Consumed by `rules/subagents.md` (postures `cost` / `quality`; the sibling
frameworks' four-tier ladder is mapped onto Superagent's three tiers). Match
the delegated task's class, then read the active posture's column:

| Task class | `cost` | `quality` |
|---|---|---|
| **Mechanical** (enumerate, filter, extract, verify, apply a specified edit) | fast — read-only agent type for sweeps | balanced |
| **Moderate synthesis** (summarize a thread, assemble an entity brief from named sources) | balanced | frontier |
| **Judgment-heavy** (outbound drafts, ambiguous triage, anything acted on directly) | session model, or inline | session model, never below frontier |

In doubt at `cost`, take the cheaper tier; in doubt at `quality`, the
stronger one.

## Maintenance

- Add a row when a new harness class is first observed driving the
  framework; add a quirk when a denial is diagnosed and worked around.
- Keep entries evidence-based (link the session, rule, or sibling doc that
  demonstrated the behavior) — no speculation.
- This doc is framework knowledge (generic, no user data) and lives under
  `superagent/docs/`; per-machine overrides belong in
  `workspace/_custom/rules/`.
