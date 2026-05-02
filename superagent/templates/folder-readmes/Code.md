# `Code/`

Self-contained coding projects built by **Supercoder Mode 2**. Each subfolder is one project, with its own source code, its own (optional) git repo, and its own tests.

This folder is a peer of `Domains/`, `Projects/`, `Sources/`, `Inbox/`, and `Outbox/`. It is part of `workspace/`, which is gitignored.

---

## Layout

```
Code/<slug>/
├── .supercoder/                ← agent metadata (Supercoder-managed)
│   ├── info.md                 ←   charter
│   ├── status.md               ←   RAG + open / done tasks
│   ├── history.md              ←   chronological log
│   └── decisions.yaml          ←   append-only decisions
├── .gitignore
├── README.md
└── (project source — language-specific)
```

The single registry is `workspace/_memory/code-projects-index.yaml`.

## Self-containment (the hard rule)

Per `superagent/procedures.md` § 40.2, the Supercoder writes ONLY inside the active project's folder, with four small enumerated exceptions for workspace-level indexes (`code-projects-index.yaml`, `context.yaml.active_code_project`, `interaction-log.yaml`, optionally `decisions.yaml`).

It cannot touch:

- The framework code under `superagent/`.
- Personal-life folders: `Domains/`, `Projects/`, `Sources/`.
- Any other code project (`Code/<other-slug>/`).
- The Outbox.

This is enforced by the Supercoder's path-scope safeguard, not optional. To switch projects, the user must explicitly `supercoder open <slug>`.

## Common commands

```
supercoder new <slug> "<purpose>"     # bootstrap a new project
supercoder list                       # list every code project + status
supercoder open <slug>                # set the active project
supercoder status [<slug>]            # charter + RAG + tasks
supercoder work "<instruction>"       # iterate on the active project
supercoder close <slug>                # mark complete
supercoder archive <slug>             # move to workspace/Archive/code/<slug>/
```

Each project is independently git-able: run `git init` inside the project folder when you're ready to track it. The same `commit-msg` AI-attribution guard from the parent repo can be installed per-project (see `superagent/supercoder.agent.md` § "Git practices").
