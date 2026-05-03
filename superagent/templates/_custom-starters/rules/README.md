# Custom rules — starter pack

Drop any of these files into `workspace/_custom/rules/` and edit them in place.
Each starter is a short markdown file holding a list of natural-language
instructions the agent should follow on every Superagent turn.

Per AGENTS.md § "Custom overlay", the agent reads every file under
`workspace/_custom/rules/` at the start of every Superagent turn and treats
their contents as additional rules on top of `AGENTS.md`. Files are loaded
in alphabetical order — the leading `01-`, `02-`, ... numbers control
that order.

## Starters in this pack

| File | What it captures |
|---|---|
| `01-tone.md` | How you want the agent to address you and how terse / formal to be. |
| `02-boundaries.md` | Outbound and irreversible-action constraints. |
| `03-privacy.md` | Names, addresses, and identifiers to never write into shareable artifacts. |
| `04-quiet-hours.md` | When non-urgent surfacing is suppressed. |
| `05-capture-nudges.md` | Phrases that should trigger ambient capture (tasks, contacts, expenses). |

## How to use

1. Copy the files you want into `workspace/_custom/rules/`.
2. Edit each file in place — fill in your preferences, delete what you don't want.
3. Files you delete entirely simply stop applying — there's no registration step.

## Naming conventions

* Numbered prefixes (`01-`, `02-`, ...) control load order. Lower numbers load first.
* Use kebab-case filenames.
* Each file should focus on ONE category — it's easier to reason about which rule
  fired when categories are kept separate.
