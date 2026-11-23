---
name: <name>
kind: durable
description: >-
  <One or two lines: what the role is for and where it stops.>
skills: [<skill>, <skill>]
owns:
  - <workspace-relative path the role is the main writer of, e.g. _memory/<file>.yaml>
authority: act
model_tier: runner
duties:
  - id: <duty-id>
    skill: <skill>
    tier: act
    schedule: on-request
    check: none
---

<!--
  Author guide (delete this block when the file is filled in):
  - Spec: contracts/agents.md. The filename is `<name>.agent.md` and `name` repeats it.
  - kind: durable (always-on) or project (one instance per project). `lead` is taken by
    the framework's `superagent` role.
  - skills: stems of skill files that exist (framework or `_custom/skills/`).
  - authority and each duty's tier: act | approve | prepare | owner (contracts/autonomy.md).
    A duty's tier is never looser than the role's authority.
  - schedule: a key of `config.preferences.cadences`, `watchlist`, or `on-request`.
  - check: cadence | none, or `tool:<name>` for a script of your own,
    `workspace/_custom/tools/<name>.py` (exit 0 due, 1 not due; see the `add-agent` skill).
  - A custom role lives in `workspace/_custom/agents/` and its name differs from every
    framework role: a same-named file there is an overlay on the framework role.
  - Write the dev twin `<name>.agent.dev.md` beside this file (template `agent.dev.md`).
-->

# <Name> — Agent Role Definition

The frontmatter above is the machine-readable part of the role (`contracts/agents.md`). This file is the rest: what the role is for, what it does, and where it stops.

## Purpose

<What the role is for, in a short paragraph. What is in its scope and what belongs to another role.>

## What it does

- <The work, one line per kind of work, naming the skill that does it.>

## Boundaries

- <What the role never does. Sending, paying, asking the owner: say which apply (`contracts/autonomy.md`).>

## Duties

| Duty | Skill | Schedule | Check |
|---|---|---|---|
| `<duty-id>` | `<skill>` | <when> | <what the check looks at> |

## With no separate agents running

<Which requests make a chat session take this role.>

## Hand-offs

- **To the lead**: <what goes to the lead>.

## Changes to this role

The running agent never edits this file, its checks or its duties. It files a change request (an action signal with `target: tailor`), and its dev twin builds the change (`<name>.agent.dev.md`, `contracts/agents.md` § "Dev twins").
