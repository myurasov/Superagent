# Framework Artifact Creation Contract

<!-- Migrated from `procedures.md § 12`. Citation form: `contracts/framework-artifacts.md`. -->

(Copied here in full because it is universal across skills — the same text as `AGENTS.md` § "Framework Artifact Creation Contract", restated for skill authors.)

Whenever a skill is about to create a new **skill**, **agent role**, **rule**, **template**, **tool**, or **doc page**:

1. **Auto-classify destination** — `superagent` (generic, committed) or `_custom` (user-specific, gitignored). **Default to `_custom` when ambiguous.**
2. **If unambiguous**, announce the destination at the top of the response and proceed.
3. **If ambiguous**, **ask the user** with the `AskQuestion` tool: choose `superagent`, `_custom`, or `cancel`.
4. **Safeguard**: scan the artifact body for any name from `_memory/contacts.yaml`, `domains-index.yaml`, `assets-index.yaml`, `accounts-index.yaml`, address fragments, account numbers, license-plate patterns. On any match, refuse the `superagent/` path and re-route to `workspace/_custom/`.
5. **NEVER** silently write user-specific content under `superagent/`. The Supercoder enforces this with a refusal on receipt.

This contract does NOT govern workspace **data** writes (domain files, contact entries, bill records, appointment rows) — those go to `workspace/` by their own conventions.

### Default-routing table

| Artifact | Default destination | Notes |
|---|---|---|
| New skill that is generic across users | `superagent/skills/` | "useful for anyone running Superagent" — rare; default `_custom` if uncertain |
| New skill that mentions a specific person, asset, account, address | `_custom/skills/` | always |
| New rule that codifies a workflow specific to this household | `_custom/rules/` | always |
| New template that styles output for a specific recipient | `_custom/templates/` | always |
| New ingestor for a previously unsupported source | `superagent/tools/ingest/` | generic if the source itself is generic |
| New tool that processes generic data | `superagent/tools/` | otherwise `_custom/tools/` |
| New doc page (architecture, faq, …) | `superagent/docs/` | almost always |
