# Init flow

<!-- Migrated from `procedures.md § 3`. Citation form: `contracts/init-flow.md`. -->

The full step-by-step is in `skills/init.md`. The contract:

- **Quick-start path (default):** scaffold `_memory/`, scaffold the 12 default `Domains/`, ask the user three orientation questions (name, household composition, what hurts most today), enable zero data sources, and end with a 5-minute walkthrough of the most relevant capture skill (e.g. `add-bill` if "bills are stressful" was the top pain point).
- **Heavy-import path (opt-in):** after the quick-start, init asks "Do you want me to look for data sources to enable now?" and probes for every supported MCP / CLI tool. For each one found, init shows what it can do, asks whether to enable, and (if yes) runs a first ingest with the conservative `recency_window_days` default. The user can opt out at any prompt and run `ingest --setup` later.
- **No silent enables.** Init never enables a source without an explicit yes.
