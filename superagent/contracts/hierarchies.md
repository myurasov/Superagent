# Hierarchies Contract (sub-domains / sub-projects)

<!-- Migrated from `procedures.md § 22`. Citation form: `contracts/hierarchies.md`. -->

Implements ideas-better-structure.md item #10. Strictly opt-in; flat is the default.

**Schema addition** (`domains-index.yaml`, `projects-index.yaml`):

```yaml
- id: "health-mental"
  name: "Mental Health"
  parent: "health"
  path: "workspace/Domains/Health/Mental-Health"
```

**Folder convention**: when `parent` is set, the folder lives under the parent's path (`Domains/<Parent>/<Child>/` for sub-domains; `Projects/<parent>/<sub-slug>/` for sub-projects).

**Skill traversal**: skills SHOULD walk parents transparently — listing tasks for "Health" includes tasks for "Mental Health" by default. `--strict` flag opts out.

**Use sparingly**: the cognitive overhead is real. Recommend converting to sub-X only when a single folder overflows 50+ files into natural sub-groups.
