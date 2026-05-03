# Workflows Contract

<!-- Migrated from `procedures.md § 25`. Citation form: `contracts/workflows.md`. -->

Implements ideas-better-structure.md item #2. Backed by `superagent/templates/workflows/<id>.yaml` (framework) and `workspace/_custom/templates/workflows/` (user overlay).

**A workflow is a versioned, parameterized recipe** for instantiating a Project. The `add-project --workflow <id>` flow:

1. Reads the workflow file.
2. Prompts for parameters (`year`, `destination`, etc. per workflow).
3. Substitutes `{param}` everywhere in the workflow body.
4. Creates the Project (charter, success criteria, deliverables, related domains).
5. Creates the seed tasks with `due_date = target_date + due_offset_days`.
6. Creates the listed `source_subfolders` (lazy — only the path is recorded, no files).
7. Sets the project's `workflow: <id>` field so lessons-learned feed back at completion.

**Lessons-learned loop**: when a workflow-instantiated project completes, the user is prompted with "Anything to capture for next time?" — the answer appends to `_memory/procedures.yaml` cross-referenced from the workflow id. The NEXT instantiation surfaces these notes upfront.

**Five starter workflows**: `tax-filing`, `trip-planning`, `annual-health-tuneup`, `job-search`, `appliance-replacement`. Schema reference: `templates/workflows/_schema.yaml`.
