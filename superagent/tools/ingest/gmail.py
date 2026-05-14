#!/usr/bin/env -S uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Gmail ingestor.

Pulls message **metadata** (id, threadId, subject, from, to, date, snippet,
labels) for messages within the configured recency window from configured
Gmail labels (default: INBOX + SENT). Stores results as monthly JSONL
shards under `workspace/_memory/_gmail/<YYYY-MM>.jsonl`. Idempotent: each
message id is unique within a shard; re-runs skip already-captured ids.

This is the MVP scope per `superagent/contracts/ingestion.md` and
`superagent/docs/data-sources.md` § gmail. Body fetch and downstream
classification (bills / subscriptions / contacts / domain history) are
deferred to follow-up skills that read the JSONL shards.

Architecture
------------
- **Headless ingest (this module):** talks to the Gmail API directly
  using OAuth tokens cached at `~/.gmail-mcp/credentials.json` by the
  gongrzhe Gmail MCP. The same tokens; the API is just a different
  transport. This avoids requiring a Cursor-managed MCP server during
  cron-like runs.
- **Chat-time ad-hoc (separate):** the gongrzhe MCP server (configured
  in `~/.cursor/mcp.json` as `gmail`) handles interactive queries from
  the agent during a Cursor chat. Both share the same OAuth grant.

Read-only at the SKILL layer
----------------------------
The OS-granted scopes are wider (`gmail.modify` + `gmail.settings.basic`)
because no maintained Gmail MCP supports a true read-only mode. This
ingestor MUST only call `users().messages().list()` and
`users().messages().get()` -- never `send`, `modify`, `trash`, `delete`,
`batchModify`. The framework's "no remote write" rule
(`AGENTS.md` § "Privacy and data location") enforces read-only at the
skill level. Flipping to actual upstream writes requires setting
`writes_upstream: true` on the row in `data-sources.yaml` and explicit
audit logging per call.

Probe
-----
Loads OAuth credentials and calls `users().getProfile(userId='me')`.
Cheap, exercises the auth path, returns the connected mailbox address.

Run
---
1. Load credentials, refresh in-memory if expired.
2. For each label in `config_row.labels` (default `["INBOX", "SENT"]`):
   page through `messages().list()` with `q="newer_than:<recency_window_days>d"`,
   collect ids up to `max_items_per_run`.
3. For each new id (not already in any monthly shard), call
   `messages().get(format='metadata', metadataHeaders=['Subject','From','To','Date','Cc'])`
   and append a normalized row to the right monthly shard.
4. Write `workspace/_memory/_gmail/<YYYY-MM>.jsonl` (append-only).

Output schema (one JSON object per line in each shard)::

    {
      "id": "<message-id>",
      "thread_id": "<thread-id>",
      "label_ids": ["INBOX", "UNREAD", ...],
      "internal_date": "<RFC3339>",       # from internalDate (epoch ms -> ISO)
      "subject": "<subject>",
      "from": "<from>",
      "to": "<to>",
      "cc": "<cc>",                       # may be empty
      "snippet": "<gmail snippet>",
      "size_estimate": <int>,
      "captured_at": "<RFC3339>"          # when the ingestor saw it
    }

CLI
---
    uv run python -m superagent.tools.ingest.gmail [--workspace PATH] [--dry-run] [--probe]

Exit codes
----------
    0  success
    1  one or more API errors (still wrote what it could)
    3  probe failed (auth missing / scope insufficient / API unreachable)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from ._base import IngestorBase, ProbeResult, ProbeStatus, RunResult, now_iso

# Default credential paths (where the gongrzhe MCP stores tokens).
DEFAULT_CREDENTIALS_PATH = Path.home() / ".gmail-mcp" / "credentials.json"
DEFAULT_OAUTH_KEYS_PATH = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"

# Gmail API scopes the gongrzhe MCP requests at auth time. We declare them
# here only so the Credentials object knows what was granted; we never
# request more than was granted (the auth is already complete).
KNOWN_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
)


class GmailIngestor(IngestorBase):
    """Pull Gmail message metadata into JSONL shards under `_memory/_gmail/`."""

    source = "gmail"
    kind = "api"
    description = "Gmail message metadata -> _memory/_gmail/<YYYY-MM>.jsonl (read-only)."

    def __init__(
        self,
        workspace: Path,
        credentials_path: Path | None = None,
        oauth_keys_path: Path | None = None,
    ) -> None:
        super().__init__(workspace)
        self.credentials_path = credentials_path or DEFAULT_CREDENTIALS_PATH
        self.oauth_keys_path = oauth_keys_path or DEFAULT_OAUTH_KEYS_PATH
        self._service = None  # lazy

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _load_credentials(self):  # noqa: ANN202 (return type is googleapiclient internal)
        """Build a `google.oauth2.credentials.Credentials` from the MCP files.

        Raises FileNotFoundError if either file is missing; the caller turns
        that into a NEEDS_SETUP probe result.
        """
        from google.oauth2.credentials import Credentials

        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"OAuth tokens not found at {self.credentials_path}. "
                "Run `npx -y @gongrzhe/server-gmail-autoauth-mcp auth` first."
            )
        if not self.oauth_keys_path.exists():
            raise FileNotFoundError(
                f"OAuth client secrets not found at {self.oauth_keys_path}."
            )
        tokens = json.loads(self.credentials_path.read_text(encoding="utf-8"))
        keys_envelope = json.loads(self.oauth_keys_path.read_text(encoding="utf-8"))
        # OAuth client JSON wraps the actual fields in either `installed`
        # (Desktop app) or `web` (Web application). Handle both.
        client = keys_envelope.get("installed") or keys_envelope.get("web") or {}
        scope_str = tokens.get("scope") or " ".join(KNOWN_SCOPES)
        return Credentials(
            token=tokens.get("access_token"),
            refresh_token=tokens.get("refresh_token"),
            token_uri=client.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=client.get("client_id"),
            client_secret=client.get("client_secret"),
            scopes=scope_str.split(),
        )

    def _service_lazy(self):  # noqa: ANN202
        """Build (or return cached) Gmail API service. Refreshes the token if expired."""
        if self._service is not None:
            return self._service
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds = self._load_credentials()
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        # cache_discovery=False avoids a pointless filesystem cache that
        # google-api-python-client warns about under recent oauth2client versions.
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return self._service

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self) -> ProbeResult:
        """Verify auth + connectivity by calling `users().getProfile()`."""
        try:
            service = self._service_lazy()
        except FileNotFoundError as exc:
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.NEEDS_SETUP,
                detail=str(exc),
                setup_hint="See superagent/docs/data-sources.md § gmail.",
            )
        except Exception as exc:  # auth library raises a few unrelated kinds
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.AUTH_EXPIRED,
                detail=f"credentials load failed: {exc}",
                setup_hint=(
                    "Re-auth: `npx -y @gongrzhe/server-gmail-autoauth-mcp auth`"
                ),
            )
        try:
            profile = service.users().getProfile(userId="me").execute()
        except Exception as exc:
            from googleapiclient.errors import HttpError

            if isinstance(exc, HttpError) and exc.resp.status in (401, 403):
                return ProbeResult(
                    source=self.source,
                    status=ProbeStatus.PERMISSION_DENIED,
                    detail=f"Gmail API rejected with HTTP {exc.resp.status}",
                    setup_hint="Re-auth with the right scopes.",
                )
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.AUTH_EXPIRED,
                detail=f"getProfile failed: {exc}",
                setup_hint="Network issue or auth needs refresh.",
            )
        return ProbeResult(
            source=self.source,
            status=ProbeStatus.AVAILABLE,
            detail=f"connected as {profile.get('emailAddress', '<unknown>')}",
        )

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        """Pull message metadata for the configured window + labels."""
        started = now_iso()
        t0 = time.time()
        result = RunResult(source=self.source, started_at=started, finished_at=started)

        labels: list[str] = list(config_row.get("labels") or ["INBOX", "SENT"])
        window_days: int = int(config_row.get("recency_window_days", 30))
        max_items: int = int(config_row.get("max_items_per_run", 200))

        try:
            service = self._service_lazy()
        except Exception as exc:
            result.errors.append(f"auth: {exc}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result

        out_dir = self.workspace / "_memory" / "_gmail"
        if not dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
        existing_ids = self._load_existing_ids(out_dir)

        # 1) Page IDs across labels.
        candidate_ids: list[str] = []
        seen: set[str] = set()
        for label in labels:
            try:
                ids = self._list_message_ids(
                    service,
                    label=label,
                    window_days=window_days,
                    cap=max_items - len(candidate_ids),
                )
            except Exception as exc:
                result.errors.append(f"list({label}): {exc}")
                continue
            for mid in ids:
                if mid in seen:
                    continue
                seen.add(mid)
                candidate_ids.append(mid)
                if len(candidate_ids) >= max_items:
                    break
            if len(candidate_ids) >= max_items:
                result.truncated = True
                break

        result.items_pulled = len(candidate_ids)
        new_ids = [mid for mid in candidate_ids if mid not in existing_ids]
        result.items_skipped = len(candidate_ids) - len(new_ids)

        # 2) Fetch metadata for new ids.
        rows_by_shard: dict[str, list[dict]] = {}
        for mid in new_ids:
            try:
                row = self._fetch_message_metadata(service, mid)
            except Exception as exc:
                result.errors.append(f"get({mid}): {exc}")
                continue
            shard_key = self._shard_for(row["internal_date"])
            rows_by_shard.setdefault(shard_key, []).append(row)

        result.items_inserted = sum(len(v) for v in rows_by_shard.values())
        result.destination_summary = {
            f"_memory/_gmail/{shard}.jsonl": len(rows)
            for shard, rows in sorted(rows_by_shard.items())
        }

        # 3) Persist.
        if not dry_run and rows_by_shard:
            for shard_key, rows in rows_by_shard.items():
                self._append_shard(out_dir / f"{shard_key}.jsonl", rows)
        elif dry_run:
            result.notes = (
                f"dry-run; would have appended {result.items_inserted} message(s) "
                f"across {len(rows_by_shard)} shard(s)"
            )

        result.finished_at = now_iso()
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _list_message_ids(
        self, service: Any, label: str, window_days: int, cap: int,
    ) -> list[str]:
        """Page through `users().messages().list()` for one label."""
        if cap <= 0:
            return []
        ids: list[str] = []
        page_token = None
        q = f"newer_than:{window_days}d"
        while len(ids) < cap:
            resp = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=[label],
                    q=q,
                    maxResults=min(100, cap - len(ids)),
                    pageToken=page_token,
                )
                .execute()
            )
            for m in resp.get("messages") or []:
                ids.append(m["id"])
                if len(ids) >= cap:
                    break
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def _fetch_message_metadata(self, service: Any, message_id: str) -> dict[str, Any]:
        """Get one message in 'metadata' format -- headers only, no body."""
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "To", "Cc", "Date"],
            )
            .execute()
        )
        headers = {
            h["name"].lower(): h.get("value", "")
            for h in (msg.get("payload", {}).get("headers") or [])
        }
        # internalDate is epoch milliseconds (string).
        try:
            ts_ms = int(msg.get("internalDate", "0"))
            internal = (
                dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.UTC).isoformat()
            )
        except (ValueError, TypeError):
            internal = ""
        return {
            "id": msg.get("id", message_id),
            "thread_id": msg.get("threadId", ""),
            "label_ids": list(msg.get("labelIds") or []),
            "internal_date": internal,
            "subject": headers.get("subject", ""),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "cc": headers.get("cc", ""),
            "snippet": msg.get("snippet", ""),
            "size_estimate": int(msg.get("sizeEstimate", 0)),
            "captured_at": now_iso(),
        }

    @staticmethod
    def _shard_for(iso_date: str) -> str:
        """Return `YYYY-MM` for a given ISO 8601 date string. Falls back to current month."""
        if iso_date and len(iso_date) >= 7:
            return iso_date[:7]
        return dt.datetime.now(tz=dt.UTC).strftime("%Y-%m")

    @staticmethod
    def _load_existing_ids(out_dir: Path) -> set[str]:
        """Read every shard once and return the union of message ids already captured."""
        if not out_dir.exists():
            return set()
        ids: set[str] = set()
        for shard in sorted(out_dir.glob("*.jsonl")):
            try:
                for line in shard.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        ids.add(json.loads(line)["id"])
                    except (json.JSONDecodeError, KeyError):
                        continue
            except OSError:
                continue
        return ids

    @staticmethod
    def _append_shard(path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    """CLI: `uv run python -m superagent.tools.ingest.gmail [--workspace PATH] [--dry-run] [--probe]`."""
    parser = argparse.ArgumentParser(prog="ingest-gmail")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--probe", action="store_true", help="Just probe and exit (no ingest).",
    )
    parser.add_argument(
        "--credentials", type=Path, default=None,
        help=f"Override credentials path (default: {DEFAULT_CREDENTIALS_PATH}).",
    )
    parser.add_argument(
        "--oauth-keys", type=Path, default=None,
        help=f"Override OAuth keys path (default: {DEFAULT_OAUTH_KEYS_PATH}).",
    )
    args = parser.parse_args()

    framework = Path(__file__).resolve().parents[2]
    workspace = args.workspace or framework.parent / "workspace"

    ingestor = GmailIngestor(
        workspace,
        credentials_path=args.credentials,
        oauth_keys_path=args.oauth_keys,
    )
    probe = ingestor.probe()
    print(f"probe: {probe.status} -- {probe.detail or 'OK'}")
    if probe.status != ProbeStatus.AVAILABLE:
        if probe.setup_hint:
            print(f"hint:  {probe.setup_hint}")
        return 3
    if args.probe:
        return 0

    config_row = {"max_items_per_run": 200, "recency_window_days": 30}
    result = ingestor.run(config_row, dry_run=args.dry_run)
    print(
        f"pulled={result.items_pulled} inserted={result.items_inserted} "
        f"skipped={result.items_skipped} errors={len(result.errors)} "
        f"truncated={result.truncated} duration_ms={result.duration_ms}"
    )
    if result.destination_summary:
        for path, n in result.destination_summary.items():
            print(f"  -> {path}: {n} row(s)")
    if result.notes:
        print(f"notes: {result.notes}")
    if result.errors:
        for err in result.errors:
            print(f"  error: {err}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
