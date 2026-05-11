# Payment Confirmation Capture Contract

<!-- Citation form: `contracts/payment-confirmations.md`. -->

Governs how Superagent captures, files, indexes, and cross-links the artifact produced whenever **money changes hands** on the user's behalf or with the user's involvement.

The contract is universal: anytime a skill helps with a payment (`bills mark-paid`, `subscriptions update`, `vehicle-log` for a service paid, `add-appointment` for a copay, `expenses`, `draft-email` answering "here is my payment confirmation", an `ingest` run that surfaces a fresh charge, or a future "auto-pay this bill" workflow) it must follow this contract — no exceptions for "small" payments.

---

## 1. Trigger

This contract activates when **any** of these are true in the current turn:

- A skill submits a payment upstream (auto-pay, agent-initiated transfer, etc.).
- The user reports a just-made payment ("I just paid X", "paid the dentist today").
- The user shares / forwards / pastes a payment artifact (PDF, screenshot, email body, portal HTML, wire confirmation, money-order stub, check image, in-app receipt).
- An ingestor surfaces a fresh transaction that matches an open bill, subscription, appointment, or project task (auto-capture per `contracts/capture.md § 7.3`).

The skill should NOT skip capture because the artifact "is just sitting in email" or "is in my online banking" — those locations are not durable enough; pull it into the workspace.

---

## 2. What the artifact must contain

Save the original confirmation when one exists (PDF / HTML / image / email source). When no native artifact is available (the user reports a payment verbally, or pastes raw text), the agent generates a markdown stub and saves that instead.

Every saved artifact (or stub) MUST carry these fields, either in the native document or in an accompanying frontmatter / sidecar `.ref.md`:

- `payee` — who got paid
- `amount` and `currency`
- `payment_date` (ISO 8601)
- `method` — payment rail and last-4 / source identifier (e.g. `card-1234`, `ACH from checking-5678`, `check #1042`, `money-order #M-…`, `wire`)
- `confirmation` — confirmation / reference / tracking number when issued
- `purpose` — one-line description of what the payment was for
- `related_entities` — list of operational handles per `contracts/operational-handles.md` (e.g. `[bill:pge-electric, appointment:20260512-dr-smith-cleaning, project:tax-2026]`)
- `provenance` — per `contracts/provenance.md` (source: `user` | `<skill>` | `<ingestor>`; at; verified_at)

If the user pastes a confirmation into chat, transcribe the fields into a markdown file and save that — do NOT rely on chat history as the canonical record. Chat scrollback is not memory.

---

## 3. Where to save it — destination decision

Decide between two destinations based on **what the payment was for**, not on dollar amount.

### 3.1 `workspace/Sources/<Domain>/...` — long-lived / auditable payments

Use `Sources/` whenever the payment relates to a significant life area where the artifact could matter months or years later. Rule of thumb: anything that could be audited, claimed on a tax return, contested, or needed for warranty / title / record-keeping / handoff (`handoff` skill).

Canonical default domains (seeded by `init`) and their typical payment classes:

| Domain | Typical payment classes routed to `Sources/<Domain>/` |
|---|---|
| **Finances** | mortgage / rent, loan paydowns, brokerage transfers, large internal transfers, wire fees, banking fees worth a record |
| **Taxes** *(or `Finances/Taxes/`)* | federal / state / local income tax, property tax, estimated quarterlies, FBAR-related, filing fees |
| **Home** | HOA dues, homeowner's insurance, capital improvements, major repairs that affect cost basis |
| **Vehicles** | registration / DMV fees, insurance premiums, major service invoices, tickets, smog/inspection fees |
| **Health** | out-of-pocket medical, dental, vision, therapy, HSA / FSA-eligible receipts, copays for chronic-care visits |
| **Pets** | vet bills worth keeping (annual exam, dental, surgery), pet insurance premiums |
| **Family** | tuition, school fees, childcare, dependent-care payments |
| **Career** | professional dues, license / certification renewals, business-expense receipts the user will claim |
| **Travel** | trip-related receipts the user will reimburse / claim (otherwise use the trip's project, per § 3.2) |
| **Self / Legal / Government** | passport / visa fees, court costs, license renewals, filing fees, notary, immigration |

The exact sub-folder layout under `Sources/` is **user-defined** (per `contracts/sources.md` § 15.1). The agent picks an existing folder when one obviously fits; only creates a new subfolder when no good fit exists. Custom workspaces may override defaults — check `workspace/_custom/rules/` for per-user folder-naming preferences first.

**Filename**: lowercase, hyphenated, date-prefixed.
`YYYY-MM-DD-<payee-slug>-<purpose-slug>.<ext>`
Examples: `2026-05-11-pge-electric-confirmation.pdf`, `2026-q1-irs-estimated-tax-confirmation.pdf`, `2026-05-11-dr-smith-dental-copay.pdf`.

### 3.2 `workspace/Projects/<project>/Resources/` — project-scoped / time-bounded payments

Use the project's `Resources/` folder when the payment is bound to a time-limited project and has no independent long-term value once the project closes. Examples:

- single-purpose purchases for a project (parts, tools, supplies, one-off contractor payments under the project budget)
- registration fees for events tied to a project
- printing / shipping / notarization / lab fees incurred as part of the project workflow
- small reimbursable purchases the user expects to wrap up when the project closes

Filename convention: same `YYYY-MM-DD-<payee>-<purpose>.<ext>`. After saving, append a row to the project's `history.md` and link with a workspace-relative path.

### 3.3 Cross-domain payments (when both could apply)

If a payment relates to **a project that itself sits inside a major domain** — e.g. a property-tax payment made via a `Projects/property-tax-<year>/` project, a medical-procedure payment under `Projects/<surgery-recovery>/`, a tuition payment under `Projects/<school-year>/` — save the **canonical copy** to `Sources/<Domain>/...` and **cross-link** from the project's `history.md` and (optionally) a `.ref.md` pointer in `Projects/<project>/Resources/`. Do NOT duplicate the binary file.

### 3.4 Tie-breaker

If a confirmation could plausibly live in either place, **prefer `Sources/`**. The cost of over-archiving is a slightly noisier sources index; the cost of under-archiving is a missing receipt years later when it matters. State the chosen destination in one line in the agent's reply so the user can redirect on the spot.

---

## 4. What to do after saving

Every save MUST trigger the following side-effects, in order:

1. **Refresh the Sources index** when saving into `Sources/`:
   `python3 -m superagent.tools.sources_index refresh` (mtime-lazy — near-no-op when nothing changed).

2. **Register as a document (optional but recommended)** for confirmations that matter long-term (taxes, large medical, property, vehicles): file a `documents-index.yaml` row via the `add-document` skill with `kind: receipt` (or `kind: tax_return` / `property_tax` when applicable). This makes the artifact queryable by domain and surfaces it in `monthly-review` and `handoff`.

3. **Update the related entity** (whichever applies):
   - **`bills.yaml.<bill>.history[]`** — append a new history row with:
     - `date`, `amount`, `confirmation`, `source` (per existing schema)
     - **NEW**: `confirmation_ref: "<workspace-relative-path-to-saved-artifact>"`
   - **`subscriptions.yaml.<sub>.history[]`** — same fields.
   - **`appointments.yaml.<appt>.confirmation`** — set + add `confirmation_ref`.
   - **`Projects/<project>/status.md`** — mark the related task done if applicable, link to the saved artifact.
   - **`Domains/<domain>/history.md`** — append a 1-line entry for material payments (taxes, property tax, major medical, large purchases). Routine recurring-bill payments can skip the domain-history entry if `bills.yaml.<bill>.history[]` already records them.
   - **`_memory/todo.yaml`** — close any todo whose closing condition is this payment; link the confirmation_ref.

4. **Append to `interaction-log.yaml`** with `kind: payment_confirmation_saved`, citing:
   - `path` — saved artifact path (workspace-relative)
   - `payee`, `amount`, `currency`, `payment_date`
   - `related` — list of operational handles touched
   - `destination_class` — `sources` | `project_resources`

5. **Mirror to the events stream** (per `contracts/events-stream.md`) — `auto_mirror_history_md: true` handles this when the entity's `history.md` is updated; explicit mirror is only needed when the save did not write to any `history.md`.

6. **Provenance** — write the `provenance` block on the saved artifact's frontmatter (or sidecar `.ref.md`) per `contracts/provenance.md`. For ingestor-sourced confirmations, also include the `ingestion_log_row` reference.

7. **Sensitive routing** — if the artifact contains a full account number, full card number, SSN, or medical detail beyond a generic receipt line, route per `contracts/sensitive-tier.md` (rename file with `.sensitive.<ext>` suffix and/or move under a `_memory/sensitive/`-routed path). Default visibility is `private` per `contracts/visibility.md`; mark `household` only when the user has explicitly enabled household-shared receipts.

---

## 5. User-driven captures ("I just paid X")

When the agent is not the one clicking "Pay" — the user shares a confirmation screen, forwards a receipt email, or simply mentions a completed payment — the agent should proactively offer to capture it per this contract.

Default UX:

1. First payment-confirmation moment per session: ask `Capture this confirmation → <proposed destination>? [yes / edit destination / skip]`.
2. After the first explicit `yes` in a session: assume yes for subsequent same-session payment confirmations unless the user says "stop capturing these" or analogous. Announce the destination in one line per save so the user can correct on the spot.
3. Per-session preference does NOT persist across sessions. Set `_memory/config.yaml.preferences.payments.auto_capture: true` to flip the default to "yes silently". Default: `null` (ask once per session).

---

## 6. Local-first read before save

Before writing a new confirmation, run the local-first read order (`contracts/local-first-read-order.md`):

1. Search `sources-index.yaml` for an already-filed copy (dedupe key: payee + amount + payment_date within ±1 day).
2. If a match exists, **do not duplicate**. Update the existing entry's metadata if the new artifact has more detail; otherwise just cross-link.
3. Skip live MCP / API calls for de-dup — the local index suffices.

---

## 7. Project archival hand-off

When a project's lifecycle moves to `completed → archived` (per `contracts/projects.md`), the `projects` skill MUST scan `Projects/<project>/Resources/` for payment confirmations and ask the user, per artifact, whether to:

- **Promote to Sources** (capital improvements, tax-relevant receipts, warranty-relevant invoices, professional-development claims) — moves the file under the appropriate `Sources/<Domain>/`, updates `documents-index.yaml`, leaves a `.ref.md` breadcrumb at the original project path.
- **Leave in project archive** (genuinely time-bound and uninteresting once the project closes).
- **Discard** (rare; only on explicit user request, never auto).

Default proposal: promote when the receipt's `purpose` slug matches `tax`, `home-improvement`, `capital`, `medical`, `vehicle-major-service`, `professional-dues`, `license`, `permit`, `government`, `legal`. Otherwise leave in place.

---

## 8. Failure modes the agent must guard against

- **Silent skipping** — "this is small, the user wouldn't want a file". WRONG. Capture by **what** the payment is, not the dollar amount.
- **Chat-only memory** — quoting the confirmation in the reply and not saving a file. WRONG. The reply is ephemeral; the workspace is canonical.
- **Duplicate files** — saving the same receipt to both `Sources/<Domain>/` and `Projects/<project>/Resources/`. WRONG. One canonical copy + cross-link.
- **Skipping the index refresh** — saving the file and not running `sources_index refresh`. WRONG. The next local-first read will miss the new artifact.
- **Skipping `confirmation_ref`** — adding the bill-history row without the path to the saved file. WRONG. The reconciliation skill will not be able to find the underlying artifact later.
- **Routing sensitive content to `Sources/`** — full SSN, full account number, full card number, medical details. WRONG. Route through `contracts/sensitive-tier.md`.

The anti-pattern scanner (`tools/anti_patterns.py`) flags skills that emit text like "I noted your payment" without a corresponding file-write tool call in the same turn.

---

## 9. Per-user overrides

This contract codifies the universal protocol. A user can refine routing under `workspace/_custom/rules/payment-confirmations.md` (per `contracts/custom-overlay.md`) — typically to:

- pin specific folder names (`Sources/Property Tax/<County>/<Year>/` instead of `Sources/Home/`)
- raise / lower thresholds for "ask vs auto-save"
- change the per-session default to always-auto

The framework contract runs first; the custom overlay is appended on top per the custom-overlay merge rule.

---

## 10. Citing this contract

Skills that touch payments MUST cite this contract in their frontmatter / steps:

- `bills.md` mark-paid → after appending to `history[]`, follow `contracts/payment-confirmations.md`.
- `subscriptions.md` update → same.
- `appointments.md` post-visit → save copay/receipt per this contract.
- `vehicle-log` service entries → save invoice per this contract.
- `expenses` skill (when added) → all expense entries flow through this contract.
- `ingest` (finance ingestors) → when a charge matches an open bill/sub/appt, the auto-capture pass invokes this contract.
- `draft-email` when sending a "here is my proof of payment" reply → save the user's outgoing-payment artifact first.

Cross-references: `contracts/sources.md`, `contracts/capture.md`, `contracts/local-first-read-order.md`, `contracts/provenance.md`, `contracts/operational-handles.md`, `contracts/projects.md`, `contracts/sensitive-tier.md`, `contracts/visibility.md`, `contracts/events-stream.md`, `contracts/custom-overlay.md`.
