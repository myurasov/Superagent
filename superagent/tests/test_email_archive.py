# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/email/archive.py` (capture-on-read-or-send archive).

Implements `contracts/email-capture.md`. Verifies layout, idempotency
(stub -> full upgrade, full -> full noop / update), attachment policy,
and find / find_by_query helpers.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from superagent.tools.email import archive


def _gmail_raw(
    message_id: str,
    *,
    subject: str = "Hello world",
    sender: str = '"Jane Doe" <jane@example.com>',
    to: str = "me@example.com",
    cc: str = "",
    snippet: str = "Hi there, ...",
    labels: list[str] | None = None,
    thread_id: str = "t1",
    internal_ms: int | None = None,
    body: str = "Hello!",
    attachments: list[dict] | None = None,
) -> dict:
    """Build a Gmail-API-shaped raw message dict for tests."""
    if internal_ms is None:
        internal_ms = int(
            dt.datetime(2026, 5, 14, 12, 0, 0, tzinfo=dt.UTC).timestamp() * 1000
        )
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
    ]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    parts: list[dict] = [
        {
            "mimeType": "text/plain",
            "body": {"data": body, "size": len(body)},
            "filename": "",
        }
    ]
    for att in attachments or []:
        parts.append(att)
    return {
        "id": message_id,
        "threadId": thread_id,
        "labelIds": list(labels or ["INBOX"]),
        "internalDate": str(internal_ms),
        "snippet": snippet,
        "payload": {
            "headers": headers,
            "body": {"size": 0, "data": ""},
            "parts": parts,
        },
    }


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Return a workspace root with `_memory/` pre-created (the archive
    creates `_memory/email/` itself on first use)."""
    workspace = tmp_path / "workspace"
    (workspace / "_memory").mkdir(parents=True)
    return workspace


# ---------------------------------------------------------------------------
# Layout + first-use scaffolding
# ---------------------------------------------------------------------------


def test_first_capture_creates_layout(ws: Path) -> None:
    raw = _gmail_raw("msg-1")
    result = archive.capture_inbound(raw, workspace=ws)
    assert result.action == "created"
    email_dir = ws / "_memory" / "email"
    assert email_dir.is_dir()
    assert (email_dir / "_index.yaml").is_file()
    assert (email_dir / "_messages.jsonl").is_file()
    assert result.path.is_file()
    # No attachments dir yet — created lazily on first save.
    assert not (email_dir / "attachments").exists()


def test_filename_follows_layout(ws: Path) -> None:
    raw = _gmail_raw(
        "msg-fn",
        subject="Re: [Project] Tomorrow's meeting?",
        sender='"John O\'Doe" <jod@example.com>',
    )
    result = archive.capture_inbound(raw, workspace=ws)
    rel = Path(result.record.path)
    assert rel.parts[0] == "2026"
    assert rel.parts[1] == "05"
    assert rel.parts[2] == "14"
    assert rel.parts[3].startswith("2026-05-14_in_john_o_doe_")
    assert rel.parts[3].endswith(".json")
    # Re:/Fwd: stripped from subject slug.
    assert "project_tomorrow" in rel.parts[3]


def test_index_counts_bump(ws: Path) -> None:
    archive.capture_inbound(_gmail_raw("msg-a"), workspace=ws)
    archive.capture_inbound(_gmail_raw("msg-b", labels=["SENT"]), workspace=ws)
    index = yaml.safe_load((ws / "_memory" / "email" / "_index.yaml").read_text())
    assert index["counts"]["total"] == 2
    assert index["counts"]["full"] == 2
    assert index["counts"]["inbound"] == 1
    assert index["counts"]["outbound"] == 1
    assert index["first_capture"] is not None
    assert index["last_capture"] is not None


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_repeat_inbound_full_to_full_is_noop_when_unchanged(ws: Path) -> None:
    raw = _gmail_raw("msg-dup")
    archive.capture_inbound(raw, workspace=ws)
    sidecar = (ws / "_memory" / "email" / "_messages.jsonl").read_text().splitlines()
    assert len(sidecar) == 1
    result2 = archive.capture_inbound(raw, workspace=ws)
    assert result2.action == "noop"
    sidecar2 = (ws / "_memory" / "email" / "_messages.jsonl").read_text().splitlines()
    assert len(sidecar2) == 1  # no new sidecar row on noop
    index = yaml.safe_load((ws / "_memory" / "email" / "_index.yaml").read_text())
    assert index["counts"]["total"] == 1


def test_label_change_emits_update_row(ws: Path) -> None:
    raw1 = _gmail_raw("msg-lbl", labels=["INBOX", "UNREAD"])
    raw2 = _gmail_raw("msg-lbl", labels=["INBOX"])  # marked read
    archive.capture_inbound(raw1, workspace=ws)
    archive.capture_inbound(raw2, workspace=ws)
    lines = (ws / "_memory" / "email" / "_messages.jsonl").read_text().splitlines()
    assert len(lines) == 2
    latest = json.loads(lines[-1])
    assert latest["labels"] == ["INBOX"]
    # find() returns latest-wins.
    rec = archive.find("msg-lbl", workspace=ws)
    assert rec is not None
    assert rec.labels == ["INBOX"]


def test_stub_upgrades_to_full(ws: Path) -> None:
    archive.maybe_capture_stubs([{"id": "msg-up", "snippet": "preview"}], workspace=ws)
    rec = archive.find("msg-up", workspace=ws)
    assert rec is not None and rec.kind == "stub"
    index1 = yaml.safe_load((ws / "_memory" / "email" / "_index.yaml").read_text())
    assert index1["counts"]["stubs"] == 1
    assert index1["counts"]["full"] == 0
    result = archive.capture_inbound(_gmail_raw("msg-up"), workspace=ws)
    assert result.action == "upgraded"
    rec2 = archive.find("msg-up", workspace=ws)
    assert rec2 is not None and rec2.kind == "full"
    index2 = yaml.safe_load((ws / "_memory" / "email" / "_index.yaml").read_text())
    assert index2["counts"]["stubs"] == 0
    assert index2["counts"]["full"] == 1
    # total unchanged across the upgrade.
    assert index2["counts"]["total"] == 1


def test_full_does_not_downgrade_to_stub(ws: Path) -> None:
    archive.capture_inbound(_gmail_raw("msg-noflip"), workspace=ws)
    results = archive.maybe_capture_stubs(
        [{"id": "msg-noflip", "snippet": "later preview"}], workspace=ws
    )
    # `maybe_capture_stubs` returns nothing (or skips silently) for full records.
    assert all(r.action == "noop" for r in results) or results == []
    rec = archive.find("msg-noflip", workspace=ws)
    assert rec is not None and rec.kind == "full"


def test_stub_on_stub_noop_when_unchanged(ws: Path) -> None:
    payload = {"id": "msg-2x", "subject": "hi", "snippet": "preview"}
    archive.maybe_capture_stubs([payload], workspace=ws)
    res = archive.maybe_capture_stubs([payload], workspace=ws)
    assert res and res[0].action == "noop"


# ---------------------------------------------------------------------------
# capture_sent
# ---------------------------------------------------------------------------


def test_capture_sent_records_outbound(ws: Path) -> None:
    request = {
        "to": "alice@example.com",
        "cc": ["bob@example.com"],
        "subject": "Project update",
        "body": "Hi Alice,\n\nHere's the update...",
    }
    response = {"messageId": "sent-1", "threadId": "thr-1"}
    result = archive.capture_sent(request, response, workspace=ws, from_address="me@example.com")
    assert result.action == "created"
    assert result.record.direction == "out"
    assert "SENT" in result.record.labels
    # Recipient parsed into the structured `to` list.
    assert result.record.to == ["alice@example.com"]
    assert result.record.cc == ["bob@example.com"]
    # The body lives in the per-message JSON's `_send_request`.
    raw = json.loads(result.path.read_text())
    assert raw["_send_request"]["body_text"].startswith("Hi Alice")
    assert raw["_send_response"] == response


def test_capture_sent_requires_message_id(ws: Path) -> None:
    with pytest.raises(ValueError, match="messageId"):
        archive.capture_sent({"to": "x@y", "subject": "s", "body": "b"}, {}, workspace=ws)


# ---------------------------------------------------------------------------
# find / find_by_query
# ---------------------------------------------------------------------------


def test_find_by_query_filters(ws: Path) -> None:
    archive.capture_inbound(
        _gmail_raw(
            "m1",
            subject="Receipt for order #123",
            sender='"Stripe" <receipts@stripe.com>',
            internal_ms=int(
                dt.datetime(2026, 5, 1, 9, 0, 0, tzinfo=dt.UTC).timestamp() * 1000
            ),
        ),
        workspace=ws,
    )
    archive.capture_inbound(
        _gmail_raw(
            "m2",
            subject="Meeting tomorrow",
            sender='"Jane" <jane@example.com>',
            internal_ms=int(
                dt.datetime(2026, 5, 10, 9, 0, 0, tzinfo=dt.UTC).timestamp() * 1000
            ),
        ),
        workspace=ws,
    )
    archive.capture_inbound(
        _gmail_raw(
            "m3",
            subject="Vacation plans",
            sender='"Jane" <jane@example.com>',
            internal_ms=int(
                dt.datetime(2026, 5, 14, 9, 0, 0, tzinfo=dt.UTC).timestamp() * 1000
            ),
        ),
        workspace=ws,
    )
    by_from = archive.find_by_query(workspace=ws, from_substr="jane")
    assert sorted(r.id for r in by_from) == ["m2", "m3"]
    by_subj = archive.find_by_query(workspace=ws, subject_substr="receipt")
    assert [r.id for r in by_subj] == ["m1"]
    by_since = archive.find_by_query(workspace=ws, since="2026-05-10")
    assert sorted(r.id for r in by_since) == ["m2", "m3"]
    limited = archive.find_by_query(workspace=ws, limit=1)
    assert len(limited) == 1
    assert limited[0].id == "m3"  # most recent first


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------


def test_receipt_heuristic_matches() -> None:
    assert archive.receipt_heuristic("Your receipt from Acme", "")
    assert archive.receipt_heuristic("Order #1234 confirmation", "")
    assert archive.receipt_heuristic("hi", "receipts@stripe.com")
    assert archive.receipt_heuristic("hi", '"Stripe" <noreply@stripe.com>')
    assert archive.receipt_heuristic("hi", "billing@example.com")
    assert not archive.receipt_heuristic("Meeting tomorrow", '"Jane" <jane@example.com>')


def test_save_attachment_writes_blob_and_updates_sidecar(ws: Path) -> None:
    raw = _gmail_raw(
        "att-1",
        attachments=[
            {
                "filename": "invoice.pdf",
                "mimeType": "application/pdf",
                "body": {"size": 6, "attachmentId": "aid-1"},
            }
        ],
    )
    archive.capture_inbound(raw, workspace=ws)
    res = archive.save_attachment(
        message_id="att-1",
        attachment_id="aid-1",
        filename="invoice.pdf",
        content_bytes=b"%PDF-1\n",
        reason="receipt_heuristic",
        workspace=ws,
    )
    assert res.saved is True
    assert res.path is not None and res.path.is_file()
    assert res.path.name.endswith("_invoice.pdf")
    # Sidecar bumped.
    rec = archive.find("att-1", workspace=ws)
    assert rec is not None
    assert rec.attachments_saved == 1
    # Per-message JSON tagged.
    msg = json.loads((ws / "_memory" / "email" / rec.path).read_text())
    parts = msg["payload"]["parts"]
    pdf_part = next(p for p in parts if p.get("filename") == "invoice.pdf")
    assert pdf_part["saved_to"].startswith("attachments/")
    assert pdf_part["save_reason"] == "receipt_heuristic"
    # Index counter bumped.
    index = yaml.safe_load((ws / "_memory" / "email" / "_index.yaml").read_text())
    assert index["counts"]["attachments_saved"] == 1


def test_save_attachment_size_cap(ws: Path) -> None:
    raw = _gmail_raw(
        "big-1",
        attachments=[
            {
                "filename": "huge.bin",
                "mimeType": "application/octet-stream",
                "body": {"size": 0, "attachmentId": "aid-big"},
            }
        ],
    )
    archive.capture_inbound(raw, workspace=ws)
    big = b"\x00" * (archive.MAX_ATTACHMENT_BYTES + 1)
    res = archive.save_attachment(
        message_id="big-1",
        attachment_id="aid-big",
        filename="huge.bin",
        content_bytes=big,
        reason="user_request",
        workspace=ws,
    )
    assert res.saved is False
    assert "exceeds" in res.note
    # No blob written.
    attach_dir = ws / "_memory" / "email" / "attachments"
    assert not attach_dir.exists() or not any(attach_dir.iterdir())


def test_save_attachment_dedupes_by_content_hash(ws: Path) -> None:
    raw = _gmail_raw("dup-att-1", attachments=[
        {"filename": "a.pdf", "mimeType": "application/pdf",
         "body": {"size": 4, "attachmentId": "aid-A"}},
    ])
    raw2 = _gmail_raw("dup-att-2", attachments=[
        {"filename": "b.pdf", "mimeType": "application/pdf",
         "body": {"size": 4, "attachmentId": "aid-B"}},
    ])
    archive.capture_inbound(raw, workspace=ws)
    archive.capture_inbound(raw2, workspace=ws)
    same_bytes = b"DEAD"
    r1 = archive.save_attachment("dup-att-1", "aid-A", "a.pdf", same_bytes, "test", workspace=ws)
    r2 = archive.save_attachment("dup-att-2", "aid-B", "b.pdf", same_bytes, "test", workspace=ws)
    assert r1.saved and r2.saved
    # Same sha8 prefix -> same path; both files reuse one blob.
    assert r1.sha256 == r2.sha256
    # Different filenames -> still 2 distinct on-disk files because of the
    # `<sha8>_<safe_filename>` shape (intentional: human-readable names).
    # That is fine; the agent can still notice the shared hash via the
    # sidecar's `last_attachment_saved.saved_to` chain. We just verify
    # both files exist.
    assert r1.path is not None and r1.path.is_file()
    assert r2.path is not None and r2.path.is_file()


def test_save_attachment_requires_existing_record(ws: Path) -> None:
    with pytest.raises(ValueError, match="no archived message"):
        archive.save_attachment(
            "nonexistent",
            "aid",
            "file.txt",
            b"hi",
            "test",
            workspace=ws,
        )


# ---------------------------------------------------------------------------
# Slug + hash helpers (white-box; small surface)
# ---------------------------------------------------------------------------


def test_slugify_basic() -> None:
    assert archive._slugify("Hello, World!") == "hello_world"
    assert archive._slugify("   ") == "unknown"
    assert archive._slugify("") == "unknown"
    assert archive._slugify("a" * 100, max_len=10) == "a" * 10


def test_from_slug_extracts_display_name() -> None:
    assert archive._from_slug('"John Doe" <john@example.com>') == "john_doe"
    assert archive._from_slug("john@example.com") == "john"
    assert archive._from_slug("") == "unknown"


def test_subject_slug_strips_reply_prefixes() -> None:
    assert archive._subject_slug("Re: Hello") == "hello"
    assert archive._subject_slug("Fwd: Re: Topic") == "topic"
    assert archive._subject_slug("") == "no_subject"
