# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Refresh auto-managed blocks inside Domains/<Name>/{info,history}.md.

Per `contracts/domain-reflection.md`: domain files mix curated narrative
with regenerated blocks marked by

    <!-- auto:<slug>:start -->
    ...
    <!-- auto:<slug>:end -->

This tool reads the source-of-truth indexes under `_memory/*.yaml`,
re-renders each registered block, and splices the result between its
markers. Anything outside the markers is preserved verbatim.

The tool is opt-in: domain files without any markers are not modified.
It is also idempotent — re-running with the same input produces the
same file bytes.

CLI:
    uv run python -m superagent.tools.render_domain --domain finances
    uv run python -m superagent.tools.render_domain --all
    uv run python -m superagent.tools.render_domain --check        # exit 1 if any block is stale
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

DOMAINS_DIR = "Domains"
MARKER_RE = re.compile(
    r"<!--\s*auto:([a-z0-9-]+):start\s*-->(.*?)<!--\s*auto:\1:end\s*-->",
    re.DOTALL,
)

Renderer = Callable[[Path], str]


def _load(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return None


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_accounts_summary(workspace: Path) -> str:
    """Table of accounts grouped by institution. Reads accounts-index.yaml."""
    data = _load(workspace / "_memory" / "accounts-index.yaml") or {}
    accounts = [a for a in (data.get("accounts") or []) if isinstance(a, dict)]
    if not accounts:
        return "_No accounts on file._"

    by_inst: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in accounts:
        if a.get("status") and a["status"] not in (None, "active"):
            continue
        by_inst[a.get("institution") or "(unknown)"].append(a)

    lines: list[str] = []
    lines.append("| Institution | Account | Kind | Last 4 | Ingest source |")
    lines.append("|---|---|---|---|---|")
    for inst in sorted(by_inst):
        rows = sorted(by_inst[inst], key=lambda a: a.get("name") or a.get("id") or "")
        for r in rows:
            kind = r.get("kind") or "—"
            last4 = r.get("number_last4") or "—"
            src = r.get("ingest_source") or "manual"
            name = (r.get("name") or r.get("id") or "—").replace("|", "\\|")
            lines.append(f"| {inst} | {name} | {kind} | {last4} | {src} |")
    return "\n".join(lines)


def render_financial_balances(workspace: Path) -> str:
    """Headline financial position, sourced from accounts-index transactions
    (when present) and visible balance snapshots."""
    data = _load(workspace / "_memory" / "accounts-index.yaml") or {}
    accounts = [a for a in (data.get("accounts") or []) if isinstance(a, dict)]
    if not accounts:
        return "_No accounts on file._"

    # No live balance is stored on accounts-index rows (those are user-facing
    # entity rows). Surface a structural summary instead: counts by kind +
    # institutions covered, with a pointer at the simplefin source.
    counts: dict[str, int] = defaultdict(int)
    insts: set[str] = set()
    sf_linked = 0
    for a in accounts:
        if a.get("status") and a["status"] not in (None, "active"):
            continue
        counts[a.get("kind") or "other"] += 1
        if a.get("institution"):
            insts.add(a["institution"])
        if a.get("simplefin_account_id"):
            sf_linked += 1

    lines: list[str] = []
    # Render institutions as a nested list — names sometimes contain both
    # commas and semicolons (e.g. "Rocket Mortgage, LLC (originator);
    # Nationstar Mortgage LLC ..."), so any inline separator gets ambiguous.
    lines.append(f"- **Institutions covered** ({len(insts)}):")
    for inst in sorted(insts):
        lines.append(f"  - {inst}")
    lines.append("- **Accounts by kind**: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ) + ".")
    lines.append(f"- **SimpleFin-linked accounts**: {sf_linked} of {len(accounts)}. "
                 "Live balances are pulled by the simplefin ingestor and cached in "
                 "`_memory/transactions.yaml`; per-account balance is NOT stored in "
                 "`accounts-index.yaml`.")
    return "\n".join(lines)


def render_recurring_commitments(workspace: Path) -> str:
    """Combined view of active bills + active subscriptions."""
    bills_data = _load(workspace / "_memory" / "bills.yaml") or {}
    subs_data = _load(workspace / "_memory" / "subscriptions.yaml") or {}
    bills = [b for b in (bills_data.get("bills") or [])
             if isinstance(b, dict) and b.get("id")
             and (b.get("status") in (None, "active"))]
    subs = [s for s in (subs_data.get("subscriptions") or [])
            if isinstance(s, dict) and s.get("id")
            and (s.get("status") in (None, "active"))]

    if not bills and not subs:
        return "_No active bills or subscriptions tracked._"

    lines: list[str] = []
    if bills:
        lines.append("**Bills (active)**:")
        lines.append("")
        lines.append("| Bill | Amount | Cadence | Pay from |")
        lines.append("|---|---|---|---|")
        for b in sorted(bills, key=lambda x: (x.get("name") or x.get("id") or "")):
            name = (b.get("name") or b.get("id") or "").replace("|", "\\|")[:60]
            amount = b.get("amount")
            amount_s = f"${amount:.2f}" if isinstance(amount, (int, float)) else "—"
            cadence = b.get("cadence") or "—"
            pay_from = b.get("pay_from_account") or b.get("account") or "—"
            lines.append(f"| {name} | {amount_s} | {cadence} | `{pay_from}` |")
        lines.append("")

    if subs:
        total_monthly = 0.0
        for s in subs:
            amt = s.get("amount") or 0
            if not isinstance(amt, (int, float)):
                continue
            if s.get("cadence") == "annual":
                total_monthly += amt / 12
            elif s.get("cadence") == "monthly":
                total_monthly += amt
        lines.append("**Subscriptions (active)** — "
                     f"{len(subs)} subs, **${total_monthly:.2f}/mo** monthly-equivalent "
                     f"(**${total_monthly*12:,.0f}/yr**):")
        lines.append("")
        lines.append("| Subscription | Provider | Amount | Cadence |")
        lines.append("|---|---|---|---|")
        for s in sorted(subs, key=lambda x: -(x.get("amount") or 0)
                        if x.get("cadence") == "monthly"
                        else -((x.get("amount") or 0) / 12)):
            name = (s.get("name") or s.get("id") or "").replace("|", "\\|")[:50]
            provider = (s.get("provider") or "—").replace("|", "\\|")[:30]
            amount = s.get("amount")
            amount_s = f"${amount:.2f}" if isinstance(amount, (int, float)) else "—"
            cadence = s.get("cadence") or "—"
            lines.append(f"| {name} | {provider} | {amount_s} | {cadence} |")

    return "\n".join(lines).rstrip()


def render_ingest_events(workspace: Path) -> str:
    """Recent ingest-log entries — last 10. Used in history.md."""
    log = _load(workspace / "_memory" / "ingestion-log.yaml") or {}
    runs = [r for r in (log.get("runs") or []) if isinstance(r, dict) and r.get("id")]
    if not runs:
        return "_No ingestion runs recorded._"
    runs = sorted(runs, key=lambda r: r.get("started_at") or "")[-10:]
    runs.reverse()
    lines: list[str] = []
    for r in runs:
        when = (r.get("finished_at") or r.get("started_at") or "")[:19]
        src = r.get("source") or "?"
        ins = r.get("items_inserted") or 0
        skp = r.get("items_skipped") or 0
        errs = len(r.get("errors") or [])
        run_id = r.get("id") or "?"
        lines.append(
            f"- **{when}** — `{src}` ingest ({run_id}): "
            f"{ins} inserted, {skp} skipped, {errs} error(s)."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Per-(domain, slug) renderer. Domain slug matches Domains/<Name>/ on disk
# (case-insensitive match; the renderer normalizes to lowercase here).
RENDERERS: dict[tuple[str, str], Renderer] = {
    ("finances", "accounts-summary"):       render_accounts_summary,
    ("finances", "financial-balances"):     render_financial_balances,
    ("finances", "recurring-commitments"):  render_recurring_commitments,
    ("finances", "ingest-events"):          render_ingest_events,
}

# Files each domain's renderers can write into.
DOMAIN_FILES = ("info.md", "history.md")


def _splice(text: str, slug: str, body: str) -> tuple[str, bool, bool]:
    """Splice `body` between `<!-- auto:<slug>:start -->` and `:end -->` markers.

    Returns (new_text, found, changed).
    """
    start_re = re.compile(r"<!--\s*auto:" + re.escape(slug) + r":start\s*-->")
    end_re = re.compile(r"<!--\s*auto:" + re.escape(slug) + r":end\s*-->")
    start_match = start_re.search(text)
    end_match = end_re.search(text)
    if not start_match or not end_match:
        return text, False, False
    if end_match.start() <= start_match.end():
        return text, False, False  # malformed (end before start)
    before = text[: start_match.end()]
    after = text[end_match.start():]
    new_inner = "\n" + body.rstrip() + "\n"
    new_text = before + new_inner + after
    return new_text, True, (new_text != text)


def _find_domain_dir(workspace: Path, domain: str) -> Path | None:
    """Resolve `Domains/<Domain>/` case-insensitively."""
    domains_root = workspace / DOMAINS_DIR
    if not domains_root.exists():
        return None
    target = domain.lower()
    for entry in domains_root.iterdir():
        if entry.is_dir() and entry.name.lower() == target:
            return entry
    return None


def list_managed_domains(workspace: Path) -> list[str]:
    """All domain slugs that have at least one registered renderer."""
    return sorted({d for (d, _) in RENDERERS})


def refresh_domain(workspace: Path, domain: str, *, check_only: bool = False) -> dict[str, Any]:
    """Refresh all marker blocks inside one domain's files.

    Returns a summary dict: {files: [{path, slugs_found, slugs_changed}], errors: [...]}.
    """
    summary: dict[str, Any] = {"domain": domain, "files": [], "errors": []}
    dir_ = _find_domain_dir(workspace, domain)
    if dir_ is None:
        summary["errors"].append(f"domain folder not found for {domain!r}")
        return summary
    for fname in DOMAIN_FILES:
        path = dir_ / fname
        if not path.exists():
            continue
        text = path.read_text()
        original = text
        per_file_slugs_found: list[str] = []
        per_file_slugs_changed: list[str] = []
        # Scan all markers actually present in this file.
        for slug_match in MARKER_RE.finditer(text):
            slug = slug_match.group(1)
            renderer = RENDERERS.get((domain.lower(), slug))
            if renderer is None:
                summary["errors"].append(
                    f"{path.relative_to(workspace)}: stale marker {slug!r} "
                    "(no renderer registered)"
                )
                continue
            try:
                body = renderer(workspace)
            except Exception as exc:  # noqa: BLE001
                summary["errors"].append(
                    f"{path.relative_to(workspace)}#{slug}: renderer failed: {exc}"
                )
                continue
            text, found, changed = _splice(text, slug, body)
            if found:
                per_file_slugs_found.append(slug)
                if changed:
                    per_file_slugs_changed.append(slug)
        if text != original and not check_only:
            path.write_text(text)
        summary["files"].append({
            "path": str(path.relative_to(workspace)),
            "slugs_found": per_file_slugs_found,
            "slugs_changed": per_file_slugs_changed,
        })
    return summary


def _refresh_status_md(workspace: Path, domain: str) -> list[str]:
    """Refresh `Domains/<Name>/status.md`'s Open/Done tables via render_status.

    Splices into an existing curated status.md when present, preserving the
    RAG / Recent Progress / Blockers / Next Steps narrative; otherwise
    renders a fresh template. Returns a list of error strings.
    """
    try:
        from superagent.tools import render_status as rs
    except ImportError as exc:
        return [f"render_status import failed: {exc}"]

    todo_path = workspace / "_memory" / "todo.yaml"
    domains_index_path = workspace / "_memory" / "domains-index.yaml"
    if not todo_path.exists():
        return []
    todo_data = rs.load_yaml(todo_path) or {}
    tasks = todo_data.get("tasks") or []
    domains_data = rs.load_yaml(domains_index_path) or {}
    domains_list = domains_data.get("domains") or []
    domain_row = next(
        (d for d in domains_list
         if isinstance(d, dict) and (d.get("id") or "").lower() == domain.lower()),
        None,
    )
    if not domain_row:
        return [f"render_status: domain {domain!r} not in domains-index.yaml"]
    scope_id = (domain_row.get("id") or domain).lower()
    scope_name = domain_row.get("name") or domain
    out_path = workspace / "Domains" / scope_name / "status.md"
    body: str | None = None
    if out_path.is_file():
        body = rs.splice_open_done(out_path.read_text(), scope_id, tasks)
    if body is None:
        body = rs.render_status_md(scope_name, scope_id, tasks)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body)
    return []


def _refresh_workbook(workspace: Path, domain: str) -> list[str]:
    """Refresh `Domains/<Name>/<domain>.xlsx` via render_workbooks.

    `render_workbooks` is mtime-lazy: a re-render is a no-op when no source
    yaml has changed since the last build. Returns a list of error strings.
    """
    try:
        from superagent.tools import render_workbooks as rw
    except ImportError as exc:
        return [f"render_workbooks import failed: {exc}"]
    framework = Path(rw.__file__).resolve().parents[1]
    try:
        result = rw.render_domain(workspace, framework, domain.lower())
    except Exception as exc:  # noqa: BLE001
        return [f"render_workbooks({domain}): {exc}"]
    if result.status == "error":
        return [f"render_workbooks({domain}): {result.error}"]
    return []


def refresh(workspace: Path, domains: list[str] | None = None,
            *, check_only: bool = False) -> dict[str, Any]:
    """Public entry point used by ingestors and skills.

    Per `contracts/domain-reflection.md`, this refreshes ALL derived
    views for the affected domain(s):

      1. Marker blocks in `info.md` / `history.md` (this module).
      2. The `## Open` / `## Done` task tables in `status.md`
         (delegated to `render_status`).
      3. The per-domain `.xlsx` workbook (delegated to `render_workbooks`,
         mtime-lazy — no-op when source yaml has not changed).

    All three stages are best-effort: errors are aggregated into the
    returned summary but never raised. The data is already safely in
    `_memory/*.yaml`; rendering is a derived view.

    Pass `domains=None` (or `--all`) to refresh every managed domain.
    """
    if domains is None:
        domains = list_managed_domains(workspace)
    if not domains:
        return {"domains": [], "errors": []}
    out: dict[str, Any] = {"domains": [], "errors": [], "checked_at": _now()}
    for d in domains:
        summary = refresh_domain(workspace, d, check_only=check_only)
        if not check_only:
            summary["errors"].extend(_refresh_status_md(workspace, d))
            summary["errors"].extend(_refresh_workbook(workspace, d))
        out["domains"].append(summary)
        out["errors"].extend(summary["errors"])
    return out


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(prog="render-domain")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--domain", type=str, default=None,
                        help="Domain slug (default: all managed domains).")
    parser.add_argument("--all", action="store_true",
                        help="Refresh every managed domain.")
    parser.add_argument("--check", action="store_true",
                        help="Don't write; exit 1 if any block would change.")
    args = parser.parse_args()

    framework = Path(__file__).resolve().parents[1]
    workspace = args.workspace or framework.parent / "workspace"

    if args.all or args.domain is None:
        domains = None
    else:
        domains = [args.domain]

    summary = refresh(workspace, domains, check_only=args.check)
    any_changed = False
    for d in summary["domains"]:
        for f in d["files"]:
            if f["slugs_found"]:
                marker = "would change" if args.check else "refreshed"
                changed = f["slugs_changed"]
                print(f"  {f['path']}: {marker} {len(changed)}/{len(f['slugs_found'])} "
                      f"block(s) [{', '.join(changed) or '(none)'}]")
                if changed:
                    any_changed = True
    for err in summary["errors"]:
        print(f"  error: {err}", file=sys.stderr)
    if args.check and any_changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
