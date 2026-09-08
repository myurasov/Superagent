# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Gmail pack handler — the `gmail` detect type (contract § 5), checked LIVE.

Loaded by file path by `tools/watchlist.py`; exposes `detect(ctx)`. One
bounded `messages.list` per check with the OAuth token the Gmail MCP saved at
`~/.gmail-mcp/credentials.json`, then at most `MAX_GETS` `messages.get`
(metadata) calls for the newest ids the local email archive does not know
yet; those pass through `archive.maybe_capture_stubs` so a watch check also
grows the archive (`contracts/email-capture.md`). Fingerprint = newest
message id + newest `internalDate`. The result count is reported as detail
only: with a `newer_than:` bound it shrinks as mail ages out of the window,
which is not a change.

Read-only at the API layer: only `list` and `get` are ever called — never
send / modify / trash / delete (AGENTS.md "no remote write").

Token absent -> `DetectError` with the setup hint (-> `unreachable`).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from superagent.tools.ingest._base import DetectContext, DetectError, DetectResult

LIST_MAX = 100
MAX_GETS = 10
DEFAULT_CREDENTIALS_PATH = Path.home() / ".gmail-mcp" / "credentials.json"
DEFAULT_OAUTH_KEYS_PATH = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"
# Scopes the Gmail MCP requests at auth time; declared so the Credentials
# object knows what was granted. Never request more than was granted.
KNOWN_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
)
SETUP_HINT = (
    "Authorize the Gmail MCP once (`npx -y @gongrzhe/server-gmail-autoauth-mcp auth`); "
    "the watcher reuses its token at ~/.gmail-mcp/credentials.json."
)


class AuthMissing(FileNotFoundError):
    """The Gmail MCP has not saved an OAuth token yet."""


class GmailClient:
    """Thin read-only wrapper over the Gmail API using the MCP's saved token."""

    def __init__(self, credentials_path: Path | None = None, oauth_keys_path: Path | None = None):
        self.credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self.oauth_keys_path = oauth_keys_path or DEFAULT_OAUTH_KEYS_PATH
        self._service = None

    def _load_credentials(self):  # noqa: ANN202 — google-auth internal type
        from google.oauth2.credentials import Credentials

        if not self.credentials_path.exists():
            raise AuthMissing(f"OAuth token not found at {self.credentials_path}.")
        if not self.oauth_keys_path.exists():
            raise AuthMissing(f"OAuth client secrets not found at {self.oauth_keys_path}.")
        tokens = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        envelope = json.loads(self.oauth_keys_path.read_text(encoding="utf-8"))
        client = envelope.get("installed") or envelope.get("web") or {}
        scope_str = tokens.get("scope") or " ".join(KNOWN_SCOPES)
        return Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri=client.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=client.get("client_id"),
            client_secret=client.get("client_secret"),
            scopes=scope_str.split(),
        )

    def service(self):  # noqa: ANN202 — googleapiclient resource
        if self._service is not None:
            return self._service
        from google.auth.transport.requests import Request as GoogleRequest
        from googleapiclient.discovery import build

        creds = self._load_credentials()
        if not creds.valid and creds.refresh_token:
            creds.refresh(GoogleRequest())
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    def list_messages(self, query: str, max_results: int = LIST_MAX) -> list[dict[str, Any]]:
        """One `messages.list` call; newest first as Gmail orders them."""
        resp = (
            self.service().users().messages()
            .list(userId="me", q=query, maxResults=max_results)
            .execute()
        )
        return list(resp.get("messages") or [])

    def get_metadata(self, message_id: str) -> dict[str, Any]:
        """`messages.get(format=metadata)` — headers only, shaped for the archive."""
        return (
            self.service().users().messages()
            .get(userId="me", id=message_id, format="metadata",
                 metadataHeaders=["Subject", "From", "To", "Cc", "Date"])
            .execute()
        )


def default_client_factory() -> GmailClient:
    """Build the live client; raises AuthMissing when the MCP token is absent."""
    client = GmailClient(DEFAULT_CREDENTIALS_PATH, DEFAULT_OAUTH_KEYS_PATH)
    if not client.credentials_path.exists():
        raise AuthMissing(f"OAuth token not found at {client.credentials_path}.")
    return client


#: Swapped by tests (and by any caller that wants a fake API client).
CLIENT_FACTORY: Callable[[], Any] = default_client_factory


def detect(ctx: DetectContext) -> DetectResult:
    """The `gmail` detect: one bounded live search, capture-through, fingerprint."""
    query = str(ctx.detect.get("query") or "").strip()
    if not query:
        raise DetectError("gmail watcher has no query (set params.query)")
    try:
        client = CLIENT_FACTORY()
    except AuthMissing as exc:
        raise DetectError(f"{exc} {SETUP_HINT}") from exc
    except Exception as exc:  # noqa: BLE001 — auth libraries raise assorted kinds
        raise DetectError(f"gmail auth failed: {exc}") from exc
    try:
        msgs = client.list_messages(query, LIST_MAX)
    except Exception as exc:  # noqa: BLE001
        raise DetectError(f"gmail list failed: {exc}") from exc
    count = len(msgs)
    if not msgs:
        return DetectResult("gmail:none", f"0 matches for {query!r}")

    from superagent.tools.email import archive

    known: dict[str, Any] = {}
    if (ctx.workspace / "_memory").exists():
        try:
            known = {r.id: r for r in archive._read_sidecar(ctx.workspace)}
        except Exception:  # noqa: BLE001 — a torn sidecar must not fail detect
            known = {}
    new_ids = [m["id"] for m in msgs if m.get("id") and m["id"] not in known][:MAX_GETS]
    fetched: list[dict[str, Any]] = []
    for mid in new_ids:
        try:
            fetched.append(client.get_metadata(mid))
        except Exception as exc:  # noqa: BLE001
            raise DetectError(f"gmail get({mid}) failed: {exc}") from exc
    # Newest message's date in the archive's ISO-UTC form, whether it was fetched
    # this run (raw epoch-ms `internalDate`) or is already a sidecar record —
    # the two must fingerprint identically or every re-check reads as a change.
    newest_id = str(msgs[0].get("id") or "")
    newest_internal: str | None = None
    for raw in fetched:
        if str(raw.get("id")) == newest_id:
            newest_internal = archive._internal_date_utc(raw.get("internalDate"))
            break
    if newest_internal is None and newest_id in known:
        newest_internal = getattr(known[newest_id], "internal_date_utc", None)
    if not newest_internal:
        newest_internal = "?"
    fingerprint = f"gmail:{newest_id}:{newest_internal}"
    captured = 0
    if fetched and not ctx.dry_run and (ctx.workspace / "_memory").exists():
        try:
            results = archive.maybe_capture_stubs(fetched, workspace=ctx.workspace, source="watchlist")
            captured = sum(1 for r in results if r.action in ("created", "updated"))
        except Exception as exc:  # noqa: BLE001 — capture is a side benefit, never the check
            return DetectResult(fingerprint, f"{count} match(es); capture-through failed: {exc}")
    return DetectResult(
        fingerprint,
        f"{count} match(es); {len(fetched)} new id(s) fetched; {captured} captured",
    )
