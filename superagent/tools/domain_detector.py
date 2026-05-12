#!/usr/bin/env -S uv run python
"""Detection-driven domain suggestions.

Implements `contracts/domains-and-assets.md` § 6.4b. Walks workspace signals
(off-domain tags, contact-role clusters, off-domain project clusters, source
folder clusters) and surfaces candidate new domains the user hasn't already
accepted / declined / deferred.

CLI:

    uv run python -m superagent.tools.domain_detector run         # detect + print
    uv run python -m superagent.tools.domain_detector run --write # ... and persist to suggested[]
    uv run python -m superagent.tools.domain_detector list        # show suggested/accepted/declined/deferred
    uv run python -m superagent.tools.domain_detector record <theme> <answer>
                                                                  # answer: yes | not_now | never
    uv run python -m superagent.tools.domain_detector forget <theme>
                                                                  # clear from declined/deferred so it can surface again

Python API:

    from superagent.tools.domain_detector import detect, record_answer, forget
    suggestions = detect(workspace)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Default thresholds (overridable via _memory/config.yaml.preferences.domain_detection.*).
DEFAULT_MIN_SCORE = 5
DEFAULT_TOP_N = 3
DEFAULT_DEFER_DAYS = 90
DEFAULT_TAG_THRESHOLD = 3
DEFAULT_ROLE_THRESHOLD = 3
DEFAULT_PROJECT_THRESHOLD = 2
DEFAULT_SOURCE_FOLDER_THRESHOLD = 5

# Built-in alias map: each registered domain id maps to a set of common
# synonyms / related keywords. Themes that match an alias are NOT suggested
# (already covered by an existing domain). Augmented at runtime with each
# registered domain's `tags[]`.
DOMAIN_ALIASES: dict[str, set[str]] = {
    "health": {
        "health", "medical", "medicine", "doctor", "physician", "dentist",
        "dental", "vision", "optometrist", "prescription", "rx", "vaccine",
        "vaccination", "vital", "lab", "lab-result", "specialist", "surgery",
        "mental-health", "therapy", "therapist", "wellness",
    },
    "finances": {
        "finances", "finance", "financial", "bill", "bills", "subscription",
        "account", "bank", "banking", "checking", "savings", "credit",
        "credit-card", "loan", "mortgage", "insurance", "policy", "tax",
        "taxes", "irs", "ftb", "budget", "payroll", "transaction", "expense",
        "expenses", "deductible", "withholding",
    },
    "home": {
        "home", "house", "household", "residence", "hoa", "utility",
        "utilities", "hvac", "plumber", "plumbing", "electrician",
        "electrical", "contractor", "handyman", "appliance", "appliances",
        "property-tax", "lawn", "landscaping", "alarm", "security",
    },
    "vehicles": {
        "vehicles", "vehicle", "car", "cars", "auto", "automobile",
        "motorcycle", "bike", "bicycle", "rv", "boat", "registration",
        "dmv", "tire", "tires", "brake", "fuel", "gasoline",
        "oil-change", "tesla",
    },
    "assets": {
        "assets", "asset", "electronics", "jewelry", "watch", "watches",
        "instrument", "art", "collectible", "tool", "tools", "stock",
        "stocks", "bond", "bonds", "etf", "mutual-fund", "crypto",
        "cryptocurrency", "cash-position", "precious-metal", "gold",
        "silver", "treasury", "treasuries", "real-estate", "rental",
        "investment",
    },
    "pets": {
        "pets", "pet", "vet", "veterinary", "kennel", "groomer", "grooming",
        "boarding", "microchip", "dog", "cat", "puppy", "kitten",
    },
    "family": {
        "family", "spouse", "partner", "wife", "husband", "kid", "kids",
        "child", "children", "parent", "parents", "sibling", "siblings",
        "school", "teacher", "babysitter", "nanny", "in-law",
        "extracurricular", "playdate",
    },
    "travel": {
        "travel", "trip", "trips", "vacation", "flight", "flights", "hotel",
        "hotels", "airbnb", "rental-car", "passport", "visa",
        "frequent-flier", "loyalty", "tsa", "global-entry", "itinerary",
    },
    "career": {
        "career", "job", "employer", "manager", "colleague", "coworker",
        "performance-review", "salary", "rsu", "401k", "vesting", "equity",
        "professional-development", "promotion", "interview",
    },
    "business": {
        "business", "client", "clients", "invoice", "invoices", "contract",
        "freelance", "freelancing", "consulting", "consultant", "sole-prop",
        "sole-proprietor", "llc", "s-corp", "vendor", "billable",
        "deliverable", "sow", "ein",
    },
    "education": {
        "education", "school", "university", "college", "course", "courses",
        "credit", "credits", "transcript", "transcripts", "advisor",
        "registrar", "bursar", "fafsa", "tuition", "syllabus", "professor",
        "instructor", "degree", "diploma", "certificate", "certification",
        "exam", "midterm", "final",
    },
    "hobbies": {
        "hobbies", "hobby", "fitness", "gym", "workout", "running",
        "cycling", "swim", "swimming", "yoga", "garden", "gardening",
        "workshop", "reading", "book-club", "side-project", "music",
        "photography",
    },
    "self": {
        "self", "personal-development", "journal", "journaling",
        "mindfulness", "meditation", "life-theme", "growth", "reflection",
    },
}


@dataclass
class Evidence:
    """A single signal contributing to a suggestion's score."""
    kind: str  # tag | contact-role | project | source-folder
    ref: str   # entity id / tag id / folder name
    weight: int


@dataclass
class Suggestion:
    """A candidate domain to propose to the user."""
    theme: str
    proposed_name: str
    proposed_scope: str
    proposed_priority: str = "P2"
    rationale: str = ""
    evidence: list[Evidence] = field(default_factory=list)
    score: int = 0


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def workspace_default(framework: Path) -> Path:
    return framework.parent / "workspace"


def _load_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open() as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def _save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


def _suggestions_path(workspace: Path) -> Path:
    return workspace / "_memory" / "domain-suggestions.yaml"


def _strip_empty_stubs(data: dict[str, Any]) -> dict[str, Any]:
    """Template ships with empty-string `theme` placeholder rows; drop them."""
    for key in ("suggested", "accepted", "declined", "deferred", "surfaced"):
        rows = data.get(key) or []
        data[key] = [r for r in rows if isinstance(r, dict) and r.get("theme")]
    return data


def load_suggestions(workspace: Path) -> dict[str, Any]:
    """Load `_memory/domain-suggestions.yaml`. Initialize empty if missing."""
    data = _load_yaml(_suggestions_path(workspace)) or {}
    data.setdefault("schema_version", 1)
    for key in ("suggested", "accepted", "declined", "deferred", "surfaced"):
        data.setdefault(key, [])
    return _strip_empty_stubs(data)


def save_suggestions(workspace: Path, data: dict[str, Any]) -> None:
    data["last_updated"] = now_iso()
    _save_yaml(_suggestions_path(workspace), data)


# Theme + alias helpers -----------------------------------------------------

def normalize_theme(value: str) -> str:
    """Canonicalize a free-form string into a stable theme slug."""
    s = (value or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s


def build_alias_set(workspace: Path) -> set[str]:
    """Return the union of all alias terms for currently-registered domains."""
    aliases: set[str] = set()
    for terms in DOMAIN_ALIASES.values():
        aliases |= terms
    domains_idx = _load_yaml(workspace / "_memory" / "domains-index.yaml") or {}
    for row in (domains_idx.get("domains") or []):
        if not isinstance(row, dict):
            continue
        if row.get("id"):
            aliases.add(row["id"].lower())
        if row.get("name"):
            aliases.add(row["name"].lower())
        for t in (row.get("tags") or []):
            if isinstance(t, str) and t:
                aliases.add(t.lower())
    return aliases


def is_aliased(theme: str, aliases: set[str]) -> bool:
    """Theme matches an alias either exactly OR via any of its hyphen-separated parts.

    Multi-word themes like `garden-supplier` are treated as aliased when any
    significant component (`garden`) maps to a known domain — they are
    sub-categories of an existing domain rather than candidates for a new one.
    """
    if not theme:
        return True
    if theme in aliases:
        return True
    for part in theme.split("-"):
        if len(part) >= 4 and part in aliases:
            return True
    return False


def handled_themes(data: dict[str, Any], today: dt.date | None = None) -> set[str]:
    """Themes that should NOT be re-surfaced.

    Includes accepted (user said yes), declined (with no revisit), and
    deferred (with revisit_after still in the future).
    """
    today = today or dt.date.today()
    out: set[str] = set()
    for row in data.get("accepted", []):
        if isinstance(row, dict) and row.get("theme"):
            out.add(row["theme"])
    for row in data.get("declined", []):
        if not (isinstance(row, dict) and row.get("theme")):
            continue
        ra = row.get("revisit_after")
        if ra is None:
            out.add(row["theme"])
            continue
        try:
            if dt.date.fromisoformat(str(ra)[:10]) > today:
                out.add(row["theme"])
        except ValueError:
            out.add(row["theme"])
    for row in data.get("deferred", []):
        if not (isinstance(row, dict) and row.get("theme")):
            continue
        ra = row.get("revisit_after")
        if ra is None:
            continue
        try:
            if dt.date.fromisoformat(str(ra)[:10]) > today:
                out.add(row["theme"])
        except ValueError:
            continue
    return out


# Signal collectors ---------------------------------------------------------

def _collect_tag_signals(workspace: Path) -> dict[str, list[Evidence]]:
    """Tags from `_memory/tags.yaml.tags[]` weighted by usage count.

    Schema accepts either `uses_count` (canonical per the template) or
    `usage_count` (used by some legacy callers) for robustness.
    """
    out: dict[str, list[Evidence]] = defaultdict(list)
    data = _load_yaml(workspace / "_memory" / "tags.yaml") or {}
    for row in (data.get("tags") or []):
        if not isinstance(row, dict):
            continue
        tag_id = row.get("id") or ""
        usage = int(row.get("uses_count") or row.get("usage_count") or 0)
        if not tag_id or usage < DEFAULT_TAG_THRESHOLD:
            continue
        theme = normalize_theme(tag_id)
        if theme:
            out[theme].append(Evidence(kind="tag", ref=tag_id, weight=usage))
    return out


def _collect_role_signals(workspace: Path) -> dict[str, list[Evidence]]:
    """Contact roles in `_memory/contacts.yaml.contacts[]` clustered by `role`."""
    out: dict[str, list[Evidence]] = defaultdict(list)
    by_role: dict[str, list[str]] = defaultdict(list)
    data = _load_yaml(workspace / "_memory" / "contacts.yaml") or {}
    for row in (data.get("contacts") or []):
        if not isinstance(row, dict):
            continue
        role = row.get("role") or ""
        cid = row.get("id") or ""
        if role and cid:
            by_role[role].append(cid)
    for role, cids in by_role.items():
        if len(cids) < DEFAULT_ROLE_THRESHOLD:
            continue
        theme = normalize_theme(role)
        if not theme:
            continue
        for cid in cids:
            out[theme].append(Evidence(kind="contact-role", ref=cid, weight=2))
    return out


def _significant_words(slug: str) -> list[str]:
    """Strip year suffix and short stop-words from a project slug."""
    slug = re.sub(r"-?\d{4}$", "", slug)
    return [w for w in slug.split("-") if len(w) >= 4]


def _collect_project_signals(workspace: Path) -> dict[str, list[Evidence]]:
    """Project names clustered by shared keyword (≥4 chars)."""
    data = _load_yaml(workspace / "_memory" / "projects-index.yaml") or {}
    rows = data.get("projects") or []
    keyword_counts: Counter[str] = Counter()
    keyword_refs: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = row.get("id") or ""
        if not pid:
            continue
        slug = normalize_theme(row.get("name") or pid)
        for word in _significant_words(slug):
            keyword_counts[word] += 1
            if pid not in keyword_refs[word]:
                keyword_refs[word].append(pid)
    out: dict[str, list[Evidence]] = defaultdict(list)
    for word, count in keyword_counts.items():
        if count < DEFAULT_PROJECT_THRESHOLD:
            continue
        for pid in keyword_refs[word]:
            out[word].append(Evidence(kind="project", ref=pid, weight=3))
    return out


def _collect_source_folder_signals(workspace: Path) -> dict[str, list[Evidence]]:
    """Top-level `Sources/<folder>/` paths clustered by name."""
    out: dict[str, list[Evidence]] = defaultdict(list)
    sources_root = workspace / "Sources"
    if not sources_root.is_dir():
        return out
    for child in sources_root.iterdir():
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.lower() == "readme.md":
            continue
        theme = normalize_theme(child.name)
        if not theme:
            continue
        try:
            entry_count = sum(1 for _ in child.rglob("*") if _.is_file())
        except OSError:
            entry_count = 0
        if entry_count < DEFAULT_SOURCE_FOLDER_THRESHOLD:
            continue
        out[theme].append(Evidence(
            kind="source-folder",
            ref=f"Sources/{child.name}",
            weight=min(entry_count, 10),
        ))
    return out


# Detection + scoring -------------------------------------------------------

def _propose_name_and_scope(theme: str, evidence: list[Evidence]) -> tuple[str, str]:
    """Title-case the theme into a domain name + draft a one-line scope."""
    name = " ".join(part.capitalize() for part in theme.split("-")) or theme.title()
    kinds = sorted({e.kind for e in evidence})
    bits = []
    for k in kinds:
        refs = [e.ref for e in evidence if e.kind == k]
        if k == "tag":
            bits.append(f"tag `{refs[0]}` used across {sum(e.weight for e in evidence if e.kind == 'tag')} entities")
        elif k == "contact-role":
            bits.append(f"{len(refs)} contacts with this role")
        elif k == "project":
            bits.append(f"{len(refs)} projects ({', '.join(refs[:3])})")
        elif k == "source-folder":
            bits.append(f"`{refs[0]}/` with material content")
    rationale = "; ".join(bits)
    scope = (
        f"Auto-detected from {rationale}. Customize this scope on accept "
        "to reflect what {name} actually covers for you."
    ).format(name=name)
    return name, scope


def _build_suggestion(theme: str, evidence: list[Evidence]) -> Suggestion:
    score = sum(e.weight for e in evidence)
    name, scope = _propose_name_and_scope(theme, evidence)
    bits = []
    for k in sorted({e.kind for e in evidence}):
        refs = [e.ref for e in evidence if e.kind == k]
        if k == "tag":
            total = sum(e.weight for e in evidence if e.kind == "tag")
            bits.append(f"tag `{refs[0]}` used {total}x")
        elif k == "contact-role":
            bits.append(f"{len(refs)} contacts with role")
        elif k == "project":
            bits.append(f"{len(refs)} project(s)")
        elif k == "source-folder":
            bits.append(f"folder `{refs[0]}`")
    rationale = "; ".join(bits)
    return Suggestion(
        theme=theme,
        proposed_name=name,
        proposed_scope=scope,
        proposed_priority="P2",
        rationale=rationale,
        evidence=evidence,
        score=score,
    )


def detect(
    workspace: Path,
    *,
    min_score: int = DEFAULT_MIN_SCORE,
    top_n: int = DEFAULT_TOP_N,
) -> list[Suggestion]:
    """Run the detector. Returns the top N candidates above the score floor."""
    aliases = build_alias_set(workspace)
    data = load_suggestions(workspace)
    skip = handled_themes(data)

    merged: dict[str, list[Evidence]] = defaultdict(list)
    for collector in (
        _collect_tag_signals,
        _collect_role_signals,
        _collect_project_signals,
        _collect_source_folder_signals,
    ):
        for theme, evidence in collector(workspace).items():
            if is_aliased(theme, aliases) or theme in skip:
                continue
            merged[theme].extend(evidence)

    suggestions = [
        _build_suggestion(theme, evidence)
        for theme, evidence in merged.items()
    ]
    suggestions = [s for s in suggestions if s.score >= min_score]
    suggestions.sort(key=lambda s: -s.score)
    return suggestions[:top_n]


def write_suggestions(workspace: Path, suggestions: list[Suggestion]) -> int:
    """Persist newly-detected suggestions into `suggested[]` (idempotent by theme)."""
    data = load_suggestions(workspace)
    existing_themes = {r.get("theme") for r in data.get("suggested", []) if isinstance(r, dict)}
    handled = handled_themes(data)
    written = 0
    for s in suggestions:
        if s.theme in existing_themes or s.theme in handled:
            continue
        data["suggested"].append({
            "theme": s.theme,
            "proposed_name": s.proposed_name,
            "proposed_scope": s.proposed_scope,
            "proposed_priority": s.proposed_priority,
            "rationale": s.rationale,
            "evidence": [asdict(e) for e in s.evidence],
            "score": s.score,
            "detected_at": now_iso(),
            "last_surfaced_at": None,
        })
        written += 1
    if written:
        save_suggestions(workspace, data)
    return written


def record_answer(workspace: Path, theme: str, answer: str) -> bool:
    """Move a suggested[] row to accepted/declined/deferred per the user's answer."""
    if answer not in {"yes", "not_now", "never"}:
        raise ValueError(f"answer must be yes | not_now | never (got {answer!r})")
    data = load_suggestions(workspace)
    src = data.get("suggested", [])
    matched = next((r for r in src if isinstance(r, dict) and r.get("theme") == theme), None)
    if matched is None:
        return False
    data["suggested"] = [r for r in src if r is not matched]
    matched["last_surfaced_at"] = now_iso()
    today = dt.date.today()
    if answer == "yes":
        data["accepted"].append({
            "theme": theme,
            "domain_id": "",
            "accepted_at": now_iso(),
            "proposed_name": matched.get("proposed_name", ""),
            "proposed_scope": matched.get("proposed_scope", ""),
            "proposed_priority": matched.get("proposed_priority", "P2"),
        })
    elif answer == "not_now":
        revisit = (today + dt.timedelta(days=DEFAULT_DEFER_DAYS)).isoformat()
        data["deferred"].append({
            "theme": theme,
            "deferred_at": now_iso(),
            "revisit_after": revisit,
        })
    else:
        data["declined"].append({
            "theme": theme,
            "declined_at": now_iso(),
            "revisit_after": None,
        })
    data["surfaced"].append({
        "theme": theme,
        "session_id": "cli",
        "surfaced_at": now_iso(),
        "answer": answer,
    })
    save_suggestions(workspace, data)
    return True


def forget(workspace: Path, theme: str) -> bool:
    """Remove a theme from declined/deferred so the detector may re-surface it."""
    data = load_suggestions(workspace)
    removed = False
    for key in ("declined", "deferred"):
        before = len(data.get(key, []))
        data[key] = [
            r for r in data.get(key, [])
            if not (isinstance(r, dict) and r.get("theme") == theme)
        ]
        if len(data[key]) != before:
            removed = True
    if removed:
        save_suggestions(workspace, data)
    return removed


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="domain_detector")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--framework",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="Detect candidate domains and print results.")
    r.add_argument(
        "--write",
        action="store_true",
        help="Persist new candidates to domain-suggestions.yaml.suggested[].",
    )
    r.add_argument("--min-score", type=int, default=DEFAULT_MIN_SCORE)
    r.add_argument("--top-n", type=int, default=DEFAULT_TOP_N)
    r.add_argument("--json", action="store_true")
    sub.add_parser("list", help="Show the current state of all suggestion buckets.")
    rec = sub.add_parser(
        "record",
        help="Record a user answer for a previously-surfaced theme.",
    )
    rec.add_argument("theme")
    rec.add_argument("answer", choices=("yes", "not_now", "never"))
    f = sub.add_parser(
        "forget",
        help="Remove a theme from declined/deferred so it may surface again.",
    )
    f.add_argument("theme")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework: Path = args.framework
    workspace: Path = args.workspace or workspace_default(framework)
    if not (workspace / "_memory").exists():
        print(f"no workspace at {workspace}", file=sys.stderr)
        return 1

    if args.cmd == "run":
        suggestions = detect(
            workspace,
            min_score=args.min_score,
            top_n=args.top_n,
        )
        if args.json:
            print(json.dumps(
                [
                    {
                        **{k: v for k, v in asdict(s).items() if k != "evidence"},
                        "evidence": [asdict(e) for e in s.evidence],
                    }
                    for s in suggestions
                ],
                indent=2,
                default=str,
            ))
        else:
            if not suggestions:
                print("No new domain candidates above threshold.")
            for s in suggestions:
                print(f"\n# {s.proposed_name}  (theme: {s.theme}, score: {s.score})")
                print(f"  Why: {s.rationale}")
                print(f"  Proposed scope: {s.proposed_scope}")
                for e in s.evidence:
                    print(f"    - [{e.kind}] {e.ref}  (weight {e.weight})")
        if args.write:
            written = write_suggestions(workspace, suggestions)
            print(f"\nWrote {written} new suggestion(s) to "
                  f"_memory/domain-suggestions.yaml.suggested[].")
        return 0

    if args.cmd == "list":
        data = load_suggestions(workspace)
        for key in ("suggested", "accepted", "declined", "deferred"):
            rows = data.get(key, [])
            print(f"\n## {key}  ({len(rows)})")
            for r in rows:
                if not isinstance(r, dict):
                    continue
                tail = ""
                if key == "deferred" and r.get("revisit_after"):
                    tail = f"  (revisit after {r['revisit_after']})"
                elif key == "accepted" and r.get("domain_id"):
                    tail = f"  → domain:{r['domain_id']}"
                print(f"  - {r.get('theme'):20s}{tail}")
        return 0

    if args.cmd == "record":
        ok = record_answer(workspace, args.theme, args.answer)
        print(f"recorded {args.answer!r} for theme {args.theme!r}"
              if ok else f"theme {args.theme!r} not in suggested[]")
        return 0 if ok else 1

    if args.cmd == "forget":
        ok = forget(workspace, args.theme)
        print(f"cleared theme {args.theme!r} from declined/deferred"
              if ok else f"theme {args.theme!r} was not declined/deferred")
        return 0 if ok else 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
