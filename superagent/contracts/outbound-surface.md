# Outbound Surface Contract

<!-- Migrated from `procedures.md § 13`. Citation form: `contracts/outbound-surface.md`. -->

Whenever a skill is about to generate an artifact that the user will hand-carry outside the workspace (a draft email body, a printable shopping list, a PDF for a contractor, a CSV the user will attach to a tax return), the artifact MUST go through a scrub pipeline before it leaves `workspace/`:

1. **Source-discretion check**: redact internal-only labels, `_memory` IDs, agent commentary, debug strings.
2. **Voice-of-the-user check**: the artifact reads in the user's voice (not "Superagent thinks…"); preserves their tone preferences from `model-context.yaml.communication`.
3. **PII compression**: include only the PII the recipient needs. Don't include the full insurance policy number when only the carrier name is required for the question.
4. **File destination**: if no destination was specified, default to `workspace/Outbox/` (which is gitignored).

The full pipeline (and which steps each outbound skill owns) is in `docs/outbound-surfaces.md`.
