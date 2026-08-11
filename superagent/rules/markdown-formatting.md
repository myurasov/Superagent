# Rule: Markdown formatting conventions

## Horizontal rules

Never use `---` (horizontal rules / thematic breaks) in any markdown file. Use blank lines and `##` headings for section separation.

## Checkboxes and numbered lists

Use `- [ ]` unordered GFM checkboxes. Do NOT use `1. [ ]` — numbered task lists lose their numbers in VS Code's renderer. Show ordering with bold prefixes instead:

```markdown
- [ ] **1.** `TASK-043`\
  **Task description** `project-tag`
```

The backslash at the end of a line forces a hard `<br>` (CommonMark hard line break), keeping the task ID and description on separate visual lines within the same list item.

## Autonomy markers (workspace/todo.md)

Mark each task with one of:

- `🚗` — agent does it fully autonomously, no permission needed
- `👤` — human only; cannot be automated even with permission (phone calls, physical actions, inherently human decisions)
- `🚗 👤` — agent preps the work, user executes the final step
- `👤 🚗` — agent can do it fully, but only with explicit user permission per task (large financial transactions, filing/submitting documents on user's behalf, any action where the user needs to review before it goes out)

Non-breaking space (`&nbsp;`) between the two emoji in `🚗 👤` and `👤 🚗`.

Place the marker immediately after the task ID code span, before the backslash line break.
