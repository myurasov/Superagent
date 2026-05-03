#!/usr/bin/env python3
"""Idempotent workspace scaffolder for Superagent.

Creates `workspace/` (or the path configured via
`--workspace`) with:
  - `_memory/`  — copied from `superagent/templates/memory/`
  - `Domains/`  — the 10 default domains, each with the 4-file structure
  - `Inbox/`, `Outbox/`, `Archive/` — staging / output / archive folders
  - `Projects/`, `Sources/`         — personal-life folders
  - `_custom/`                      — empty per-user overlay scaffold
  - `todo.md`                       — workspace-level cross-cutting task view

Re-running this script never overwrites existing files. Safe to invoke
repeatedly — useful after pulling framework updates that ship new
template files. Existing files are kept as-is.

Usage:
  python3 superagent/tools/workspace_init.py [--workspace PATH]
                                                [--framework PATH]
                                                [--dry-run]

Exit codes:
  0  success
  1  runtime error
  2  CLI usage error
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

DEFAULT_DOMAINS = [
    ("Health", "Medical, dental, vision, mental health, prescriptions, vaccines, vitals, family medical history"),
    ("Finance", "Bills, accounts (banks, brokerage, retirement), taxes, budget, insurance, credit"),
    ("Home", "Mortgage / rent, utilities, HOA, maintenance schedule, contractors, security, deliveries"),
    ("Vehicles", "Every vehicle owned (cars, bikes, motorcycles, RVs, boats); registration, insurance, maintenance, fuel"),
    ("Pets", "Each pet's vet, vaccinations, prescriptions, food, grooming, boarding"),
    ("Family", "Spouse, kids, parents, siblings; school calendars, kids' doctors, extracurriculars, family events"),
    ("Travel", "Trips planned and past, flights, hotels, rentals, packing lists, frequent-flier numbers, passports"),
    ("Career", "Resume, certifications, performance reviews, learning goals, networking, salary history"),
    ("Hobbies", "Each meaningful hobby — fitness, reading log, side project, garden, workshop, etc."),
    ("Self", "Personal-development goals, journaling, books / podcasts / media log, life themes"),
]


def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def render_domain_file(template: str, domain_name: str) -> str:
    """Substitute the standard placeholders for a domain template."""
    domain_id = domain_name.lower().replace(" ", "-")
    return (
        template
        .replace("{{DOMAIN_NAME}}", domain_name)
        .replace("{{DOMAIN_ID}}", domain_id)
        .replace("{{LAST_UPDATED}}", now_iso())
        .replace("{{OVERVIEW}}", "")
        .replace("{{CURRENT_STATE}}", "")
        .replace("{{KEY_FACTS}}", "")
        .replace("{{ROUTINES}}", "")
        .replace("{{STAKEHOLDERS}}", "")
        .replace("{{OPEN_QUESTIONS}}", "")
        .replace("{{CURRENT_STATUS_RAG}}", "**Green** — fresh workspace, no open issues.")
        .replace("{{RECENT_PROGRESS}}", "<!-- nothing yet -->")
        .replace("{{ACTIVE_BLOCKERS}}", "<!-- nothing yet -->")
        .replace("{{NEXT_STEPS}}", "<!-- nothing yet -->")
        .replace("{{OPEN_ITEMS}}", "<!-- no open tasks yet -->")
        .replace("{{DONE_ITEMS}}", "<!-- nothing completed yet -->")
        .replace("{{HISTORY_ENTRIES}}", "<!-- newest at top; H4 entries: #### YYYY-MM-DD — title -->")
        # Sources.md placeholders
        .replace("{{DOC_1_TITLE}}", "—")
        .replace("{{DOC_1_PATH}}", "—")
        .replace("{{DOC_1_CATEGORY}}", "—")
        .replace("{{DOC_1_ADDED}}", "—")
        .replace("{{DOC_1_NOTES}}", "no entries yet")
        .replace("{{DOCUMENTS_TABLE_ROWS}}", "")
        .replace("{{REF_1_TITLE}}", "—")
        .replace("{{REF_1_PATH}}", "—")
        .replace("{{REF_1_KIND}}", "—")
        .replace("{{REF_1_SOURCE}}", "—")
        .replace("{{REF_1_NOTES}}", "no entries yet")
        .replace("{{REFERENCES_TABLE_ROWS}}", "")
        .replace("{{ART_1_TITLE}}", "—")
        .replace("{{ART_1_PATH}}", "—")
        .replace("{{ART_1_KIND}}", "—")
        .replace("{{ART_1_ADDED}}", "—")
        .replace("{{ART_1_NOTES}}", "no entries yet")
        .replace("{{ARTIFACTS_TABLE_ROWS}}", "")
        # Rolodex placeholders (empty by default).
        .replace("{{P_1_NAME}}", "")
        .replace("{{P_1_ROLE}}", "")
        .replace("{{P_1_PHONE}}", "")
        .replace("{{P_1_EMAIL}}", "")
        .replace("{{P_1_NOTES}}", "")
        .replace("{{P_1_LAST_CONTACTED}}", "")
        .replace("{{F_1_NAME}}", "")
        .replace("{{F_1_RELATIONSHIP}}", "")
        .replace("{{F_1_PHONE}}", "")
        .replace("{{F_1_EMAIL}}", "")
        .replace("{{F_1_NOTES}}", "")
        .replace("{{F_1_LAST_CONTACTED}}", "")
        .replace("{{V_1_NAME}}", "")
        .replace("{{V_1_SERVICE}}", "")
        .replace("{{V_1_PHONE}}", "")
        .replace("{{V_1_URL}}", "")
        .replace("{{V_1_NOTES}}", "")
        .replace("{{V_1_LAST_CONTACTED}}", "")
        .replace("{{PROVIDERS_TABLE_ROWS}}", "")
        .replace("{{FAMILY_TABLE_ROWS}}", "")
        .replace("{{VENDORS_TABLE_ROWS}}", "")
        .replace("{{OTHER_CONTACTS}}", "")
    )


def render_workspace_todo(template: str) -> str:
    """Substitute placeholders in templates/todo.md for the workspace root view."""
    empty_row = "| — | — | — | — |"
    return (
        template
        .replace("{{SCOPE}}", "workspace")
        .replace("{{LAST_UPDATED}}", now_iso())
        .replace("{{P0_ROWS}}", empty_row)
        .replace("{{P1_ROWS}}", empty_row)
        .replace("{{P2_ROWS}}", empty_row)
        .replace("{{P3_ROWS}}", empty_row)
        .replace("{{DONE_ROWS}}", "| — | — | — |")
    )


def safe_copy(src: Path, dst: Path, *, dry_run: bool, log: list[str]) -> bool:
    """Copy `src` to `dst` only if `dst` does not exist. Return True if copied."""
    if dst.exists():
        log.append(f"keep    {dst}")
        return False
    if dry_run:
        log.append(f"would create {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    log.append(f"create  {dst}")
    return True


def safe_write(dst: Path, content: str, *, dry_run: bool, log: list[str]) -> bool:
    """Write `content` to `dst` only if `dst` does not exist. Return True if written."""
    if dst.exists():
        log.append(f"keep    {dst}")
        return False
    if dry_run:
        log.append(f"would write  {dst}")
        return True
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(content)
    log.append(f"write   {dst}")
    return True


def safe_mkdir(dst: Path, *, dry_run: bool, log: list[str]) -> bool:
    """Create directory `dst` if missing. Return True if created."""
    if dst.exists():
        log.append(f"keep    {dst}/")
        return False
    if dry_run:
        log.append(f"would create {dst}/")
        return True
    dst.mkdir(parents=True, exist_ok=True)
    log.append(f"create  {dst}/")
    return True


def init_memory(workspace: Path, framework: Path, dry_run: bool, log: list[str]) -> int:
    """Copy every file from templates/memory/ into _memory/. Returns count copied."""
    src_dir = framework / "templates" / "memory"
    dst_dir = workspace / "_memory"
    safe_mkdir(dst_dir, dry_run=dry_run, log=log)
    copied = 0
    for src in sorted(src_dir.glob("*.yaml")):
        if safe_copy(src, dst_dir / src.name, dry_run=dry_run, log=log):
            copied += 1
    return copied


def init_domains(workspace: Path, framework: Path, dry_run: bool, log: list[str]) -> int:
    """Create the 10 default domain folders with the 5-file structure. Returns count of folders touched."""
    domains_dir = workspace / "Domains"
    safe_mkdir(domains_dir, dry_run=dry_run, log=log)
    template_dir = framework / "templates" / "domains"
    info_t = (template_dir / "info.md").read_text()
    status_t = (template_dir / "status.md").read_text()
    history_t = (template_dir / "history.md").read_text()
    rolodex_t = (template_dir / "rolodex.md").read_text()
    sources_t = (template_dir / "sources.md").read_text()
    touched = 0
    for name, _scope in DEFAULT_DOMAINS:
        domain_dir = domains_dir / name
        safe_mkdir(domain_dir, dry_run=dry_run, log=log)
        wrote = False
        wrote |= safe_write(domain_dir / "info.md",
                            render_domain_file(info_t, name),
                            dry_run=dry_run, log=log)
        wrote |= safe_write(domain_dir / "status.md",
                            render_domain_file(status_t, name),
                            dry_run=dry_run, log=log)
        wrote |= safe_write(domain_dir / "history.md",
                            render_domain_file(history_t, name),
                            dry_run=dry_run, log=log)
        wrote |= safe_write(domain_dir / "rolodex.md",
                            render_domain_file(rolodex_t, name),
                            dry_run=dry_run, log=log)
        wrote |= safe_write(domain_dir / "sources.md",
                            render_domain_file(sources_t, name),
                            dry_run=dry_run, log=log)
        if wrote:
            touched += 1
    return touched


def init_folders(workspace: Path, framework: Path, dry_run: bool, log: list[str]) -> int:
    """Create the standard top-level workspace folders with their READMEs. Returns count touched."""
    readmes = framework / "templates" / "folder-readmes"
    folders = [
        ("Inbox", readmes / "Inbox.md"),
        ("Outbox", readmes / "Outbox.md"),
        ("Archive", readmes / "Archive.md"),
        ("Projects", readmes / "Projects.md"),
        ("Sources", readmes / "Sources.md"),
    ]
    touched = 0
    for name, readme_src in folders:
        folder = workspace / name
        if safe_mkdir(folder, dry_run=dry_run, log=log):
            touched += 1
        if readme_src.exists():
            safe_copy(readme_src, folder / "README.md", dry_run=dry_run, log=log)
    domains_readme = readmes / "Domains.md"
    if domains_readme.exists():
        safe_copy(domains_readme, workspace / "Domains" / "README.md",
                  dry_run=dry_run, log=log)
    # Outbox lifecycle sub-folders (item #13).
    outbox_root = workspace / "Outbox"
    for sub in ("drafts", "staging", "sent", "sealed"):
        safe_mkdir(outbox_root / sub, dry_run=dry_run, log=log)
    return touched


def init_internal_dirs(workspace: Path, dry_run: bool, log: list[str]) -> int:
    """Create internal `_memory/_*` sub-directories the new tools rely on."""
    internal = [
        "_memory/_briefings",
        "_memory/_artifacts",
        "_memory/_session",
        "_memory/_telemetry",
        "_memory/_checkpoints",
        "_memory/sensitive",
        "_memory/events",
    ]
    touched = 0
    for sub in internal:
        if safe_mkdir(workspace / sub, dry_run=dry_run, log=log):
            touched += 1
    return touched


def init_custom(workspace: Path, dry_run: bool, log: list[str]) -> int:
    """Create the per-user overlay scaffold."""
    custom = workspace / "_custom"
    touched = 0
    for sub in ("rules", "skills", "agents", "templates", "templates/memory", "tools"):
        if safe_mkdir(custom / sub, dry_run=dry_run, log=log):
            touched += 1
    return touched


def init_workspace_todo(workspace: Path, framework: Path, dry_run: bool, log: list[str]) -> bool:
    """Write the workspace-level todo.md."""
    template = (framework / "templates" / "todo.md").read_text()
    rendered = render_workspace_todo(template)
    return safe_write(workspace / "todo.md", rendered, dry_run=dry_run, log=log)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(
        prog="workspace_init",
        description="Idempotent workspace scaffolder for Superagent.",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="Target workspace path (default: workspace next to the framework).",
    )
    parser.add_argument(
        "--framework",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Path to superagent/ framework (default: parent of this script).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen; do not write any files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns exit code."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    if not framework.is_dir():
        print(f"ERROR: framework path does not exist: {framework}", file=sys.stderr)
        return 1
    workspace: Path = args.workspace or framework.parent / "workspace"

    print(f"Framework:  {framework}")
    print(f"Workspace:  {workspace}")
    if args.dry_run:
        print("DRY RUN — no files will be written.")
    print()

    log: list[str] = []

    try:
        memory_copied = init_memory(workspace, framework, args.dry_run, log)
        domains_touched = init_domains(workspace, framework, args.dry_run, log)
        folders_touched = init_folders(workspace, framework, args.dry_run, log)
        internal_touched = init_internal_dirs(workspace, args.dry_run, log)
        custom_touched = init_custom(workspace, args.dry_run, log)
        todo_written = init_workspace_todo(workspace, framework, args.dry_run, log)
    except Exception as exc:
        print(f"ERROR during init: {exc}", file=sys.stderr)
        return 1

    for line in log:
        print(line)

    print()
    print("Summary:")
    print(f"  _memory files created:    {memory_copied}")
    print(f"  Domain folders touched:   {domains_touched}")
    print(f"  Top-level folders:        {folders_touched}")
    print(f"  internal _memory dirs:    {internal_touched}")
    print(f"  _custom subfolders:       {custom_touched}")
    print(f"  workspace todo.md:        {'created' if todo_written else 'kept'}")
    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to apply.")
    else:
        print(f"\nWorkspace ready at {workspace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
