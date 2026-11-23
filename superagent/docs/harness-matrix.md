# Harness capability matrix

One row per harness class that has driven Superagent, one column per
capability that matters to the framework. Seeded 2026-08-11 from sibling
frameworks' (Co-SA, Solaris) Aug 2026 cross-agent work; extend as new
harnesses are observed. Rules and skills use **harness-class language**
(`cursor`, `claude-code`, `opencode`, `codex-cli`, `unknown-cli`) — the agent
self-identifies its row; no code detection beyond `tools/ide.py`
(`unknown` is a first-class answer).

| Capability | Cursor | Claude Code | OpenCode | Codex CLI | Generic `AGENTS.md` CLI |
|---|---|---|---|---|---|
| Loads `AGENTS.md` | Yes (native) | Via `CLAUDE.md` `@`-import | Yes (native) | Yes (native) | Yes (native) |
| Hooks that inject context | No (hook API cannot add context) | Yes (`UserPromptSubmit` → skill auto-loader) | No Superagent hooks | No hooks | Assume none |
| Hooks that run commands | Yes (`.cursor/hooks.json`) | Yes (`.claude/settings.json`) | No Superagent hooks | No | Varies |
| Shell sandbox | Sandbox + `required_permissions` escalation | Permission prompts | Permission rules (user config); no sandbox by default | Seatbelt sandbox + `approval_policy = "on-request"` | Varies |
| Structured question tool | Yes (`AskQuestion`) | Yes (`AskUserQuestion`) | Yes (`question`) | No — ask in prose and wait | Assume prose |
| Subagent / task tool | Yes | Yes | Yes (runs in the background; the result arrives in a later turn) | No | Assume none |
| Ranged file reads | Yes (`Read --offset --limit`) | Yes | Yes (`read` with `offset` / `limit`) | Shell equivalents (`sed -n`) | Assume shell |
| MCP config file | `.cursor/mcp.json` | `.mcp.json` | `opencode.jsonc` (`mcp.servers`) | TOML config (own format) | Varies |
| Agents as registered sessions (Autocode mode, `rules/autocode.md`) | No | No | Yes (OpenCode 2.3+ with the Autocode plugin; `tools/autocode.py`) | No | No |

Per-file wiring (which harness reads which setup file, and whether it is
committed) is tabulated in [`harness-setup.md`](harness-setup.md); `AGENTS.md`
§ "Harness setup" carries the binding summary. The committed
`.cursor/` tree holds only `hooks.json` and the `mcp.json.cursor` template —
there is no `.cursor/rules/` shim, since Cursor reads `AGENTS.md` natively.
`.claudeignore` keeps `.cursor/` and `workspace/` out of Claude Code's context.

## Known sandbox quirks

| Quirk | Harness | Evidence | Workaround |
|---|---|---|---|
| Chromium killed at launch (Seatbelt denies mach-bootstrap) | Codex CLI | Solaris `environment.md` + Co-SA harness notes, Aug 2026 | Escalate the `browserctl launch` command; drive commands then work over CDP inside the sandbox (`skills/browserctl.md` § "Sandboxed harnesses") |
| Clipboard / `open` / `sips` denied by name | Codex CLI | Co-SA `unbox` overlay, Aug 2026 | Pass-through wrappers per `rules/unbox.md`; registry `workspace/_memory/unboxed.yml` |
| Chromium dies at launch with `No usable sandbox!` (Ubuntu 23.10+ grants user namespaces per AppArmor profile) | Any harness whose shell is unconfined (seen with OpenCode); a harness whose command sandbox has an AppArmor profile of its own passes it to the browser, so the same machine can launch there | A launch from an agent session on Ubuntu, Nov 2026 | `browserctl doctor` names the cause and the fix; `doctor --install-profile` after the owner's yes (`skills/browserctl.md` § "Linux") |
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
