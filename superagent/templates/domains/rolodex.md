# Rolodex — {{DOMAIN_NAME}}

> **[Do not change manually — managed by Superagent]**

<!--
  Rolodex (contact directory) for this domain (4-file structure).

  Sync contract: every Superagent skill that processes a touchpoint involving
  a person in this domain (log-event, health-log, vehicle-log, pet-care,
  appointments, draft-email, summarize-thread, ingestors, etc.) MUST add or
  update a row here when it sees a new person or a new touchpoint with an
  existing person. See contracts/domains-and-assets.md (rolodex sync).

  Authoritative contact records live in `_memory/contacts.yaml`. This file
  is a domain-scoped projection — same person can appear in multiple
  rolodexes if they're relevant to multiple domains (e.g. your dentist
  appears in both Health and (if you happen to also run a side-business
  with them) Career).
-->

_Last updated: {{LAST_UPDATED}}_

---

## Table of Contents

- [Rolodex — {{DOMAIN_NAME}}](#rolodex--domain_name)
  - [How this file stays current](#how-this-file-stays-current)
  - [Providers & Professionals](#providers--professionals)
  - [Family & Personal](#family--personal)
  - [Vendors & Services](#vendors--services)
  - [Other](#other)

---

## How this file stays current

- New people mentioned in interactions, appointments, ingested data, or chat for this domain are appended here automatically by Superagent skills.
- Existing rows get their **Last contacted** and **Notes** columns refreshed on each interaction.
- Do not delete rows — mark as `(no longer used)` in **Notes** instead so history stays auditable.

---

## Providers & Professionals

<!-- Doctors, dentists, mechanics, lawyers, accountants, financial advisors,
     contractors, anyone you pay for service. -->

| Name | Role | Phone | Email | Notes | Last contacted |
|------|------|-------|-------|-------|----------------|
| {{P_1_NAME}} | {{P_1_ROLE}} | {{P_1_PHONE}} | {{P_1_EMAIL}} | {{P_1_NOTES}} | {{P_1_LAST_CONTACTED}} |

{{PROVIDERS_TABLE_ROWS}}

---

## Family & Personal

<!-- Family members, friends, neighbours involved in this domain. -->

| Name | Relationship | Phone | Email | Notes | Last contacted |
|------|--------------|-------|-------|-------|----------------|
| {{F_1_NAME}} | {{F_1_RELATIONSHIP}} | {{F_1_PHONE}} | {{F_1_EMAIL}} | {{F_1_NOTES}} | {{F_1_LAST_CONTACTED}} |

{{FAMILY_TABLE_ROWS}}

---

## Vendors & Services

<!-- Companies / services you do business with regularly in this domain
     (utility-company support lines, insurance carriers, subscription
     providers' customer-service contacts, the local hardware store you
     trust, etc.). -->

| Name | Service | Phone | URL | Notes | Last contacted |
|------|---------|-------|-----|-------|----------------|
| {{V_1_NAME}} | {{V_1_SERVICE}} | {{V_1_PHONE}} | {{V_1_URL}} | {{V_1_NOTES}} | {{V_1_LAST_CONTACTED}} |

{{VENDORS_TABLE_ROWS}}

---

## Other

<!-- Anything not in the above buckets. -->

{{OTHER_CONTACTS}}
