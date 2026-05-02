# Superagent — Skills reference

The full list of skills shipped with the framework, with the natural-language phrasing the agent listens for and a one-paragraph description of what each does.

To invoke a skill, just say what you want in plain English. The agent matches your phrasing against each skill's `triggers:` frontmatter (or against the skill's name) and reads the skill's full instructions before acting.

| Skill | Lifecycle | Description |
|---|---|---|
| **init** | first-run | Onboarding questionnaire (3 questions), workspace scaffold, optional data-source probe, walk-through of one capture skill. Quick-start works without ingestion; heavy import is opt-in. |
| **whatsup** | every check-in | Quick delta since last `last_check`: bills due, today's appointments, important dates, P0/P1 tasks, alerts. Designed to be the morning command. |
| **daily-update** | daily | Runs scheduled ingestors, then composes the full daily briefing (P0 block, today, this week, health signals, inbox highlights, alerts, suggested next moves). Updates `last_check`. |
| **weekly-review** | weekly | Bookkeeper + Coach + Concierge + Quartermaster passes for the trailing 7 days. Captures one or two reflection notes into `Domains/Self/history.md`. |
| **monthly-review** | monthly | Subscription audit, document expirations, vehicle / home maintenance windows, financial recap, health overdue items, domain hygiene. |
| **todo** | capture / surface | Add / list / complete / update tasks in `_memory/todo.yaml`. P0-P3 priority rules with overdue awareness. Syncs `status.md` / `todo.md` after every change. |
| **add-domain** | capture | Bootstrap a new life domain beyond the 10 defaults (Boat, Cabin, Side-business, Volunteer, …). Creates folder + 4-file structure + index row. |
| **add-asset** | capture | Register a physical asset (vehicle, appliance, electronics, jewelry, instrument, tool). Optional: seed recurring maintenance schedule from per-kind defaults. |
| **add-contact** | capture | Register a person (family, friend, professional, provider, vendor, neighbour). Auto-syncs into the relevant domain rolodex(es). |
| **add-account** | capture | Register a financial / utility / subscription account. Last-4 only; full credentials reference a vault entry (`vault_ref`). |
| **add-bill** | capture | Register a recurring or one-shot bill. Computes `next_due` immediately. |
| **add-subscription** | capture | Register a recurring subscription. Sets a trial-ending guard if applicable. |
| **add-appointment** | capture | Register an appointment (medical / dental / vet / mechanic / school / etc.). Auto-creates a P1 prep task if `prep_notes` is non-empty. |
| **add-important-date** | capture | Register a recurring date (birthday, anniversary, deadline, observance). Recurrence-aware. |
| **add-document** | capture | Register an important document (passport / license / deed / will / insurance policy / warranty). Tracks expiration dates. |
| **log-event** | capture | One-shot capture for "I just …". Routes the event to the right domain and the right specialized log skill (health / vehicle / home / pet / interaction). |
| **health-log** | capture | Log a symptom, vital reading, medication change, visit, vaccination, or lab result for self or any household member. |
| **vehicle-log** | capture | Log a vehicle event — service, fuel-up, mileage, repair, accident, registration / inspection / insurance renewal. |
| **home-maintenance** | capture / surface | Track and log home-care tasks (HVAC service, filter changes, gutters, pest, smoke detectors, water heater). |
| **pet-care** | capture / surface | Per-pet vet schedule, vaccinations, medications, weight, food, grooming, boarding. |
| **bills** | surface | List, mark-paid, reconcile against ingested transactions. |
| **subscriptions** | surface | List, audit (cancel candidates), log-use, cancel, detect new recurring charges. |
| **appointments** | surface / action | List, prep (assemble briefing context), mark complete (with outcome + follow-ups), reschedule, cancel. |
| **important-dates** | surface | List, draft a greeting, suggest a gift, mark acknowledged. |
| **expenses** | surface | Categorize and review spending; spot anomalies; cross-reference against bills + subscriptions; tag tax-deductible categories. |
| **draft-email** | action | Compose a personal email with full context. Renders to `Outbox/emails/`. Read-only against email sources unless explicitly authorized. |
| **summarize-thread** | action | Condense a long email or message thread into key points, decisions, action items. |
| **follow-up** | action | Hunt for dropped balls — overdue tasks, unanswered messages, unfulfilled commitments, stale appointments. |
| **research** | action | Research across local notes, ingested email / messages, web, and any knowledge MCPs (Obsidian / Notion). |
| **ingest** | action | Front-end to the ingestor catalogue. `status` / `setup` / `run --source X` / `run --all` / `run --backfill` / `--reauth` / `--disable`. |
| **personal-signals** | capture / surface | Ambient capture of self-development feedback; on-request surface of growth themes. Writes to `_memory/personal-signals.yaml` and rolls up into `Domains/Self/history.md`. |
| **triage-overdue** | action | Force a decision on every overdue task. Done / reschedule / drop priority / cancel / delegate / skip. |
| **handoff** | action (sensitive) | Generate the "if hit by a bus" packet — accounts, documents, beneficiaries, emergency contacts, household routines, vault references. Written to `Outbox/handoff/` with prominent storage warnings. |
| **doctor** | hygiene | Workspace data hygiene — stale domains, duplicate contacts, near-duplicate todos, simplification candidates, broken cross-references, expired documents. Asks per pass which findings to action. |
| **supertailor-review** | hygiene + meta | Two-pass framework review (hygiene + strategic). Hygiene applies mechanical reversible repairs (Supercoder writes them); strategic surfaces ranked framework-improvement suggestions, tagged `superagent` or `_custom` (both routed through the Supercoder for implementation). |
| **pm-review** | project review | Project-manager-angle review of every personal-life Project under `workspace/Projects/` — RAG status, stall detection, deadline pressure, scope drift, cross-project resource / date conflicts, dropped balls, charter hygiene, top 3 next moves. Distinct from `weekly-review` / `monthly-review` (time-based) and from `supertailor-review` (framework-meta). |

## Skill discovery

The agent uses three layers to find the right skill for what you said:

1. **Exact name match** — "run init" / "do `supertailor-review`" → that skill.
2. **Trigger-phrase match** — your text contains a phrase from any skill's `triggers:` frontmatter list.
3. **Domain hint** — your text mentions a domain (Health, Finance, …) → biases toward skills that operate on that domain (`health-log`, `expenses`, …).
4. **Verb hint** — "add", "log", "list", "draft", "research", "what's", "summarize" → maps to the skill family with that verb.

When the match is ambiguous, the agent asks rather than guesses.

## Per-user skills

Drop a `<your-skill-name>.md` into `workspace/_custom/skills/`. The frontmatter shape is the same as framework skills. The agent treats it as first-class — invokable by `triggers:` or by name. Custom skills can call any framework skill (e.g. `add-task` could be a custom convenience wrapper around `todo`).

On same-name collision between framework and custom, the framework version runs first; the custom version is then applied as an addendum (extra steps). The agent surfaces this with a banner: *"Using `_custom/skills/<name>` as addendum to framework `<name>`."*
