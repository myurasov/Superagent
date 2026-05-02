# Superagent — Quick start

Goal: have something useful in **5 minutes**, with no data-source setup required.

---

## 1. Open this directory in Cursor or Claude Code

Both work. Anything else that can read files and follow markdown instructions also works.

## 2. Tell the agent

In the chat:

```
Follow AGENTS.md and run init.
```

The agent will:

1. Read `AGENTS.md` (the canonical operating rules).
2. Find `superagent/skills/init.md` and follow it.
3. Ask three quick questions:
   - What should I call you?
   - Who's in your household?
   - What's the most painful personal-admin friction right now?
4. Scaffold `workspace/` with all 22 memory files and the 10 default Domain folders.
5. Ask whether to probe for available data sources (you can say "later").
6. Walk you through one capture skill that matches your top pain point.

That's the quick-start. You're done.

## 3. The five commands you'll use most

```
whatsup                   # 30-second status check
daily-update              # full morning briefing
weekly-review             # Friday-afternoon / Sunday-evening wrap
add-bill                  # capture a recurring bill
add-appointment           # capture an upcoming appointment
```

Or just describe what you want in plain English — the agent matches your phrasing against the skill catalogue.

## 4. Add data sources later, when you trust it

When you're ready to make Superagent know your life better than you do:

```
ingest --setup
```

The agent probes your machine for every supported data source (email, calendar, banks, health, smart home, notes, photos, location), shows you what's available, and lets you enable each one with a single yes. Heavy backfills (e.g. five years of email or three years of bank data) are a separate explicit invocation:

```
ingest gmail --backfill
```

Full catalogue of supported sources: `superagent/docs/data-sources.md`.

## 5. When something feels off, run

```
tailor-review
```

The Supertailor reviews the framework itself — hygiene + strategic-improvement passes — and proposes ranked changes. Approved changes either land in your private overlay (`_custom/`) or, if generic and safe, in the committed framework code (with safeguards that prevent any of your personal data from leaking into a commit).

## 6. The vault analogy

Think of `workspace/` as a personal vault. Everything in there is local to your machine. Nothing is sent anywhere. The agent reads from it, writes to it, but never publishes it.

When you want to share something (an email draft, a printable checklist, a contractor packet), it lands in `workspace/Outbox/` — that's the doorway. You decide what leaves.

## 7. If you want to extract this to its own repo later

Superagent is designed to be self-contained. Copy the `superagent/` folder to a new repo, add a single `.gitignore` line for `workspace/`, and you're done. See `superagent/docs/architecture.md` § "Extracting to a standalone repo" for the exact recipe.
