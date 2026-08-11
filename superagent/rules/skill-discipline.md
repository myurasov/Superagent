# Rule: Skills must be read and followed before acting

When a Superagent skill governs the current task, the agent must read the skill file before executing any action. This rule has no exceptions.

## When a skill applies

A skill applies when any of the following is true:

1. The skill autoloader (UserPromptSubmit hook) has injected the skill into the current turn — visible in the hook output at the start of the turn.
2. The user's request matches a known skill trigger (see `superagent/skills/_manifest.yaml`).
3. The task involves a workspace entity type that has a dedicated capture skill: source, contact, bill, appointment, domain, project, asset, account, document, event log entry.

## What "follow the skill" means

- Read the relevant steps of the skill file (use the step index for long skills).
- Execute the steps in order — do not skip, reorder, or substitute raw operations.
- Cite the skill at the top of the response: *"Following `superagent/skills/<name>.md`."*
- If the skill requires a sub-step the agent cannot perform (e.g. user login), pause and ask — do not skip the step.

## What is prohibited

- **Skill bypass:** executing raw file ops (`cp`, `mv`, `grep`, direct yaml edits) for a task that a skill covers, without first reading that skill.
- **Partial follow:** reading only part of a skill and ignoring steps that seem minor (e.g. reading the file-move step but skipping `_processed.yaml`).
- **Assumption-driven execution:** assuming you know the skill's steps from memory or prior context. Skills evolve; always read the current file.

## Why this matters

Skills encode the complete protocol for a task — file operations, index updates, log entries, cross-links, and provenance. Bypassing a skill produces incomplete workspace state (missing index rows, stale logs, orphaned files) that silently degrades the workspace over time and is expensive to repair.

## On violations

When a skill bypass is discovered mid-task, stop, read the skill, and complete the remaining steps correctly before continuing. Log the correction in `workspace/_memory/model-context.yaml` under `corrections`.
