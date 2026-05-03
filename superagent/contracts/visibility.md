# Visibility Contract

<!-- Migrated from `procedures.md § 20`. Citation form: `contracts/visibility.md`. -->

Implements ideas-better-structure.md item #19. Every entity carries an optional `visibility` field.

**Values**:
- `private` (default) — owner only; never appears in shared / multi-user views.
- `household` — visible to anyone in the workspace's household scope.
- `public` — included in any export (handoff packet, contractor brief, tax-prep packet) by default.

**Outbound scrub** (per § "Outbound Surface Contract"): redacts `private` content; lets `public` through; treats `household` per the recipient.

**Per-domain default**: a domain's `visibility` field cascades as the default for entities scoped to it (e.g. all `Domains/Home/` entities default to `household` since the Home domain itself is `household` in the seeded config).
