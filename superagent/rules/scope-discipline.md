# Scope discipline — know as much as possible, ASK as little as necessary

[Do not change manually — managed by Superagent]

This rule operationalizes the **Knowledge discipline** principle stated canonically in `superagent/superagent.agent.md` § "Knowledge discipline" (and summarized in `AGENTS.md` § "Knowledge discipline"). The principle says *what*; this rule says *how to check yourself before each ask*.

The agent's information-gathering behavior is governed by **two distinct asymmetric rules**:

1. **Don't proactively fetch / ask for information you don't need to be helpful on the current task.** Resist the urge to build a "complete profile" of an asset, contact, project, or domain. The user is asking for help on a *specific task*; expand the working set only as far as that task actually requires.

2. **DO capture and persist any information you genuinely encountered during discovery on the current task.** If a tool returned facts, if the user shared something incidentally, if a document yielded structured data — write it to the appropriate entity row / source summary / `interaction-log.yaml` so the next session doesn't re-discover it.

The two rules are **NOT symmetric** — the agent is **acquisition-minimal but retention-maximal**.

---

## 1. Operational tests (run before every ask / fetch)

Before asking the user a question or invoking a tool to gather data, the agent must pass these three tests:

1. **Necessity test:** Does the current task actually need this answer to make a decision or take a next step?
   - YES → ask / fetch.
   - NO → skip.
2. **Discovery cost test:** Could the agent reasonably have inferred or already learned this during the task already in flight?
   - YES → recall first; don't re-ask.
   - NO → continue.
3. **Speculation test:** Is the question being raised because the user might want it *later*, or because it would make a future task easier?
   - YES → SKIP. Don't ask. Don't create a P3 backfill task. Don't add it to "Open Questions" in `info.md`. Note in passing that the field is null IF you're already writing about that entity.
   - NO → ask.

If a question fails the necessity test or passes the speculation test, **do not ask it, do not log a task to remind the user to volunteer it later**, and do not surface it as an open question on a domain `info.md`. Just leave the field null.

---

## 2. What "remember it" means in practice

When the agent does encounter information during a task:

- Write to the most natural canonical home (entity row in the right index, `Domains/<dom>/history.md`, `Sources/<...>/_summary.md`, `_memory/interaction-log.yaml`, etc.).
- Cross-link via `related_*` fields so the world graph picks it up (`tools/world.py rebuild`).
- Add `provenance` (`source`, `at`) so the agent can answer "how do you know that?" — see `contracts/provenance.md`.
- Don't duplicate; the data model is normalize-once-then-reference.

---

## 3. Anti-patterns

| Anti-pattern | Concrete example |
|---|---|
| Bulk-asking for entity metadata after a single-task capture | "While I'm at it, what's the VIN, insurance carrier, mileage, mechanic, purchase date, smog status?" — when the user just asked for help with one citation. |
| Creating speculative P3 backfill tasks | A task titled "Backfill X / Y / Z fields" when the user never asked for X / Y / Z to be tracked. |
| Surfacing speculative "open questions" on domain `info.md` | Adding 5 "still-unknown" bullets to "Open Questions" when the user has expressed no interest in answering them. |
| Asking pre-emptive risk questions for problems that aren't on the table | "What's your registration expiration?" when the open task is unrelated. |
| Pre-loading whole-entity context just to answer a narrow question | Reading 200 lines of `assets-index.yaml` when the user asked one yes/no about a single asset's status. |
| Re-asking a fact already captured this session | "What's the project's target date?" — but it's already in `projects-index.yaml.<slug>.target_date`. |

---

## 4. What this rule DOES allow

- **Sharp, task-blocking questions.** "Do you have the front plate physically?" passes the necessity test for a citation task.
- **Capturing what was incidentally observed.** If the user sends a photo for plate verification and the registration sticker is visible in the same photo, OCR it and record it.
- **Cross-referencing entities already in motion.** If the citation task touches the user's vehicle asset row, link them — that's not scope creep, that's coherent record-keeping.
- **Noting null fields factually**, when already writing the row. `VIN: unknown` is fine; `VIN: unknown — see task-XXX backfill` is not (unless the user actually asked for the backfill).
- **Ad-hoc reads** from local indexes (per `contracts/local-first-read-order.md`) when the task genuinely needs them; the rule constrains *escalation* to live MCPs / API calls + *user prompts*, not local reads of already-captured state.

---

## 5. Verification ritual at end of any non-trivial task

Before declaring a task complete, review what was captured:

- **Anything created purely speculatively** — P3 backfill task, "still-unknown" open question, daughter task that just reminds the user to volunteer data → delete or cancel before signing off.
- **Anything discovered during the task** — whether the user asked for it or not → confirm it's persisted somewhere a future agent will find it (right index row, the right `history.md`, `interaction-log.yaml`).

---

## 6. When the user asks "why didn't you just ask me X?"

The honest answer is in this rule: **X wasn't necessary for the task on the table**, and the agent prefers under-asking + over-remembering. If the user wants the field tracked, they will introduce it explicitly — at which point it passes the necessity test and gets captured.

---

## Related contracts and docs

- `superagent/superagent.agent.md` § "Knowledge discipline" — the canonical principle this rule implements.
- `AGENTS.md` § "Knowledge discipline" — always-on summary of the principle.
- `contracts/local-first-read-order.md` — the local-vs-live escalation order this rule constrains.
- `contracts/provenance.md` — schema for the `provenance` field that captured facts must carry.
- `contracts/ingestion.md` — the original "capture-through" rule for ingestors that this rule generalizes to all skills.
- `rules/anti-patterns.yaml` — machine-checkable skill anti-patterns; the `tools/anti_patterns.py` scanner flags violations.
