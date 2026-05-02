# {{PROJECT_NAME}}

{{GOAL}}

---

## Run

```
{{TEST_COMMAND}}
```

## Status

See [`.supercoder/status.md`](.supercoder/status.md) for the current Open / Done view.

## Charter

See [`.supercoder/info.md`](.supercoder/info.md) for the full charter (goal, scope, success criteria).

## History

See [`.supercoder/history.md`](.supercoder/history.md) for the chronological log of changes.

## How this project is built

This project is built and maintained by **Superagent's Supercoder** (Mode 2: project build). The Supercoder writes only inside this folder; it cannot touch the parent Superagent framework or any other code project. To work on this project from a Superagent session, run:

```
supercoder open {{SLUG}}
supercoder work "<your instruction>"
```

The Supercoder runs `{{TEST_COMMAND}}` after every change and only commits on a green run.
