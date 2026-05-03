# Privacy — what never leaves the workspace as written

Complements the AGENTS.md "Framework Artifact Creation Contract" safeguard
(which prevents personal-data tokens from landing in committed framework
code) and the Visibility Contract (`procedures.md` § 20).

These rules govern what the agent writes into ARTIFACTS the user might share
(`workspace/Outbox/`, draft emails, printed lists, contractor packets). The
gitignored workspace itself is local-only either way; the concern here is
"things I might forward".

## Names

* Never write my partner's, kids', or parents' last names in any artifact
  destined for `Outbox/`. First names only.
* Never write a contact's full name + employer + city in the same artifact;
  pick at most one identifier.

## Addresses

* Never write my home street address in a committed file or in any
  `Outbox/` artifact unless I explicitly say so for that artifact.
* When citing a Sources document that contains my address, link to the
  document by name; don't quote the address itself.

## Account / card numbers

* Redact account numbers to last-4 in any `Outbox/` artifact.
  (`****1234`, never `12345678901234`.)
* Never paste card numbers, CVVs, or expiration dates anywhere. Reference
  the account by `account-id` and let me look up the rest in my vault.

## Photos

* Strip EXIF GPS from any photo I drop into `Inbox/` before moving it to
  a destination outside `_memory/sensitive/`.
* Never embed a photo of an ID document (license, passport) into an artifact
  unless it's in `Outbox/sealed/handoff/`.

## Health

* Health details are visibility=private by default. Never include
  medication names, conditions, or vitals in any artifact going to
  `Outbox/` unless the artifact's purpose is itself medical
  (`medic-prep` packet, doctor-bound document).
