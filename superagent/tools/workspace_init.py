#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Idempotent workspace scaffolder for Superagent.

Creates `workspace/` (or the path configured via
`--workspace`) with:
  - `_memory/`  — copied from `superagent/templates/memory/`
  - `Domains/`  — top-level directory + README only; per-domain folders are
                  LAZY (materialized on first data write per
                  `contracts/domains-and-assets.md` § 6.4a). The 13 default
                  domains are still REGISTERED in `_memory/domains-index.yaml`
                  so the `add-*` skills can route to them.
  - `Inbox/`, `Outbox/`, `Archive/` — staging / output / archive folders
  - `Projects/`, `Sources/`         — personal-life folders
  - `_custom/`                      — empty per-user overlay scaffold
  - `todo.md`                       — workspace-level cross-cutting task view

Re-running this script never overwrites existing files. Safe to invoke
repeatedly — useful after pulling framework updates that ship new
template files. Existing files are kept as-is.

Usage:
  uv run python superagent/tools/workspace_init.py [--workspace PATH]
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
    ("Finances", "Operational financial life — bills, banking accounts (the operational tubes), credit cards, loans, mortgages, insurance policies, payroll, taxes, budget, cash flow. Holdings themselves (stocks, significant cash positions, bonds, crypto) live in Assets"),
    ("Home", "Mortgage / rent, utilities, HOA, maintenance schedule, contractors, security, deliveries"),
    ("Vehicles", "Every vehicle owned (cars, bikes, motorcycles, RVs, boats); registration, insurance, maintenance, fuel"),
    ("Assets", "Things of value worth tracking — physical (electronics, appliances, jewelry, instruments, tools, art, collectibles) AND financial (stock holdings, ETFs, bonds, crypto, significant cash positions, precious metals) AND real estate. Excludes vehicles (Vehicles) and the home structure / fixtures (Home). The asset is the holding; the operational account it lives in stays in Finances"),
    ("Pets", "Each pet's vet, vaccinations, prescriptions, food, grooming, boarding"),
    ("Family", "Spouse, kids, parents, siblings; school calendars, kids' doctors, extracurriculars, family events"),
    ("Travel", "Trips planned and past, flights, hotels, rentals, packing lists, frequent-flier numbers, passports"),
    ("Career", "Resume, certifications, performance reviews, learning goals, networking, salary history"),
    ("Business", "Side income, freelancing, consulting, sole-proprietor / LLC operations — clients, contracts, invoices, business expenses, business taxes, vendor relationships (separate from W-2 Career employment)"),
    ("Education", "Active enrollment in a degree / certificate program (yourself — kids' schooling lives in Family). Courses in flight, credits earned, credentials being pursued, study schedule, advisors, registrar, transcripts, FAFSA / financial aid, employer tuition assistance. Distinct from Career which owns the W-2 employment side"),
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
    """Create the `Domains/` directory only.

    Per-domain folders are LAZY — materialized on first data write per
    `contracts/domains-and-assets.md` § 6.4a. The 13 default domains are
    still REGISTERED in `_memory/domains-index.yaml` (handled by
    `init_memory`), so capture skills know where to route data when the
    user starts adding rows.

    Returns 1 if `Domains/` was newly created, else 0.
    """
    del framework  # no longer needed at init time; reserved for future use
    domains_dir = workspace / "Domains"
    return 1 if safe_mkdir(domains_dir, dry_run=dry_run, log=log) else 0


def init_folders(workspace: Path, framework: Path, dry_run: bool, log: list[str]) -> int:
    """Create the standard top-level workspace folders with their READMEs.

    `Outbox/` ships FLAT — only `Outbox/` + `Outbox/README.md`. The four
    documented lifecycle sub-folders (drafts / staging / sent / sealed) and
    any artifact-kind sub-folders (emails / handoff / contractors / ...)
    are LAZY per `contracts/outbox-lifecycle.md` § "Lazy sub-directory
    creation" — they materialize on first write via
    `superagent.tools.outbox.ensure(workspace, <subdir>)`.

    Returns count of top-level folders newly created.
    """
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
    return touched


def init_internal_dirs(workspace: Path, dry_run: bool, log: list[str]) -> int:
    """Create the `_memory/` sub-directories whose writers ship today.

    Eager (created here): `sensitive/` (sensitive-tier root, written by
    capture skills + ingestors) and `events/` (events stream, written by
    `tools/log_window.py`).

    Lazy (NOT created here): `_briefings/`, `_artifacts/`, `_session/`,
    `_telemetry/`, `_checkpoints/`. Each is created by its writer on
    first use (the writers all `mkdir(parents=True, exist_ok=True)`),
    so an empty directory after init no longer means a feature is
    silently broken — it means the writer hasn't run yet (or for
    `_telemetry/` and `_checkpoints/`, the writer hasn't shipped at
    all). Re-add an entry below ONLY when its writer is wired into a
    skill that runs in normal use.
    """
    internal = [
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


def init_version_file(workspace: Path, framework: Path, dry_run: bool, log: list[str]) -> bool:
    """Stamp `workspace/.version` with the framework's current version.

    Per `contracts/versioning.md` § 2 + § 6: a fresh workspace records
    the framework version it was scaffolded from, so future framework
    upgrades can compute the correct migration chain. Idempotent — keeps
    an existing `.version` file untouched (the `migrate` skill is the
    sole authority for advancing it after init).
    """
    from superagent.tools.version import current_version
    version = current_version(framework.parent / "pyproject.toml")
    return safe_write(workspace / ".version", f"{version}\n", dry_run=dry_run, log=log)


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
        version_written = init_version_file(workspace, framework, args.dry_run, log)
    except Exception as exc:
        print(f"ERROR during init: {exc}", file=sys.stderr)
        return 1

    for line in log:
        print(line)

    print()
    print("Summary:")
    print(f"  _memory files created:    {memory_copied}")
    print(f"  Domains/ created:         {domains_touched}  (per-domain folders are lazy)")
    print(f"  Top-level folders:        {folders_touched}")
    print(f"  internal _memory dirs:    {internal_touched}")
    print(f"  _custom subfolders:       {custom_touched}")
    print(f"  workspace todo.md:        {'created' if todo_written else 'kept'}")
    print(f"  workspace .version:       {'created' if version_written else 'kept'}")
    if args.dry_run:
        print("\nDry run complete. Re-run without --dry-run to apply.")
    else:
        print(f"\nWorkspace ready at {workspace}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
