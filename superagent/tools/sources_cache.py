#!/usr/bin/env -S uv run python
"""Sources cache manager for Superagent.

Implements the local-first read pattern documented in `contracts/sources.md` § 15.4
+ § 15.5. The agent and skills route every fetch of a `.ref.md`-pointed
source through this module so caching, eviction, and chunking are uniform.

CLI:
  uv run python -m superagent.tools.sources_cache get <ref-id>           # local-first read
  uv run python -m superagent.tools.sources_cache fetch <ref-id> [--refresh]
  uv run python -m superagent.tools.sources_cache evict --all-stale | --over-cap | <ref-id>
  uv run python -m superagent.tools.sources_cache list

The actual upstream-fetching for each `kind` (mcp / cli / url / api / file /
vault / manual) is delegated to the matching ingestor or to a kind-specific
shim. In MVP, only `kind: file` (read a local file) and `kind: cli`
(run a shell command) are wired in; the rest return a stub fetch result and
defer to the corresponding ingestor when it ships.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


# Maximum cache write before LRU sweep runs (bytes).
DEFAULT_CACHE_MAX_MB = 500
DEFAULT_TTL_MINUTES = 1440
DEFAULT_CHUNK_THRESHOLD_KB = 100
DEFAULT_CHUNK_TARGET_KB = 8


def now_iso() -> str:
    """Return current local time as ISO 8601 with timezone offset."""
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def now_dt() -> dt.datetime:
    """Return current local datetime."""
    return dt.datetime.now().astimezone()


def parse_iso(value: str | None) -> dt.datetime | None:
    """Parse an ISO 8601 string; return None on failure."""
    if not value or not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value)
    except ValueError:
        return None


def load_yaml(path: Path) -> dict[str, Any]:
    """Load YAML; return empty dict on failure or missing."""
    if not path.exists():
        return {}
    try:
        with path.open() as fh:
            return yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomically write YAML."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)
    tmp.replace(path)


DEFAULT_CACHE_REL = "Sources/_cache"


def load_config(workspace: Path) -> dict[str, Any]:
    """Load `_memory/config.yaml.preferences.sources`."""
    cfg = load_yaml(workspace / "_memory" / "config.yaml")
    prefs = (cfg.get("preferences") or {}).get("sources") or {}
    return {
        "cache_path": str(prefs.get("cache_path", DEFAULT_CACHE_REL)),
        "cache_max_mb": int(prefs.get("cache_max_mb", DEFAULT_CACHE_MAX_MB)),
        "default_ttl_minutes": int(prefs.get("default_ttl_minutes", DEFAULT_TTL_MINUTES)),
        "chunk_threshold_kb": int(prefs.get("chunk_threshold_kb", DEFAULT_CHUNK_THRESHOLD_KB)),
        "chunk_target_kb": int(prefs.get("chunk_target_kb", DEFAULT_CHUNK_TARGET_KB)),
        "summary_first": bool(prefs.get("summary_first", True)),
    }


def cache_root(workspace: Path, config: dict[str, Any] | None = None) -> Path:
    """Return the configured cache root (default `Sources/_cache/`)."""
    cfg = config or load_config(workspace)
    cache_path = cfg["cache_path"]
    p = Path(cache_path)
    return p if p.is_absolute() else workspace / p


def parse_ref_md(path: Path) -> dict[str, Any]:
    """Parse a ref file (`.ref.md` or `.ref.txt`) into a dict + 'body' string.

    Canonical form (YAML frontmatter) is the fast path. Non-canonical files
    are passed to `sources_normalize.propose` which lifts the canonical
    fields liberally; the cache uses those fields READ-ONLY (does NOT rewrite
    the file — that's the user's call via the normalize prompt).

    Raises ValueError if required fields (`kind`, `source`) cannot be
    resolved either way.
    """
    if not path.exists():
        raise FileNotFoundError(f"ref file not found: {path}")
    body = path.read_text()
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", body, re.DOTALL)
    if match:
        fm = yaml.safe_load(match.group(1)) or {}
        if isinstance(fm, dict) and fm.get("kind") and fm.get("source"):
            fm["body"] = match.group(2).strip()
            return fm
    # Liberal fallback for hand-authored `.ref.txt` / loose `.ref.md`.
    try:
        from superagent.tools.sources_normalize import propose
    except ImportError:
        raise ValueError(
            f"{path}: missing canonical YAML frontmatter and "
            f"sources_normalize unavailable"
        )
    proposal = propose(path)
    fields = dict(proposal["fields"])
    if not (fields.get("kind") and fields.get("source")):
        raise ValueError(
            f"{path}: cannot resolve required fields (kind, source). "
            f"Run sources_normalize apply --mode ask <path> to fix."
        )
    fields["body"] = fields.pop("_notes", "")
    return fields


def source_hash(kind: str, source: str) -> str:
    """Deterministic 16-hex-char hash for cache pathing."""
    canonical = f"{kind}::{source.strip()}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def cache_dir(workspace: Path, kind: str, source: str,
              config: dict[str, Any] | None = None) -> Path:
    """Path to the cache folder for one source."""
    return cache_root(workspace, config) / source_hash(kind, source)


def is_cache_fresh(meta: dict[str, Any], ttl_minutes: int) -> bool:
    """Return True if the cache entry's age < ttl_minutes."""
    if ttl_minutes <= 0:
        return False
    fetched = parse_iso(meta.get("fetched_at"))
    if fetched is None:
        return False
    age = now_dt() - fetched
    return age.total_seconds() / 60.0 < ttl_minutes


def total_cache_size(workspace: Path, config: dict[str, Any] | None = None) -> int:
    """Compute the total size of the cache root in bytes."""
    root = cache_root(workspace, config)
    if not root.exists():
        return 0
    total = 0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def evict_lru(workspace: Path, target_bytes: int,
              config: dict[str, Any] | None = None) -> int:
    """Evict cache entries (oldest last_used first) until under target_bytes.

    Returns the count of entries evicted.
    """
    root = cache_root(workspace, config)
    if not root.exists():
        return 0
    entries = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        meta_path = sub / "_meta.yaml"
        if not meta_path.exists():
            continue
        meta = load_yaml(meta_path)
        last_used = parse_iso(meta.get("last_used")) or parse_iso(meta.get("fetched_at"))
        size = sum(
            p.stat().st_size for p in sub.rglob("*") if p.is_file()
        )
        entries.append((last_used or dt.datetime.fromtimestamp(0, tz=dt.timezone.utc),
                        sub, size))
    entries.sort(key=lambda x: x[0])
    evicted = 0
    current = total_cache_size(workspace, config)
    for _last_used, sub, size in entries:
        if current <= target_bytes:
            break
        shutil.rmtree(sub, ignore_errors=True)
        current -= size
        evicted += 1
    return evicted


def write_summary(cache: Path, raw: bytes | str, kind: str) -> None:
    """Write a summary file.

    QW-6 (superagent/docs/_internal/perf-improvement-ideas.md): the summary surfaces (a) what the
    document is, (b) what's in it (per-section bullets when markdown), and
    (c) where to look for specific topics (heading list with line numbers).
    The first three sections are cheap to extract heuristically; the agent
    can call this routine and then enhance the file in the same turn with
    an LLM-generated 1-2 sentence overview if it wishes.

    Falls back to a "first N chars" snippet for binary or non-markdown.
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            text = f"<binary content, {len(raw)} bytes>"
    else:
        text = raw
    headings: list[tuple[int, int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#") and " " in stripped[:8]:
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            if heading:
                headings.append((i, level, heading))
    bytes_count = len(text.encode("utf-8")) if isinstance(text, str) else len(raw)
    line_count = len(text.splitlines())
    snippet = text[:400].strip()
    if len(text) > 400:
        snippet += "\n\n…(truncated; full content in `raw.<ext>` or `chunks/`)…"

    body_lines = [
        "# Cache summary",
        "",
        f"_kind: {kind} · cached: {now_iso()} · {bytes_count:,} bytes · {line_count:,} lines_",
        "",
        "## What this document is",
        "",
    ]
    if headings:
        first_h1 = next((h for _, lvl, h in headings if lvl == 1), None)
        if first_h1:
            body_lines.append(f"Title (H1): **{first_h1}**.")
        body_lines.append(f"Contains **{len(headings)} heading(s)** organized into "
                          f"{len([1 for _, lvl, _ in headings if lvl == 1])} top-level section(s).")
    else:
        body_lines.append("No markdown headings detected; treated as a single block of content.")

    body_lines += ["", "## Section index (for `Read --offset --limit` targeting)", ""]
    if headings:
        body_lines.append("| Heading | Level | Line |")
        body_lines.append("|---------|-------|------|")
        for line_no, level, heading in headings[:40]:
            body_lines.append(f"| {heading} | H{level} | {line_no} |")
        if len(headings) > 40:
            body_lines.append(f"| _… and {len(headings) - 40} more headings — see `_toc.yaml`_ | | |")
    else:
        body_lines.append("(none)")

    body_lines += ["", "## First 400 characters", "", snippet, ""]
    (cache / "_summary.md").write_text("\n".join(body_lines))


def write_toc(cache: Path, raw: str) -> None:
    """Write a basic table-of-contents for the cached content.

    Looks for markdown-style headings (#, ##, ###) and emits a list of
    `{ heading, line }` pairs. For non-markdown content, emits an empty TOC.
    """
    sections = []
    for i, line in enumerate(raw.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#") and " " in stripped[:8]:
            level = len(stripped) - len(stripped.lstrip("#"))
            heading = stripped[level:].strip()
            if heading:
                sections.append({"level": level, "heading": heading, "line": i})
    save_yaml(cache / "_toc.yaml", {"sections": sections})


def maybe_chunk(cache: Path, raw_path: Path, config: dict[str, Any]) -> int:
    """Split raw into chunks if it exceeds threshold. Returns chunk count."""
    threshold_bytes = config["chunk_threshold_kb"] * 1024
    if not raw_path.exists() or raw_path.stat().st_size <= threshold_bytes:
        return 0
    try:
        text = raw_path.read_text()
    except UnicodeDecodeError:
        return 0
    target_bytes = config["chunk_target_kb"] * 1024
    chunks_dir = cache / "chunks"
    chunks_dir.mkdir(exist_ok=True)
    paragraphs = re.split(r"\n\s*\n", text)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para_len = len(para.encode("utf-8"))
        if current_len + para_len > target_bytes and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len
    if current:
        chunks.append("\n\n".join(current))
    for i, chunk_body in enumerate(chunks, start=1):
        (chunks_dir / f"chunk-{i:03d}.md").write_text(chunk_body)
    save_yaml(chunks_dir / "_index.yaml", {
        "chunks": [
            {"file": f"chunk-{i:03d}.md", "size_bytes": len(c.encode("utf-8"))}
            for i, c in enumerate(chunks, start=1)
        ],
    })
    return len(chunks)


def fetch_kind_file(source: str) -> tuple[bytes, str]:
    """For kind: file — read a local file. Returns (content, ext)."""
    path = Path(source).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"file source not found: {path}")
    ext = path.suffix.lstrip(".") or "bin"
    return path.read_bytes(), ext


def fetch_kind_cli(source: str) -> tuple[bytes, str]:
    """For kind: cli — run a shell command, capture stdout."""
    result = subprocess.run(
        ["sh", "-c", source],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cli fetch failed (rc={result.returncode}): {result.stderr.decode('utf-8', errors='replace')[:200]}"
        )
    return result.stdout, "txt"


def fetch_kind_url(source: str) -> tuple[bytes, str]:
    """For kind: url — HTTP GET. Uses urllib (stdlib) — no external deps."""
    import urllib.request

    with urllib.request.urlopen(source, timeout=30) as resp:
        body = resp.read()
        ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype:
        ext = "json"
    elif "html" in ctype:
        ext = "html"
    elif "markdown" in ctype:
        ext = "md"
    elif "pdf" in ctype:
        ext = "pdf"
    else:
        ext = "txt"
    return body, ext


def fetch_unimplemented(source: str, kind: str) -> tuple[bytes, str]:
    """Stub for kinds that aren't wired in yet (mcp / api / vault / manual)."""
    raise NotImplementedError(
        f"kind '{kind}' not yet implemented in MVP. "
        f"Source: {source}. "
        f"Roadmap: implement the matching ingestor under "
        f"superagent/tools/ingest/, then wire it into sources_cache.py."
    )


FETCH_HANDLERS = {
    "file": fetch_kind_file,
    "cli": fetch_kind_cli,
    "url": fetch_kind_url,
}


def fetch_to_cache(workspace: Path, kind: str, source: str,
                   ttl_minutes: int, sensitive: bool, params: dict[str, Any] | None,
                   config: dict[str, Any]) -> dict[str, Any]:
    """Fetch one source and write to cache. Returns the meta dict."""
    if kind not in FETCH_HANDLERS:
        if kind == "manual":
            raise RuntimeError(
                f"kind 'manual' requires the user to fetch this source. "
                f"Instructions: {source}"
            )
        raw, ext = fetch_unimplemented(source, kind)
    else:
        raw, ext = FETCH_HANDLERS[kind](source)

    cache = cache_dir(workspace, kind, source, config)
    cache.mkdir(parents=True, exist_ok=True)
    raw_path = cache / f"raw.{ext}"
    if isinstance(raw, str):
        raw_path.write_text(raw)
        write_toc(cache, raw)
        write_summary(cache, raw, kind)
    else:
        raw_path.write_bytes(raw)
        try:
            text = raw.decode("utf-8")
            write_toc(cache, text)
        except UnicodeDecodeError:
            save_yaml(cache / "_toc.yaml", {"sections": []})
        write_summary(cache, raw, kind)

    chunks_count = maybe_chunk(cache, raw_path, config)

    size_bytes = sum(p.stat().st_size for p in cache.rglob("*") if p.is_file())
    meta = {
        "source_hash": source_hash(kind, source),
        "source": {"kind": kind, "source": source, "params": params or {}},
        "sensitive": sensitive,
        "fetched_at": now_iso(),
        "last_used": now_iso(),
        "ttl_minutes": ttl_minutes,
        "size_bytes": size_bytes,
        "chunks_count": chunks_count,
    }
    save_yaml(cache / "_meta.yaml", meta)

    cap_bytes = config["cache_max_mb"] * 1024 * 1024
    if total_cache_size(workspace, config) > cap_bytes:
        evict_lru(workspace, cap_bytes, config)

    return meta


def get_cache(workspace: Path, ref_path: Path,
              refresh: bool = False) -> dict[str, Any]:
    """Local-first read of a ref file. Returns the meta dict."""
    config = load_config(workspace)
    fm = parse_ref_md(ref_path)
    kind = fm.get("kind", "")
    source = fm.get("source", "")
    if not kind or not source:
        raise ValueError(f"{ref_path}: missing kind or source in frontmatter")
    ttl = int(fm.get("ttl_minutes") if fm.get("ttl_minutes") is not None else config["default_ttl_minutes"])
    sensitive = bool(fm.get("sensitive", False))
    params = fm.get("params") or {}
    cache = cache_dir(workspace, kind, source, config)
    meta_path = cache / "_meta.yaml"
    if not refresh and meta_path.exists():
        meta = load_yaml(meta_path)
        if is_cache_fresh(meta, ttl):
            meta["last_used"] = now_iso()
            save_yaml(meta_path, meta)
            return meta
    return fetch_to_cache(workspace, kind, source, ttl, sensitive, params, config)


def list_cache(workspace: Path, config: dict[str, Any] | None = None,
               ) -> list[dict[str, Any]]:
    """Return a list of cache entries summarizing each."""
    root = cache_root(workspace, config)
    if not root.exists():
        return []
    entries = []
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        meta = load_yaml(sub / "_meta.yaml")
        if not meta:
            continue
        entries.append({
            "hash": sub.name,
            "kind": (meta.get("source") or {}).get("kind", ""),
            "source": (meta.get("source") or {}).get("source", "")[:60],
            "fetched_at": meta.get("fetched_at"),
            "last_used": meta.get("last_used"),
            "size_kb": round((meta.get("size_bytes") or 0) / 1024, 1),
            "chunks": meta.get("chunks_count", 0),
            "ttl_minutes": meta.get("ttl_minutes"),
            "sensitive": meta.get("sensitive", False),
        })
    return entries


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse CLI args."""
    parser = argparse.ArgumentParser(prog="sources_cache")
    parser.add_argument("--workspace", type=Path, default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="Local-first read of a `.ref.md` (returns meta JSON).")
    g.add_argument("ref", type=Path, help="Path to the `.ref.md` file.")
    g.add_argument("--refresh", action="store_true")

    f = sub.add_parser("fetch", help="Force-fetch a `.ref.md` to cache.")
    f.add_argument("ref", type=Path, help="Path to the `.ref.md` file.")
    f.add_argument("--refresh", action="store_true")

    e = sub.add_parser("evict", help="Evict cache entries.")
    e.add_argument("--all-stale", action="store_true",
                   help="Evict every entry past its TTL.")
    e.add_argument("--over-cap", action="store_true",
                   help="Evict LRU until under cache_max_mb.")
    e.add_argument("--hash", type=str, default=None,
                   help="Evict the cache entry with this hash.")

    sub.add_parser("list", help="List every cache entry.")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv if argv is not None else sys.argv[1:])
    framework = Path(__file__).resolve().parent.parent
    workspace = args.workspace or framework.parent / "workspace"
    if not (workspace / "_memory").exists():
        print(f"No workspace at {workspace}; run workspace_init.py first.",
              file=sys.stderr)
        return 1
    if args.cmd == "get":
        try:
            meta = get_cache(workspace, args.ref, refresh=args.refresh)
        except (FileNotFoundError, ValueError, RuntimeError, NotImplementedError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(meta, indent=2, default=str))
        return 0
    if args.cmd == "fetch":
        try:
            meta = get_cache(workspace, args.ref, refresh=True)
        except (FileNotFoundError, ValueError, RuntimeError, NotImplementedError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(meta, indent=2, default=str))
        return 0
    if args.cmd == "evict":
        config = load_config(workspace)
        cap = config["cache_max_mb"] * 1024 * 1024
        root = cache_root(workspace, config)
        if args.all_stale:
            removed = 0
            if root.exists():
                for sub in root.iterdir():
                    if not sub.is_dir():
                        continue
                    meta = load_yaml(sub / "_meta.yaml")
                    if not meta:
                        continue
                    if not is_cache_fresh(meta, int(meta.get("ttl_minutes", 0) or 0)):
                        shutil.rmtree(sub, ignore_errors=True)
                        removed += 1
            print(f"Evicted {removed} stale entries.")
            return 0
        if args.over_cap:
            n = evict_lru(workspace, cap, config)
            print(f"Evicted {n} entries to bring cache under cap.")
            return 0
        if args.hash:
            target = root / args.hash
            if target.exists():
                shutil.rmtree(target)
                print(f"Evicted {args.hash}")
                return 0
            print(f"No cache entry with hash {args.hash}", file=sys.stderr)
            return 1
        print("Specify --all-stale, --over-cap, or --hash <hash>", file=sys.stderr)
        return 2
    if args.cmd == "list":
        entries = list_cache(workspace)
        if not entries:
            print("Cache is empty.")
            return 0
        print(f"{'Hash':<20}{'Kind':<10}{'Size KB':<10}{'Chunks':<8}{'Age':<24}{'Source':<60}")
        print("-" * 132)
        for e in entries:
            fetched = parse_iso(e.get("fetched_at"))
            age = (now_dt() - fetched).total_seconds() / 3600 if fetched else 0
            age_label = f"{age:.1f}h"
            print(f"{e['hash']:<20}{e['kind']:<10}{e['size_kb']:<10}{e['chunks']:<8}{age_label:<24}{e['source']:<60}")
        total_kb = sum(e["size_kb"] for e in entries)
        print(f"\nTotal: {len(entries)} entries, {total_kb:.1f} KB")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
