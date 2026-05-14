#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Per-message email archive under `workspace/_memory/email/`.

Backs `contracts/email-capture.md`. The agent calls the Gmail MCP itself
(`read_email` / `send_email` / `search_emails`); the resulting payload is
fed to one of the `capture_*` helpers below, which write the per-message
JSON, append a sidecar row to `_messages.jsonl`, and bump the counters in
`_index.yaml`. No helper in this module talks to any MCP, network, or
external CLI — they are pure I/O over the workspace filesystem.

Layout produced by this module (see the contract for the full schema)::

    workspace/_memory/email/
        _index.yaml              # singleton; counts + config
        _messages.jsonl          # append-only sidecar, latest-wins per id
        <YYYY>/<MM>/<DD>/
            <YYYY-MM-DD>_<in|out>_<from_slug>_<subject_slug>_<hash8>.json
        attachments/             # lazy; only on save_attachment()
            <sha256_8>_<safe_filename>

Public API:
    capture_inbound(raw_message, *, workspace=None, direction=None) -> CaptureResult
    capture_sent(request, response, *, workspace=None) -> CaptureResult
    maybe_capture_stubs(search_results, *, workspace=None) -> list[CaptureResult]
    save_attachment(message_id, attachment_id, filename, content_bytes,
                    reason, *, workspace=None) -> AttachmentResult
    find(message_id, *, workspace=None) -> SidecarRecord | None
    find_by_query(*, workspace=None, **filters) -> list[SidecarRecord]
    receipt_heuristic(subject, sender) -> bool

CLI:
    uv run python -m superagent.tools.email.archive find <message-id>
    uv run python -m superagent.tools.email.archive query [--from PAT]
                [--subject PAT] [--since DATE] [--until DATE] [--limit N]
    uv run python -m superagent.tools.email.archive stats
"""
from __future__ import annotations

import argparse
import dataclasses as dc
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
EMAIL_DIR_NAME = "email"
INDEX_FILENAME = "_index.yaml"
SIDECAR_FILENAME = "_messages.jsonl"
ATTACHMENTS_DIRNAME = "attachments"

MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB; mirrors Co-SA.

# Subject-line heuristic that flags receipt-like messages (case-insensitive,
# word-boundary). Kept narrow on purpose; the agent's task context covers the
# long tail.
_RECEIPT_SUBJECT_RE = re.compile(
    r"\b(receipt|invoice|order|payment|confirmation|booking|reservation|"
    r"statement|ticket|registration|paid)\b",
    re.IGNORECASE,
)
# Known receipt sender domains. Suffix-matched against the email address's
# domain (so `*-receipts@stripe.com` and `noreply@stripe.com` both match).
_RECEIPT_SENDER_DOMAINS: tuple[str, ...] = (
    "stripe.com",
    "paypal.com",
    "squareup.com",
    "intuit.com",
)
# Local-part patterns that indicate a receipt mailbox even on a generic domain.
_RECEIPT_LOCAL_RE = re.compile(r"(receipt|receipts|billing|invoice)", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dc.dataclass(frozen=True)
class SidecarRecord:
    """One row of `_messages.jsonl`. Fields mirror the contract."""

    id: str
    thread_id: str
    path: str
    hash: str
    kind: str  # "stub" | "full"
    direction: str  # "in" | "out"
    from_: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    subject: str
    internal_date_utc: str
    labels: list[str]
    snippet: str
    has_attachments: bool
    attachments_saved: int
    captured_at: str
    provenance: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        """Return the JSON-serializable dict form (with `from` instead of `from_`)."""
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "path": self.path,
            "hash": self.hash,
            "kind": self.kind,
            "direction": self.direction,
            "from": self.from_,
            "to": self.to,
            "cc": self.cc,
            "bcc": self.bcc,
            "subject": self.subject,
            "internal_date_utc": self.internal_date_utc,
            "labels": self.labels,
            "snippet": self.snippet,
            "has_attachments": self.has_attachments,
            "attachments_saved": self.attachments_saved,
            "captured_at": self.captured_at,
            "provenance": self.provenance,
        }

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> SidecarRecord:
        return cls(
            id=obj["id"],
            thread_id=obj.get("thread_id", ""),
            path=obj.get("path", ""),
            hash=obj.get("hash", ""),
            kind=obj.get("kind", "stub"),
            direction=obj.get("direction", "in"),
            from_=obj.get("from", ""),
            to=list(obj.get("to") or []),
            cc=list(obj.get("cc") or []),
            bcc=list(obj.get("bcc") or []),
            subject=obj.get("subject", ""),
            internal_date_utc=obj.get("internal_date_utc", ""),
            labels=list(obj.get("labels") or []),
            snippet=obj.get("snippet", ""),
            has_attachments=bool(obj.get("has_attachments", False)),
            attachments_saved=int(obj.get("attachments_saved", 0)),
            captured_at=obj.get("captured_at", ""),
            provenance=dict(obj.get("provenance") or {}),
        )


@dc.dataclass(frozen=True)
class CaptureResult:
    """Outcome of one capture call."""

    record: SidecarRecord
    action: str  # "created" | "upgraded" | "updated" | "noop"
    path: Path  # absolute path to the per-message JSON


@dc.dataclass(frozen=True)
class AttachmentResult:
    """Outcome of one save_attachment call."""

    saved: bool
    path: Path | None  # absolute path on disk, or None if skipped
    sha256: str
    reason: str
    note: str = ""


# ---------------------------------------------------------------------------
# Workspace + path helpers
# ---------------------------------------------------------------------------


def _resolve_workspace(workspace: Path | str | None) -> Path:
    if workspace is not None:
        return Path(workspace)
    # Default: sibling of the superagent/ package.
    return Path(__file__).resolve().parents[3] / "workspace"


def _email_dir(workspace: Path) -> Path:
    return workspace / "_memory" / EMAIL_DIR_NAME


def _index_path(workspace: Path) -> Path:
    return _email_dir(workspace) / INDEX_FILENAME


def _sidecar_path(workspace: Path) -> Path:
    return _email_dir(workspace) / SIDECAR_FILENAME


def _attachments_dir(workspace: Path) -> Path:
    return _email_dir(workspace) / ATTACHMENTS_DIRNAME


# ---------------------------------------------------------------------------
# Slug + hash helpers
# ---------------------------------------------------------------------------

_SLUG_DROP_RE = re.compile(r"[^A-Za-z0-9]+")
_SUBJECT_PREFIX_RE = re.compile(r"^(re|fw|fwd|aw|sv)\s*:\s*", re.IGNORECASE)


def _slugify(value: str, *, max_len: int = 40) -> str:
    """Lowercase, alphanum + `_`, collapsed, trimmed, length-capped.

    Empty / whitespace-only input returns `"unknown"`. Truncation keeps a
    full prefix (no mid-word cut at a hyphen since we use underscores).
    """
    if not value:
        return "unknown"
    s = _SLUG_DROP_RE.sub("_", value).strip("_").lower()
    if not s:
        return "unknown"
    if len(s) > max_len:
        # Truncate at the last underscore <= max_len if one exists; else hard-cap.
        cut = s.rfind("_", 0, max_len)
        s = s[:cut] if cut > max_len // 2 else s[:max_len]
        s = s.rstrip("_")
    return s or "unknown"


def _from_slug(from_header: str) -> str:
    """Best-effort display-name / local-part slug from a raw `From` header."""
    if not from_header:
        return "unknown"
    angle = from_header.find("<")
    if angle > 0:
        name = from_header[:angle].strip().strip('"').strip("'")
        if name:
            return _slugify(name, max_len=30)
        addr = from_header[angle + 1 :].rstrip(">")
        local = addr.split("@", 1)[0]
        return _slugify(local, max_len=30)
    if "@" in from_header:
        local = from_header.split("@", 1)[0]
        return _slugify(local, max_len=30)
    return _slugify(from_header, max_len=30)


def _subject_slug(subject: str) -> str:
    if not subject:
        return "no_subject"
    cleaned = subject
    # Strip one or more reply / forward prefixes ("Re: Fwd: ...").
    while True:
        m = _SUBJECT_PREFIX_RE.match(cleaned)
        if not m:
            break
        cleaned = cleaned[m.end() :]
    return _slugify(cleaned, max_len=40)


def _msg_hash(message_id: str) -> str:
    """First 16 hex chars of sha256(message_id). Filename uses [:8]."""
    return hashlib.sha256(message_id.encode("utf-8")).hexdigest()[:16]


def _now_utc_iso() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _internal_date_utc(raw_internal_date: Any) -> str:
    """Gmail `internalDate` is epoch ms (string or int). Return ISO UTC."""
    if not raw_internal_date and raw_internal_date != 0:
        return _now_utc_iso()
    try:
        ms = int(raw_internal_date)
    except (TypeError, ValueError):
        return _now_utc_iso()
    return (
        dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC)
        .strftime("%Y-%m-%dT%H:%M:%S+00:00")
    )


# ---------------------------------------------------------------------------
# Header / payload extraction
# ---------------------------------------------------------------------------


def _headers_map(raw: dict[str, Any]) -> dict[str, str]:
    headers = (raw.get("payload") or {}).get("headers") or []
    out: dict[str, str] = {}
    for h in headers:
        name = (h.get("name") or "").lower()
        if name and name not in out:
            out[name] = h.get("value") or ""
    return out


def _split_addresses(value: str) -> list[str]:
    """Split a comma-separated address header. Returns [] for empty input."""
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    return [p for p in parts if p]


def _has_attachments(raw: dict[str, Any]) -> bool:
    """True if any payload part carries a non-empty `filename`."""
    payload = raw.get("payload") or {}

    def _walk(node: dict[str, Any]) -> bool:
        if node.get("filename"):
            return True
        return any(_walk(child) for child in node.get("parts") or [])

    return _walk(payload)


# ---------------------------------------------------------------------------
# _index.yaml read / write
# ---------------------------------------------------------------------------


def _default_index() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "attachments": "metadata",  # or "download" if user opted in workspace-wide
        "counts": {
            "total": 0,
            "full": 0,
            "stubs": 0,
            "inbound": 0,
            "outbound": 0,
            "attachments_saved": 0,
        },
        "first_capture": None,
        "last_capture": None,
    }


def _load_index(workspace: Path) -> dict[str, Any]:
    path = _index_path(workspace)
    if not path.exists():
        return _default_index()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return _default_index()
    if not isinstance(data, dict):
        return _default_index()
    # Backfill any missing keys.
    base = _default_index()
    base.update({k: v for k, v in data.items() if k != "counts"})
    raw_counts = data.get("counts") or {}
    if isinstance(raw_counts, dict):
        base["counts"].update({k: v for k, v in raw_counts.items() if k in base["counts"]})
    return base


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _save_index(workspace: Path, index: dict[str, Any]) -> None:
    body = yaml.safe_dump(index, sort_keys=False, default_flow_style=False)
    _atomic_write_text(_index_path(workspace), body)


def _ensure_email_layout(workspace: Path) -> None:
    """Create `_memory/email/` and an empty sidecar/index on first use."""
    _email_dir(workspace).mkdir(parents=True, exist_ok=True)
    if not _index_path(workspace).exists():
        _save_index(workspace, _default_index())
    sidecar = _sidecar_path(workspace)
    if not sidecar.exists():
        sidecar.touch()


# ---------------------------------------------------------------------------
# Sidecar read / write
# ---------------------------------------------------------------------------


def _read_sidecar(workspace: Path) -> list[SidecarRecord]:
    """Read the sidecar with **latest-wins** dedup on `id`.

    Returns rows in capture order (earliest first), one per unique id.
    """
    path = _sidecar_path(workspace)
    if not path.exists():
        return []
    by_id: dict[str, SidecarRecord] = {}
    order: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = obj.get("id")
        if not mid:
            continue
        if mid not in by_id:
            order.append(mid)
        by_id[mid] = SidecarRecord.from_json(obj)
    return [by_id[mid] for mid in order]


def _existing_record(workspace: Path, message_id: str) -> SidecarRecord | None:
    """Return the most recent sidecar row for `message_id`, or None."""
    path = _sidecar_path(workspace)
    if not path.exists():
        return None
    latest: SidecarRecord | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("id") == message_id:
            latest = SidecarRecord.from_json(obj)
    return latest


def _append_sidecar(workspace: Path, record: SidecarRecord) -> None:
    path = _sidecar_path(workspace)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.to_json(), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Per-message JSON write
# ---------------------------------------------------------------------------


def _relative_path_for(
    *,
    internal_date_utc: str,
    direction: str,
    from_header: str,
    subject: str,
    message_hash: str,
) -> str:
    """Build the `<YYYY>/<MM>/<DD>/<filename>.json` relative path."""
    try:
        date_part = internal_date_utc.split("T", 1)[0]
        year, month, day = date_part.split("-")
    except (ValueError, IndexError):
        today = dt.datetime.now(dt.UTC)
        year, month, day = (
            f"{today.year:04d}",
            f"{today.month:02d}",
            f"{today.day:02d}",
        )
    name = (
        f"{year}-{month}-{day}_{direction}_"
        f"{_from_slug(from_header)}_{_subject_slug(subject)}_"
        f"{message_hash[:8]}.json"
    )
    return f"{year}/{month}/{day}/{name}"


def _write_message_json(workspace: Path, relative_path: str, raw: dict[str, Any]) -> Path:
    target = _email_dir(workspace) / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(target, json.dumps(raw, ensure_ascii=False, indent=2))
    return target


# ---------------------------------------------------------------------------
# Direction inference + record assembly
# ---------------------------------------------------------------------------


def _infer_direction(
    raw: dict[str, Any], explicit: str | None = None
) -> str:
    if explicit in ("in", "out"):
        return explicit
    labels = list(raw.get("labelIds") or raw.get("label_ids") or [])
    return "out" if "SENT" in labels else "in"


def _build_record(
    *,
    raw: dict[str, Any],
    kind: str,
    direction: str,
    rel_path: str,
    captured_at: str,
    source_label: str,
) -> SidecarRecord:
    headers = _headers_map(raw)
    message_id = raw.get("id") or raw.get("messageId") or ""
    subject = headers.get("subject") or raw.get("subject") or ""
    from_header = headers.get("from") or raw.get("from") or ""
    to_header = headers.get("to") or raw.get("to") or ""
    cc_header = headers.get("cc") or raw.get("cc") or ""
    bcc_header = headers.get("bcc") or raw.get("bcc") or ""
    return SidecarRecord(
        id=message_id,
        thread_id=raw.get("threadId") or raw.get("thread_id") or "",
        path=rel_path,
        hash=_msg_hash(message_id),
        kind=kind,
        direction=direction,
        from_=from_header,
        to=_split_addresses(to_header),
        cc=_split_addresses(cc_header),
        bcc=_split_addresses(bcc_header),
        subject=subject,
        internal_date_utc=_internal_date_utc(
            raw.get("internalDate") or raw.get("internal_date")
        ),
        labels=list(raw.get("labelIds") or raw.get("label_ids") or []),
        snippet=raw.get("snippet") or "",
        has_attachments=_has_attachments(raw),
        attachments_saved=0,
        captured_at=captured_at,
        provenance={
            "source": source_label,
            "at": captured_at,
        },
    )


# ---------------------------------------------------------------------------
# Index counter bumps
# ---------------------------------------------------------------------------


def _bump_counts(
    workspace: Path,
    *,
    action: str,
    record: SidecarRecord,
    captured_at: str,
) -> None:
    index = _load_index(workspace)
    counts = index["counts"]
    if action == "created":
        counts["total"] += 1
        if record.kind == "full":
            counts["full"] += 1
        else:
            counts["stubs"] += 1
        if record.direction == "out":
            counts["outbound"] += 1
        else:
            counts["inbound"] += 1
    elif action == "upgraded":
        # stub -> full
        if counts["stubs"] > 0:
            counts["stubs"] -= 1
        counts["full"] += 1
    # "updated" and "noop" do not move totals.
    if index["first_capture"] is None:
        index["first_capture"] = captured_at
    index["last_capture"] = captured_at
    _save_index(workspace, index)


# ---------------------------------------------------------------------------
# Capture: inbound (read_email result)
# ---------------------------------------------------------------------------


def capture_inbound(
    raw_message: dict[str, Any],
    *,
    workspace: Path | str | None = None,
    direction: str | None = None,
    source: str = "read_email",
) -> CaptureResult:
    """Write a `kind=full` record for one inbound Gmail message.

    `raw_message` MUST be the Gmail API response dict (the shape returned
    by `users().messages().get(format='full')` and by the gongrzhe MCP's
    `read_email`). Direction is inferred from `labelIds` unless overridden.
    """
    if not isinstance(raw_message, dict):
        raise TypeError(
            "capture_inbound requires a dict (raw Gmail message), "
            f"got {type(raw_message).__name__}"
        )
    message_id = raw_message.get("id") or raw_message.get("messageId")
    if not message_id:
        raise ValueError("capture_inbound: raw_message lacks 'id'")

    ws = _resolve_workspace(workspace)
    _ensure_email_layout(ws)

    captured_at = _now_utc_iso()
    dir_ = _infer_direction(raw_message, direction)

    headers = _headers_map(raw_message)
    rel_path = _relative_path_for(
        internal_date_utc=_internal_date_utc(
            raw_message.get("internalDate") or raw_message.get("internal_date")
        ),
        direction=dir_,
        from_header=headers.get("from") or raw_message.get("from") or "",
        subject=headers.get("subject") or raw_message.get("subject") or "",
        message_hash=_msg_hash(message_id),
    )

    existing = _existing_record(ws, message_id)
    action: str
    if existing is None:
        _write_message_json(ws, rel_path, raw_message)
        action = "created"
    elif existing.kind == "stub":
        # Upgrade stub -> full; rewrite the JSON at the new path. Keep the
        # newer path even if the existing stub was at a different one (the
        # newer record is the source of truth from here on).
        _write_message_json(ws, rel_path, raw_message)
        action = "upgraded"
    else:
        # Already full. Re-write the JSON only if labels/snippet differ
        # enough to matter; for now do a cheap overwrite to keep the disk
        # copy current (read_email may carry updated labels). The sidecar
        # is sidecar-only updated for label changes via a fresh row.
        _write_message_json(ws, rel_path, raw_message)
        # Detect a meaningful change.
        new_labels = list(raw_message.get("labelIds") or [])
        new_snippet = raw_message.get("snippet") or ""
        action = (
            "updated"
            if (new_labels != existing.labels or new_snippet != existing.snippet)
            else "noop"
        )

    record = _build_record(
        raw=raw_message,
        kind="full",
        direction=dir_,
        rel_path=rel_path,
        captured_at=captured_at,
        source_label=source,
    )
    # Preserve attachments_saved across re-captures.
    if existing is not None:
        record = dc.replace(record, attachments_saved=existing.attachments_saved)

    if action != "noop":
        _append_sidecar(ws, record)
    _bump_counts(ws, action=action, record=record, captured_at=captured_at)

    return CaptureResult(
        record=record, action=action, path=_email_dir(ws) / rel_path
    )


# ---------------------------------------------------------------------------
# Capture: sent (send_email result)
# ---------------------------------------------------------------------------


def capture_sent(
    request: dict[str, Any],
    response: dict[str, Any],
    *,
    workspace: Path | str | None = None,
    source: str = "send_email",
    from_address: str = "",
) -> CaptureResult:
    """Write a `kind=full`, `direction=out` record for one sent message.

    `request` is the kwargs dict passed to `mcp_user-gmail_send_email`
    (to, subject, body, htmlBody, cc, bcc, threadId, inReplyTo, attachments).
    `response` is whatever the MCP returned (typically
    `{"messageId": "...", "threadId": "..."}` from the gongrzhe server,
    sometimes with more fields). The body of the email is taken from the
    request (Gmail's response does not include it).
    """
    if not isinstance(request, dict):
        raise TypeError(
            f"capture_sent requires a dict request, got {type(request).__name__}"
        )
    if not isinstance(response, dict):
        raise TypeError(
            f"capture_sent requires a dict response, got {type(response).__name__}"
        )
    message_id = (
        response.get("messageId")
        or response.get("id")
        or response.get("message_id")
    )
    if not message_id:
        raise ValueError(
            "capture_sent: response lacks 'messageId' / 'id'; cannot key the archive"
        )

    ws = _resolve_workspace(workspace)
    _ensure_email_layout(ws)
    sent_ms = int(dt.datetime.now(dt.UTC).timestamp() * 1000)

    body_text = request.get("body") or request.get("body_text") or ""
    body_html = request.get("htmlBody") or request.get("body_html") or ""
    snippet = (body_text or body_html)[:200].strip().replace("\n", " ")

    headers = [
        {"name": "From", "value": from_address or response.get("from", "") or ""},
        {"name": "To", "value": _join_addresses(request.get("to"))},
        {"name": "Cc", "value": _join_addresses(request.get("cc"))},
        {"name": "Bcc", "value": _join_addresses(request.get("bcc"))},
        {"name": "Subject", "value": request.get("subject", "")},
    ]
    for hkey, fkey in (("In-Reply-To", "inReplyTo"), ("References", "references")):
        if request.get(fkey):
            headers.append({"name": hkey, "value": str(request[fkey])})

    attachments_meta = [
        {"filename": Path(p).name, "source_path": str(p)}
        for p in (request.get("attachments") or [])
    ]

    synthesized_raw: dict[str, Any] = {
        "id": message_id,
        "threadId": response.get("threadId")
        or response.get("thread_id")
        or request.get("threadId")
        or "",
        "labelIds": ["SENT"],
        "internalDate": str(sent_ms),
        "snippet": snippet,
        "payload": {
            "headers": headers,
            "body": {
                "size": len(body_text.encode("utf-8")),
                "data": body_text,
            },
            "parts": attachments_meta,
        },
        "_send_request": {
            "body_text": body_text,
            "body_html": body_html,
            "to": request.get("to"),
            "cc": request.get("cc"),
            "bcc": request.get("bcc"),
            "subject": request.get("subject"),
            "threadId": request.get("threadId"),
            "inReplyTo": request.get("inReplyTo"),
            "attachments": list(request.get("attachments") or []),
        },
        "_send_response": dict(response),
    }
    return capture_inbound(
        synthesized_raw,
        workspace=ws,
        direction="out",
        source=source,
    )


def _join_addresses(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v)
    return str(value)


# ---------------------------------------------------------------------------
# Capture: stubs (search_emails result)
# ---------------------------------------------------------------------------


def maybe_capture_stubs(
    search_results: list[dict[str, Any]],
    *,
    workspace: Path | str | None = None,
    source: str = "search_emails",
) -> list[CaptureResult]:
    """Append `kind=stub` rows for any messages not yet in the archive.

    `search_results` is the list of metadata items the MCP returned. Each
    item must carry at least `id`; richer fields (subject, from, snippet,
    labelIds) are written through when present. Existing `full` records
    are never downgraded; existing `stub` records are left as-is unless
    visible fields changed.
    """
    if not isinstance(search_results, list):
        raise TypeError(
            f"maybe_capture_stubs requires list, got {type(search_results).__name__}"
        )
    ws = _resolve_workspace(workspace)
    _ensure_email_layout(ws)
    captured_at = _now_utc_iso()
    out: list[CaptureResult] = []
    for item in search_results:
        if not isinstance(item, dict):
            continue
        message_id = item.get("id") or item.get("messageId")
        if not message_id:
            continue
        existing = _existing_record(ws, message_id)
        if existing is not None and existing.kind == "full":
            continue
        direction = _infer_direction(item)
        headers = _headers_map(item)
        rel_path = _relative_path_for(
            internal_date_utc=_internal_date_utc(
                item.get("internalDate") or item.get("internal_date")
            ),
            direction=direction,
            from_header=headers.get("from") or item.get("from") or "",
            subject=headers.get("subject") or item.get("subject") or "",
            message_hash=_msg_hash(message_id),
        )
        record = _build_record(
            raw=item,
            kind="stub",
            direction=direction,
            rel_path=rel_path,
            captured_at=captured_at,
            source_label=source,
        )
        if existing is None:
            # Write a tiny stub JSON file alongside the sidecar row.
            stub_payload = dict(item)
            stub_payload.setdefault("id", message_id)
            _write_message_json(ws, rel_path, stub_payload)
            _append_sidecar(ws, record)
            _bump_counts(
                ws, action="created", record=record, captured_at=captured_at
            )
            out.append(CaptureResult(record=record, action="created", path=_email_dir(ws) / rel_path))
        else:
            # stub-on-stub: only refresh if visible fields changed.
            if (
                record.labels == existing.labels
                and record.snippet == existing.snippet
                and record.subject == existing.subject
            ):
                out.append(
                    CaptureResult(
                        record=existing, action="noop",
                        path=_email_dir(ws) / existing.path,
                    )
                )
                continue
            record = dc.replace(
                record, attachments_saved=existing.attachments_saved, path=existing.path
            )
            _append_sidecar(ws, record)
            _bump_counts(
                ws, action="updated", record=record, captured_at=captured_at
            )
            out.append(
                CaptureResult(record=record, action="updated", path=_email_dir(ws) / existing.path)
            )
    return out


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def receipt_heuristic(subject: str, sender: str) -> bool:
    """Return True if the (subject, sender) pair looks like a receipt.

    Combined check: subject regex OR known receipt-sender domain OR a
    receipt-sounding local-part. Conservative on purpose — agent judgment
    fills the long tail via the `reason` argument on `save_attachment`.
    """
    if subject and _RECEIPT_SUBJECT_RE.search(subject):
        return True
    if not sender:
        return False
    # Extract address.
    angle = sender.find("<")
    addr = sender[angle + 1 :].rstrip(">") if angle > 0 else sender
    addr = addr.strip()
    if "@" not in addr:
        return False
    local, _, domain = addr.partition("@")
    domain = domain.lower().strip()
    if any(domain == d or domain.endswith("." + d) for d in _RECEIPT_SENDER_DOMAINS):
        return True
    if local and _RECEIPT_LOCAL_RE.search(local):
        return True
    return False


def _safe_filename(name: str) -> str:
    """Reduce a filename to ASCII-letters/digits/`._-` only."""
    base = Path(name).name or "attachment.bin"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._-")
    return safe or "attachment.bin"


def save_attachment(
    message_id: str,
    attachment_id: str,
    filename: str,
    content_bytes: bytes,
    reason: str,
    *,
    workspace: Path | str | None = None,
) -> AttachmentResult:
    """Persist one attachment's bytes under `_memory/email/attachments/`.

    Content-hash dedup: identical bytes across messages reuse one file.
    Files larger than `MAX_ATTACHMENT_BYTES` are skipped with a note (the
    sidecar still records the attempted save). The sidecar's
    `attachments_saved` count for the message is incremented, and the
    per-message JSON's matching `payload.parts[*]` entry (matched by
    `attachmentId` or `filename`) gets a `saved_to: "attachments/..."`
    field. `reason` is recorded on the sidecar update for audit.
    """
    if not message_id:
        raise ValueError("save_attachment: message_id is required")
    if not isinstance(content_bytes, (bytes, bytearray)):
        raise TypeError(
            f"save_attachment: content_bytes must be bytes, got {type(content_bytes).__name__}"
        )
    ws = _resolve_workspace(workspace)
    _ensure_email_layout(ws)
    existing = _existing_record(ws, message_id)
    if existing is None:
        raise ValueError(
            f"save_attachment: no archived message with id={message_id!r}; "
            "call capture_inbound() / capture_sent() first"
        )
    size = len(content_bytes)
    sha256_full = hashlib.sha256(bytes(content_bytes)).hexdigest()
    sha8 = sha256_full[:8]
    if size > MAX_ATTACHMENT_BYTES:
        # Record the skip on the sidecar so the decision is auditable.
        _append_sidecar(
            ws,
            dc.replace(
                existing,
                captured_at=_now_utc_iso(),
                provenance={
                    **(existing.provenance or {}),
                    "attachment_skipped": {
                        "filename": filename,
                        "size": size,
                        "reason": "exceeds_25mb_cap",
                        "agent_reason": reason,
                    },
                },
            ),
        )
        return AttachmentResult(
            saved=False,
            path=None,
            sha256=sha256_full,
            reason=reason,
            note=f"skipped: {size} bytes exceeds {MAX_ATTACHMENT_BYTES}",
        )

    attach_dir = _attachments_dir(ws)
    attach_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    target_name = f"{sha8}_{safe}"
    target_path = attach_dir / target_name
    if not target_path.exists():
        tmp = target_path.with_suffix(target_path.suffix + ".tmp")
        tmp.write_bytes(bytes(content_bytes))
        os.replace(tmp, target_path)

    # Update the per-message JSON's matching part with saved_to.
    msg_path = _email_dir(ws) / existing.path
    if msg_path.exists():
        try:
            payload = json.loads(msg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            _mark_part_saved(
                payload,
                attachment_id=attachment_id,
                filename=filename,
                saved_to=f"attachments/{target_name}",
                reason=reason,
            )
            _atomic_write_text(
                msg_path, json.dumps(payload, ensure_ascii=False, indent=2)
            )

    # Append a fresh sidecar row with the bumped count.
    captured_at = _now_utc_iso()
    new_record = dc.replace(
        existing,
        attachments_saved=existing.attachments_saved + 1,
        has_attachments=True,
        captured_at=captured_at,
        provenance={
            **(existing.provenance or {}),
            "last_attachment_saved": {
                "filename": filename,
                "saved_to": f"attachments/{target_name}",
                "reason": reason,
                "at": captured_at,
            },
        },
    )
    _append_sidecar(ws, new_record)
    # Bump the global attachments_saved counter.
    index = _load_index(ws)
    index["counts"]["attachments_saved"] = index["counts"].get(
        "attachments_saved", 0
    ) + 1
    index["last_capture"] = captured_at
    _save_index(ws, index)
    return AttachmentResult(
        saved=True, path=target_path, sha256=sha256_full, reason=reason
    )


def _mark_part_saved(
    payload: dict[str, Any],
    *,
    attachment_id: str,
    filename: str,
    saved_to: str,
    reason: str,
) -> bool:
    """Walk `payload.parts` and tag the matching part with `saved_to`."""

    def _walk(node: dict[str, Any]) -> bool:
        body = node.get("body") or {}
        node_aid = body.get("attachmentId") or node.get("attachmentId")
        node_fn = node.get("filename") or ""
        if attachment_id and node_aid == attachment_id:
            node["saved_to"] = saved_to
            node["save_reason"] = reason
            return True
        if filename and node_fn == filename and "saved_to" not in node:
            node["saved_to"] = saved_to
            node["save_reason"] = reason
            return True
        return any(_walk(child) for child in node.get("parts") or [])

    top = payload.get("payload")
    if isinstance(top, dict):
        return _walk(top)
    return False


# ---------------------------------------------------------------------------
# Read API
# ---------------------------------------------------------------------------


def find(
    message_id: str, *, workspace: Path | str | None = None
) -> SidecarRecord | None:
    """Return the latest sidecar record for `message_id`, or None."""
    ws = _resolve_workspace(workspace)
    return _existing_record(ws, message_id)


def find_by_query(
    *,
    workspace: Path | str | None = None,
    from_substr: str | None = None,
    to_substr: str | None = None,
    subject_substr: str | None = None,
    thread_id: str | None = None,
    label: str | None = None,
    direction: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int | None = None,
) -> list[SidecarRecord]:
    """Linear scan of the sidecar with simple filters.

    All filters are AND-combined. Substring filters are case-insensitive.
    `since` / `until` are ISO-prefix compared against `internal_date_utc`
    (any prefix Python's lexical comparison handles: `2026`, `2026-05`,
    `2026-05-14T00:00:00+00:00`).
    """
    ws = _resolve_workspace(workspace)
    rows = _read_sidecar(ws)

    def _match(r: SidecarRecord) -> bool:
        if from_substr and from_substr.lower() not in r.from_.lower():
            return False
        if to_substr:
            joined = ", ".join(r.to + r.cc + r.bcc).lower()
            if to_substr.lower() not in joined:
                return False
        if subject_substr and subject_substr.lower() not in r.subject.lower():
            return False
        if thread_id and r.thread_id != thread_id:
            return False
        if label and label not in r.labels:
            return False
        if direction and r.direction != direction:
            return False
        if since and r.internal_date_utc < since:
            return False
        if until and r.internal_date_utc > until:
            return False
        return True

    matches = [r for r in rows if _match(r)]
    matches.sort(key=lambda r: r.internal_date_utc, reverse=True)
    if limit is not None:
        matches = matches[: max(0, int(limit))]
    return matches


def stats(*, workspace: Path | str | None = None) -> dict[str, Any]:
    """Return the live `_index.yaml` contents (or defaults if missing)."""
    ws = _resolve_workspace(workspace)
    return _load_index(ws)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_record(r: SidecarRecord) -> None:
    print(
        f"  {r.internal_date_utc}  [{r.kind:<4}/{r.direction:<3}]  "
        f"{r.from_[:40]:<40}  {r.subject[:60]}"
    )


def _cmd_find(args: argparse.Namespace) -> int:
    ws = _resolve_workspace(args.workspace)
    rec = find(args.message_id, workspace=ws)
    if rec is None:
        print(f"no record for id={args.message_id!r}", file=sys.stderr)
        return 1
    print(json.dumps(rec.to_json(), indent=2))
    full_path = _email_dir(ws) / rec.path
    print(f"json: {full_path}")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    rows = find_by_query(
        workspace=args.workspace,
        from_substr=args.from_,
        to_substr=args.to,
        subject_substr=args.subject,
        thread_id=args.thread,
        label=args.label,
        direction=args.direction,
        since=args.since,
        until=args.until,
        limit=args.limit,
    )
    if not rows:
        print("(no matches)")
        return 0
    print(f"{len(rows)} match(es):")
    for r in rows:
        _print_record(r)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    data = stats(workspace=args.workspace)
    counts = data.get("counts") or {}
    print(f"workspace: {_resolve_workspace(args.workspace)}")
    print(f"attachments_mode: {data.get('attachments')}")
    print(f"first_capture: {data.get('first_capture')}")
    print(f"last_capture:  {data.get('last_capture')}")
    print("counts:")
    for k in ("total", "full", "stubs", "inbound", "outbound", "attachments_saved"):
        print(f"  {k:<18} {counts.get(k, 0)}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="email-archive",
        description="Inspect the local email archive at workspace/_memory/email/.",
    )
    parser.add_argument(
        "--workspace", type=Path, default=None, help="Workspace path (defaults to ./workspace/)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_find = sub.add_parser("find", help="Print one record by Gmail message id.")
    p_find.add_argument("message_id")
    p_find.set_defaults(func=_cmd_find)

    p_query = sub.add_parser("query", help="List records matching simple filters.")
    p_query.add_argument("--from", dest="from_", default=None, help="Substring match on `from`.")
    p_query.add_argument("--to", default=None, help="Substring match on to/cc/bcc.")
    p_query.add_argument("--subject", default=None, help="Substring match on subject.")
    p_query.add_argument("--thread", default=None, help="Exact thread_id match.")
    p_query.add_argument("--label", default=None, help="Filter to records carrying this Gmail label.")
    p_query.add_argument(
        "--direction", choices=("in", "out"), default=None, help="Filter by direction."
    )
    p_query.add_argument("--since", default=None, help="ISO-prefix lower bound on internal_date_utc.")
    p_query.add_argument("--until", default=None, help="ISO-prefix upper bound on internal_date_utc.")
    p_query.add_argument("--limit", type=int, default=20)
    p_query.set_defaults(func=_cmd_query)

    p_stats = sub.add_parser("stats", help="Print the `_index.yaml` counters.")
    p_stats.set_defaults(func=_cmd_stats)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
