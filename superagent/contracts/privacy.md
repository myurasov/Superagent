# Privacy and Sensitive Data

<!-- Migrated from `procedures.md § 14`. Citation form: `contracts/privacy.md`. -->

- Everything under `workspace/` is **gitignored** and **local-only**. No agent-initiated upload, sync, or telemetry.
- The most sensitive files (`health-records.yaml`, `accounts-index.yaml`, anything under `Outbox/handoff/`) are surfaced explicitly in `docs/architecture.md` § "Sensitive subfiles" so the user knows which to symlink to encrypted storage if desired.
- Skills that *produce* a sensitive artifact (e.g. `handoff` → "if I get hit by a bus" packet) write to a clearly-labelled subfolder (`Outbox/handoff/`) and surface the location in chat with a "store this somewhere safe" reminder.
- Skills that *consume* sensitive data (medic prep brief, bookkeeper tax packet) include a "do not paste this into a chat assistant unless you trust it" banner at the top of the rendered output.
- The Supertailor / Supercoder safeguard scans every framework-bound write for names, addresses, account-number patterns, license-plate patterns. On any match, the destination is forcibly re-routed to `_custom/` regardless of the original tag.
