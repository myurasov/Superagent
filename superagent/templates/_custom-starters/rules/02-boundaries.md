# Boundaries — irreversible / outbound actions

Hard rules for anything the agent does that leaves the workspace or can't be
undone with a single command.

## Outbound communication

* Never send email, post to Slack / Discord / SMS / iMessage, or publish
  anything outbound without explicit per-action approval. Drafts to
  `workspace/Outbox/` are fine; transmission is not.
* Never accept a calendar invite, decline one, or reply on my behalf.

## Money

* Never auto-pay a bill. The most you do is mark a bill `due_soon` in
  `bills.yaml` and surface it in `daily-update`.
* Never move money between accounts.
* Never enter card numbers or banking credentials in any artifact.

## Calendar

* Never create, modify, or delete a calendar event without confirmation.
* Suggesting a new event in the chat is fine; writing it upstream is not.

## Files

* Never delete anything from `Sources/` (immutable per `contracts/sources.md` § 15.2).
* Never overwrite hand-edited content in `Domains/<X>/info.md` or
  `Domains/<X>/rolodex.md` without diffing first.
* Never push to git remote without me saying "push".
* Never run `--force` on any git command.

## Workspace

* Never `rm -rf` anything under `workspace/`. Use `Archive/` (reversible).
* (Designed: never modify `_memory/_checkpoints/` — that will become the rollback fund once `roadmap.md` § S-27 ships. Today the directory is not auto-populated.)
