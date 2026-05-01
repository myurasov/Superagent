"""Apple Reminders ingestor.

Reads Apple Reminders via the `rem` CLI (https://rem.sidv.dev/) and mirrors
each reminder into Superagent's `_memory/todo.yaml` as a P2 task with
`source: apple_reminders`.

One-way mirror. We never write back to Reminders unless a future version
sets `writes_upstream: true` for this source.

Probe: `which rem` AND `rem list --json --limit 1`.
Run: `rem list --json` filtered by `last_ingest`.

Idempotent: each Reminder's stable id (returned by `rem`) is stored in the
task row under `tags: ["rem:<id>"]` so re-runs detect already-mirrored items.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import yaml

from ._base import IngestorBase, ProbeResult, ProbeStatus, RunResult, now_iso


class AppleRemindersIngestor(IngestorBase):
    """Mirror Apple Reminders → todo.yaml."""

    source = "apple_reminders"
    kind = "cli"
    description = "Apple Reminders -> todo.yaml (one-way mirror)."

    def probe(self) -> ProbeResult:
        """Check that `rem` is installed and we have Reminders permission."""
        if not shutil.which("rem"):
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.NOT_DETECTED,
                detail="`rem` CLI not on PATH.",
                setup_hint="curl -fsSL https://rem.sidv.dev/install | bash",
            )
        try:
            result = subprocess.run(
                ["rem", "list", "--json", "--limit", "1"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.NEEDS_SETUP,
                detail=f"rem invocation failed: {exc}",
                setup_hint="Verify Reminders permission in System Settings > Privacy & Security > Reminders.",
            )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "permission" in stderr.lower() or "tcc" in stderr.lower():
                return ProbeResult(
                    source=self.source,
                    status=ProbeStatus.PERMISSION_DENIED,
                    detail=stderr[:200],
                    setup_hint="Grant Reminders access in System Settings > Privacy & Security > Reminders.",
                )
            return ProbeResult(
                source=self.source,
                status=ProbeStatus.NEEDS_SETUP,
                detail=stderr[:200],
                setup_hint="See `rem --help`.",
            )
        return ProbeResult(source=self.source, status=ProbeStatus.AVAILABLE)

    def run(self, config_row: dict[str, Any], dry_run: bool = False) -> RunResult:
        """Pull Reminders since `last_ingest`; mirror to todo.yaml as P2 tasks."""
        started = now_iso()
        t0 = time.time()
        result = RunResult(source=self.source, started_at=started, finished_at=started)
        try:
            cmd_result = subprocess.run(
                ["rem", "list", "--json"],
                capture_output=True, text=True, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            result.errors.append(f"rem invocation failed: {exc}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        if cmd_result.returncode != 0:
            result.errors.append(f"rem returned {cmd_result.returncode}: {cmd_result.stderr.strip()[:200]}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        try:
            reminders = json.loads(cmd_result.stdout)
        except json.JSONDecodeError as exc:
            result.errors.append(f"rem JSON parse failed: {exc}")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        if not isinstance(reminders, list):
            result.errors.append("rem JSON output is not a list")
            result.finished_at = now_iso()
            result.duration_ms = int((time.time() - t0) * 1000)
            return result
        result.items_pulled = len(reminders)
        max_items = int(config_row.get("max_items_per_run", 200))
        if len(reminders) > max_items:
            result.truncated = True
            reminders = reminders[:max_items]

        todo_path = self.workspace / "_memory" / "todo.yaml"
        todo = self._load_todo(todo_path)
        existing_rem_ids = {
            tag.split(":", 1)[1]
            for task in (todo.get("tasks") or [])
            if isinstance(task, dict)
            for tag in (task.get("tags") or [])
            if isinstance(tag, str) and tag.startswith("rem:")
        }
        inserted, skipped = 0, 0
        for reminder in reminders:
            if not isinstance(reminder, dict):
                continue
            rid = str(reminder.get("id") or reminder.get("uuid") or "")
            if not rid:
                continue
            if rid in existing_rem_ids:
                skipped += 1
                continue
            title = (reminder.get("title") or "").strip()
            if not title:
                continue
            done = bool(reminder.get("isCompleted") or reminder.get("completed"))
            due = reminder.get("dueDate") or reminder.get("due")
            new_id = self._next_task_id(todo)
            todo.setdefault("tasks", []).append({
                "id": new_id,
                "title": title,
                "description": reminder.get("notes") or "",
                "priority": "P2",
                "status": "done" if done else "open",
                "created": started,
                "due_date": due,
                "completed_date": started if done else None,
                "related_domain": None,
                "related_asset": None,
                "related_account": None,
                "related_appointment": None,
                "related_bill": None,
                "tags": [f"rem:{rid}"],
                "source": "apple_reminders",
            })
            inserted += 1
        result.items_inserted = inserted
        result.items_skipped = skipped
        result.destination_summary = {"todo": inserted}

        if not dry_run and inserted > 0:
            self._save_todo(todo_path, todo)
        elif dry_run:
            result.notes = f"dry-run; would have inserted {inserted} reminders"
        result.finished_at = now_iso()
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    def _load_todo(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {"schema_version": 1, "tasks": []}
        try:
            with path.open() as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            return {"schema_version": 1, "tasks": []}
        if not isinstance(data, dict):
            return {"schema_version": 1, "tasks": []}
        data.setdefault("tasks", [])
        return data

    def _save_todo(self, path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)

    def _next_task_id(self, todo: dict[str, Any]) -> str:
        today = dt.date.today().strftime("%Y%m%d")
        prefix = f"task-{today}-"
        existing = [
            t.get("id", "")
            for t in (todo.get("tasks") or [])
            if isinstance(t, dict) and isinstance(t.get("id"), str) and t["id"].startswith(prefix)
        ]
        n = 1 + max(
            (int(tid.rsplit("-", 1)[-1]) for tid in existing if tid.rsplit("-", 1)[-1].isdigit()),
            default=0,
        )
        return f"{prefix}{n:03d}"


def main() -> int:
    """CLI entry point for `python3 -m superagent.tools.ingest.apple_reminders`."""
    import argparse

    parser = argparse.ArgumentParser(prog="ingest-apple-reminders")
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    framework = Path(__file__).resolve().parents[2]
    workspace = args.workspace or framework.parent / "workspace"

    ingestor = AppleRemindersIngestor(workspace)
    probe = ingestor.probe()
    print(f"probe: {probe.status} — {probe.detail or 'OK'}")
    if probe.status != ProbeStatus.AVAILABLE:
        if probe.setup_hint:
            print(f"hint:  {probe.setup_hint}")
        return 3
    config_row = {"max_items_per_run": 200}
    result = ingestor.run(config_row, dry_run=args.dry_run)
    print(
        f"pulled={result.items_pulled} inserted={result.items_inserted} "
        f"skipped={result.items_skipped} errors={len(result.errors)} "
        f"duration_ms={result.duration_ms}"
    )
    if result.errors:
        for err in result.errors:
            print(f"  error: {err}")
    return 1 if result.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
