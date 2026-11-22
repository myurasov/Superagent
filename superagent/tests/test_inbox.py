# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Tests for `tools/inbox.py`: which documents wait in the drop folders.

The update cycle drains the drop folders, and the zero-cost check of the
update duty asks this tool whether anything waits. It must list a document
that is ready, leave out one that is still arriving, and never touch a file.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

import pytest

from superagent.tools import inbox

NOW = dt.datetime(2026, 11, 22, 9, 0, tzinfo=dt.timezone(dt.timedelta(hours=-8)))


def _drop(folder: Path, name: str, *, age_s: int = 600, content: bytes = b"x") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(content)
    stamp = NOW.timestamp() - age_s
    os.utime(path, (stamp, stamp))
    return path


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


def _set_drop_folders(ws: Path, folders: list[str]) -> None:
    config = ws / "_memory" / "config.yaml"
    text = config.read_text()
    assert "    drop_folders: []\n" in text, "the config template carries preferences.update.drop_folders"
    config.write_text(text.replace("    drop_folders: []\n", f"    drop_folders: {json.dumps(folders)}\n"))


def test_the_default_drop_folder_is_inbox(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    assert inbox.drop_folders(ws) == [ws / "Inbox"]
    assert inbox.waiting(ws, now=NOW) == [], "a fresh workspace has nothing waiting (its README is not a document)"
    _drop(ws / "Inbox", "scan.pdf")
    rows = inbox.waiting(ws, now=NOW)
    assert _names(rows) == ["scan.pdf"]
    assert rows[0] == {"path": str(ws / "Inbox" / "scan.pdf"), "folder": str(ws / "Inbox"),
                       "name": "scan.pdf", "kind": "file", "size": 1, "settled": True}


def test_what_is_not_a_waiting_document(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    box = ws / "Inbox"
    _drop(box, "bill.pdf")
    _drop(box, "arriving.pdf", age_s=5)
    _drop(box, ".DS_Store")
    _drop(box, ".syncthing.photo.jpg.tmp")
    _drop(box, "~syncthing~statement.pdf.tmp")
    _drop(box, "bill.sync-conflict-20261122-090000-ABCDEFG.pdf")
    (box / "tax-packet").mkdir()
    _drop(box / "tax-packet", "inner.pdf")
    assert _names(inbox.waiting(ws, now=NOW)) == ["bill.pdf", "tax-packet"], (
        "a folder counts as one item; nothing inside it is listed")
    everything = {r["name"]: r for r in inbox.scan(ws, now=NOW)}
    assert set(everything) == {"arriving.pdf", "bill.pdf",
                               "bill.sync-conflict-20261122-090000-ABCDEFG.pdf", "tax-packet"}
    assert everything["arriving.pdf"]["settled"] is False
    assert everything["bill.sync-conflict-20261122-090000-ABCDEFG.pdf"]["kind"] == "conflict-copy"
    assert everything["tax-packet"]["kind"] == "folder" and everything["tax-packet"]["size"] is None


def test_configured_drop_folders(initialized_workspace: Path, tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    ws = initialized_workspace
    home = tmp_path / "home"
    shared = home / "Sync" / "Inbox" / "Assistant"
    scanner = tmp_path / "scanner-out"
    monkeypatch.setenv("HOME", str(home))
    _set_drop_folders(ws, ["~/Sync/Inbox/Assistant", str(scanner), "Inbox", "Staging/drop", ""])
    assert inbox.drop_folders(ws) == [ws / "Inbox", shared, scanner, ws / "Staging" / "drop"], (
        "`~` expands, a relative path is taken from the workspace, and a repeat is dropped")
    assert inbox.waiting(ws, now=NOW) == [], "a folder that does not exist yet holds nothing"
    _drop(shared, "letter.pdf")
    _drop(scanner, "receipt.jpg")
    _drop(ws / "Inbox", "note.txt")
    assert _names(inbox.waiting(ws, now=NOW)) == ["note.txt", "letter.pdf", "receipt.jpg"]


def test_a_broken_config_still_lists_the_inbox(initialized_workspace: Path) -> None:
    ws = initialized_workspace
    (ws / "_memory" / "config.yaml").write_text("preferences: [unclosed\n")
    _drop(ws / "Inbox", "scan.pdf")
    assert inbox.drop_folders(ws) == [ws / "Inbox"]
    assert _names(inbox.waiting(ws, now=NOW)) == ["scan.pdf"]


def test_cli_lists_and_never_touches_a_file(initialized_workspace: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    ws = initialized_workspace
    box = ws / "Inbox"
    old = dt.datetime.now().timestamp() - 600
    for name in ("scan.pdf", "scan.sync-conflict-20261122-090000-ABCDEFG.pdf"):
        (box / name).write_bytes(b"x")
        os.utime(box / name, (old, old))
    (box / "arriving.pdf").write_bytes(b"x")
    before = sorted((p.name, p.stat().st_mtime, p.read_bytes()) for p in box.iterdir())
    assert inbox.main(["list", "--workspace", str(ws)]) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines() == [str(box / "scan.pdf")]
    assert "1 sync conflict copy(ies) in a drop folder" in captured.err
    assert inbox.main(["list", "--all", "--workspace", str(ws)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert f"{box / 'arriving.pdf'}  (still arriving)" in lines
    assert f"{box / 'scan.sync-conflict-20261122-090000-ABCDEFG.pdf'}  (conflict-copy)" in lines
    assert inbox.main(["list", "--json", "--workspace", str(ws)]) == 0
    assert [r["name"] for r in json.loads(capsys.readouterr().out)] == ["scan.pdf"]
    assert sorted((p.name, p.stat().st_mtime, p.read_bytes()) for p in box.iterdir()) == before
