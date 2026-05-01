---
name: superagent-research
description: >-
  Research a topic across local notes, ingested email / messages, web,
  and any knowledge MCPs (Obsidian / Notion). Synthesizes a structured brief.
triggers:
  - research <topic>
  - what do I know about <topic>
  - find me notes on <topic>
  - look up <topic>
mcp_required: []
mcp_optional:
  - obsidian / notion
  - web search (if available in current IDE)
cli_required: []
cli_optional: []
---

# Superagent research skill

## 1. Parse the query

Determine:
- **Topic** — what the user wants to learn about.
- **Scope** — broad ("everything I know about <X>") or narrow ("<X>'s warranty terms").
- **Source preference** — if user specifies ("check my notes", "search the web"), prioritize.

## 2. Search local first

Before any external call:

- **`_memory/`**: grep across `interaction-log.yaml`, `insights.yaml`, `procedures.yaml` for topic mentions.
- **Domain files**: scan `Domains/*/info.md`, `status.md`, `history.md` for mentions.
- **Sources**: scan `_memory/sources-index.yaml` (canonical doc index) and grep `Sources/_cache/<*>/_summary.md` (cached external refs).
- **Resources**: list relevant files in any `Resources/` folder by name (working drafts and previously-rendered briefings).
- **Email / message mirrors**: search `_memory/emails/` and `_memory/slack/` (if those mirrors exist) for thread subjects matching.

Always start with what's already known.

## 3. Search MCPs (parallel where possible)

For each available knowledge MCP:

### Obsidian
- Search the configured vault.
- Fetch the most relevant 3 notes; pull frontmatter + first 500 chars.

### Notion
- Search across pages / databases.
- Pull metadata; only fetch full content for top 1-2.

## 4. Web search (if available)

If the IDE provides a web search capability and the user's query benefits from it (e.g. "what's the recommended replacement schedule for a 2018 Camry timing belt?"), run a focused search. Cite sources clearly.

## 5. Synthesize

Structure:

```
## Research Brief: <topic>

**Sources checked**: <list>
**Sources unavailable**: <list, if any>

### Summary
<2-3 sentence executive summary>

### What's already in your workspace
- <local finding 1> (from <file>)
- <local finding 2>

### What I found in <Obsidian / Notion>
- <Note title> — <one-line>

### What I found on the web
- <article title>: <url> — <one-line>

### Gaps
- <topics where no source had useful info>
```

## 6. Offer next steps

Suggest:
- "Want me to add key points to <Domains/<inferred>/info.md> § Key Facts?"
- "Want me to draft an email sharing these findings with <person>?"
- "Should I create a task to follow up on <gap>?"
- "Want me to save this brief?" (default destination: `workspace/Domains/<inferred>/Resources/research/<topic>-<date>.md`)

## 7. Logging

```yaml
- timestamp: <now>
  type: skill_run
  subject: "research"
  summary: "Researched <topic> across <source list>."
  related_domain: <inferred>
```
