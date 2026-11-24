#!/usr/bin/env -S UV_PROJECT_ENVIRONMENT=.venv uv run python
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""The asks queue: what waits on the owner's call (`_memory/asks.yaml`).

A run with nobody present never asks a question in chat (contracts/autonomy.md
§ 5): nobody reads it, and a run that waits for an answer blocks everything
behind it. Such a run writes its question here instead and carries on. The
briefing shows the open rows under "Waiting on your call", and the owner's
answer is recorded on the row, where the agent that asked finds it at its next
run.

  add      append one ask and print its id. `--key` makes it idempotent: when
           an open ask with the same key exists, its id is printed and no row
           is added, so a finding that repeats every cycle asks once.
           `--blocking` marks an ask that stops the agent's work: it gets one
           card in the owner's Inbox at once, raised by the agent that asks
           (rules/autocode.md § 4). Tier `approve` or `prepare` only, never
           from an update run, and two open ones per agent at most.
  card     record the Inbox card raised for a blocking ask (`--inbox ID`, the
           card's id in the runtime). One card per ask.
  list     print asks, oldest first. `--open` keeps the rows still waiting
           (status `open` and not past `expires_at`); `--agent` narrows to one
           asker; `--count` prints `open=N`; `--json` prints whole rows.
  answer   record the owner's answer on an open ask (`--answer TEXT`), or close
           an ask that no longer needs one (`--withdraw REASON`). While agents
           run as sessions of their own, `tools/autocode.py answer` records the
           answer the same way and tells the agent that asked.
  expire   close every open ask whose `expires_at` has passed (status
           `expired`) and print them. An expired ask counts as no answer.

An ask is not an approval. It records that a decision is wanted. An approval
of an outward action is asked for at the moment of action and covers that one
action (contracts/autonomy.md § 4).

Rows are append-only: an existing row changes only its `inbox`, `status`,
`answer`, `closed_at`, `closed_by` and `notes`. Writes are text-level, parse-checked
before and after, and atomic (the writer of `tools/signals.py`). The free text
of a row (`question`, `context`, `answer`) passes through
`log_user_query.redact()` first: the file sits outside the sensitive tier.

Exit codes: 0 ok; 1 runtime error (corrupt file, unknown or already-closed id,
post-write check failed); 2 usage error (bad arguments, unknown agent).

CLI:
    uv run python -m superagent.tools.asks add --agent updater \\
        --question "The bank feed is set to manual capture. Pull it now?" \\
        [--context "..."] [--option yes --option skip] [--tier approve] \\
        [--key harvest:simplefin] [--related watch:simplefin] [--expires 7d]
    uv run python -m superagent.tools.asks add --agent project:kitchen --blocking \\
        --question "Send the signed quote to the contractor now?" --option send --option hold
    uv run python -m superagent.tools.asks card <ask-id> --inbox <card-id>
    uv run python -m superagent.tools.asks list --open [--agent updater] [--count | --json]
    uv run python -m superagent.tools.asks answer <ask-id> --answer "yes, pull it"
    uv run python -m superagent.tools.asks answer <ask-id> --withdraw "the feed was pulled by hand"
    uv run python -m superagent.tools.asks expire
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from superagent.tools import agent_roles as ar
from superagent.tools import machines, signals
from superagent.tools.log_user_query import redact
from superagent.tools.next_id import next_id

FRAMEWORK = Path(__file__).resolve().parent.parent
ASKS_FILE = "asks.yaml"
ASKS_KEY = "asks"
OPEN = "open"
CLOSED_STATUSES = ("answered", "expired", "withdrawn")
#: What the answer unlocks (contracts/autonomy.md § 1): an action the agent
#: then carries out, groundwork it hands over, or a decision that is the owner's.
TIERS = ("approve", "prepare", "owner")
DEFAULT_TIER = "approve"
OWNER = "owner"
SKELETON = "schema_version: 1\nlast_updated: null\n"
#: Row fields in template order.
FIELDS = ("id", "asked_at", "agent", "question", "context", "options", "tier", "blocking", "key",
          "related", "expires_at", "inbox", "status", "answer", "closed_at", "closed_by", "notes")
#: A blocking ask stops its agent's work until the owner answers, so it gets one
#: card in the owner's Inbox at once (rules/autocode.md § 4). With tier `owner`
#: nothing of the agent's waits on the answer.
BLOCKING_TIERS = ("approve", "prepare")
#: Open blocking asks one agent may have: each stands for a card in the Inbox.
MAX_BLOCKING = 2
#: Roles whose runs carry on and never stop for an answer (contracts/update-loop.md).
NEVER_BLOCKING_ROLES = ("updater",)
_DURATION_RE = re.compile(r"^(\d+)\s*([mhdw])$")
_DURATION_UNIT = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")
_CARD_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


# --------------------------------------------------------------------- helpers

def asks_path(workspace: Path) -> Path:
    """`<workspace>/_memory/asks.yaml`."""
    return workspace / "_memory" / ASKS_FILE


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(microsecond=0)


def _iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def parse_expiry(value: str | None, now: dt.datetime) -> str | None:
    """`expires_at` from `7d` / `36h` / `90m` / `2w` (from now) or an ISO date or datetime.

    None stays None (the ask never expires). Raises ValueError for anything
    else, and for a moment that is not in the future.
    """
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().lower()
    match = _DURATION_RE.match(text)
    if match:
        when = now + dt.timedelta(**{_DURATION_UNIT[match.group(2)]: int(match.group(1))})
    else:
        when = machines.parse_ts(str(value).strip())
        if when is None:
            raise ValueError(f"not a duration (90m, 36h, 7d, 2w) or an ISO date: {value!r}")
    if when <= now:
        raise ValueError(f"`expires` must lie in the future: {value!r}")
    return _iso(when)


def load_asks(workspace: Path) -> list[dict[str, Any]]:
    """Every real ask row (rows with an id; the template's example row is skipped)."""
    path = asks_path(workspace)
    if not path.exists():
        return []
    _text, _data, rows = signals._read(path, ASKS_KEY)  # noqa: SLF001 - the shared reader
    return [r for r in rows if isinstance(r, dict) and r.get("id")]


def is_waiting(row: dict[str, Any], now: dt.datetime) -> bool:
    """True for an ask still waiting on the owner: `open` and not past its `expires_at`."""
    if row.get("status") != OPEN:
        return False
    expires = machines.parse_ts(row.get("expires_at"))
    return expires is None or expires > now


def open_asks(workspace: Path, *, agent: str | None = None, now: dt.datetime | None = None
              ) -> list[dict[str, Any]]:
    """The asks still waiting, oldest first, optionally those of one agent."""
    now = now or _now()
    return [r for r in load_asks(workspace)
            if is_waiting(r, now) and (agent is None or r.get("agent") == agent)]


def blocking_asks(workspace: Path, agent: str, *, now: dt.datetime | None = None
                  ) -> list[dict[str, Any]]:
    """The blocking asks of one agent that still wait on the owner, oldest first."""
    return [r for r in open_asks(workspace, agent=agent, now=now) if r.get("blocking") is True]


def _check_agent(agent: str, workspace: Path) -> str:
    """Return the role of `agent`; raise ValueError unless it is an instance name of a known role."""
    try:
        role, _slug, _dev = ar.split_instance(agent)
    except ar.AgentError as exc:
        raise ValueError(str(exc)) from exc
    roles, _errors = ar.collect(FRAMEWORK, workspace)
    if role not in {r.name for r in roles}:
        known = ", ".join(r.name for r in roles) or "none"
        raise ValueError(f"no role named {role!r} (roles: {known})")
    return role


# ---------------------------------------------------------------------- writes

def add_ask(workspace: Path, *, agent: str, question: str, context: str | None = None,
            options: list[str] | None = None, tier: str = DEFAULT_TIER, key: str | None = None,
            related: list[str] | None = None, expires: str | None = None,
            blocking: bool = False, now: dt.datetime | None = None) -> tuple[str, bool]:
    """Append one ask; return (its id, True), or (the id of the open ask with the same key, False).

    `blocking` marks an ask the agent's work is stopped on: the agent raises
    one Inbox card for it at once and records the card with `record_card`.

    Raises ValueError for an unknown agent, an off-list tier, a bad key or
    expiry, an empty question, or a blocking ask that is not allowed (tier
    `owner`, a role whose runs carry on, the agent's limit reached);
    CorruptFileError / WriteRefusedError when the file cannot be written safely.
    """
    now = now or _now()
    if not question or not question.strip():
        raise ValueError("the question must not be empty")
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {', '.join(TIERS)}")
    if key is not None and not _KEY_RE.match(key):
        raise ValueError(f"key {key!r}: use letters, digits and `_ . : / -`, 120 characters at most")
    role = _check_agent(agent, workspace)
    if blocking and tier not in BLOCKING_TIERS:
        raise ValueError(f"a blocking ask has tier {' or '.join(BLOCKING_TIERS)}: with tier "
                         f"{tier!r} nothing of the agent's waits on the answer")
    if blocking and role in NEVER_BLOCKING_ROLES:
        raise ValueError(f"{agent} never adds a blocking ask: its run carries on and the "
                         "question waits for the briefing")
    expires_at = parse_expiry(expires, now)
    path = asks_path(workspace)
    signals._read(path, ASKS_KEY)  # noqa: SLF001 - refuse a broken file before minting
    if key is not None:
        for row in load_asks(workspace):
            if row.get("key") == key and is_waiting(row, now):
                return str(row["id"]), False
    if blocking:
        waiting = blocking_asks(workspace, agent, now=now)
        if len(waiting) >= MAX_BLOCKING:
            ids = ", ".join(str(r["id"]) for r in waiting)
            raise ValueError(f"{len(waiting)} blocking asks of {agent} wait on the owner already "
                             f"({ids}); add this one without --blocking, or withdraw one first")
    stamp = _iso(now)
    ask_id = next_id("ask", stamp[:10], path)
    row: dict[str, Any] = {
        "id": ask_id,
        "asked_at": stamp,
        "agent": agent,
        "question": redact(" ".join(question.split())),
        "context": redact(context.strip()) if context and context.strip() else None,
        "options": [str(o).strip() for o in (options or []) if str(o).strip()],
        "tier": tier,
        "blocking": bool(blocking),
        "key": key,
        "related": [str(h).strip() for h in (related or []) if str(h).strip()],
        "expires_at": expires_at,
        "inbox": None,
        "status": OPEN,
        "answer": None,
        "closed_at": None,
        "closed_by": None,
        "notes": None,
    }
    signals.append_row(path, ASKS_KEY, row, skeleton=SKELETON, last_updated=stamp)
    machines.beat(workspace, min_interval_s=60)
    return ask_id, True


def _close(workspace: Path, ask_id: str, *, status: str, answer: str | None, by: str,
           now: dt.datetime) -> dict[str, Any]:
    path = asks_path(workspace)
    _text, _data, rows = signals._read(path, ASKS_KEY)  # noqa: SLF001 - the shared reader
    hits = [i for i, r in enumerate(rows) if isinstance(r, dict) and r.get("id") == ask_id]
    if not hits:
        raise signals.WriteRefusedError(f"no ask with id {ask_id!r} in {ASKS_FILE}")
    if len(hits) > 1:
        raise signals.WriteRefusedError(f"{len(hits)} rows share id {ask_id!r}; fix the duplicate by "
                                        "hand first")
    old = rows[hits[0]]
    if old.get("status") != OPEN:
        raise signals.WriteRefusedError(f"{ask_id} is already {old.get('status')}; nothing changed")
    new = dict(old)
    new.update({"status": status, "answer": answer, "closed_at": _iso(now), "closed_by": by})
    signals.replace_row(path, ASKS_KEY, hits[0], new, last_updated=_iso(now))
    return new


def record_card(workspace: Path, ask_id: str, *, inbox: str,
                now: dt.datetime | None = None) -> dict[str, Any]:
    """Record the Inbox card raised for an open blocking ask (the card's id in the runtime).

    One card per ask: the row's `inbox` is written once. Raises ValueError for
    a bad card id, WriteRefusedError for an unknown or closed ask, an ask that
    is not blocking, or an ask that has a card already.
    """
    now = now or _now()
    card = str(inbox or "").strip()
    if not _CARD_RE.match(card):
        raise ValueError(f"card id {inbox!r}: use letters, digits and `_ . : -`, 120 characters at most")
    path = asks_path(workspace)
    _text, _data, rows = signals._read(path, ASKS_KEY)  # noqa: SLF001 - the shared reader
    hits = [i for i, r in enumerate(rows) if isinstance(r, dict) and r.get("id") == ask_id]
    if len(hits) != 1:
        raise signals.WriteRefusedError(f"{len(hits)} rows with id {ask_id!r} in {ASKS_FILE}")
    old = rows[hits[0]]
    if old.get("status") != OPEN:
        raise signals.WriteRefusedError(f"{ask_id} is already {old.get('status')}; nothing changed")
    if old.get("blocking") is not True:
        raise signals.WriteRefusedError(f"{ask_id} is not a blocking ask: only a blocking ask gets "
                                        "an Inbox card, and it waits for the briefing")
    if old.get("inbox"):
        raise signals.WriteRefusedError(f"{ask_id} has the card {old['inbox']} already: one card per ask")
    new = dict(old)
    new["inbox"] = card
    signals.replace_row(path, ASKS_KEY, hits[0], new, last_updated=_iso(now))
    machines.beat(workspace, min_interval_s=60)
    return new


def answer_ask(workspace: Path, ask_id: str, *, answer: str | None = None,
               withdraw: str | None = None, by: str | None = None,
               now: dt.datetime | None = None) -> dict[str, Any]:
    """Record the owner's `answer` on an open ask, or `withdraw` it with a reason; return the row.

    Exactly one of `answer` / `withdraw` must be given. `by` defaults to the
    owner for an answer and to the asking agent for a withdrawal.
    """
    given = [v for v in (answer, withdraw) if v and v.strip()]
    if len(given) != 1 or (answer and withdraw):
        raise ValueError("give exactly one of --answer / --withdraw, with text")
    now = now or _now()
    text = redact(given[0].strip())
    if withdraw:
        asker = next((r.get("agent") for r in load_asks(workspace) if r.get("id") == ask_id), None)
        row = _close(workspace, ask_id, status="withdrawn", answer=text, by=by or asker or OWNER, now=now)
    else:
        row = _close(workspace, ask_id, status="answered", answer=text, by=by or OWNER, now=now)
    machines.beat(workspace, min_interval_s=60)
    return row


def expire_asks(workspace: Path, *, now: dt.datetime | None = None) -> list[dict[str, Any]]:
    """Close every open ask whose `expires_at` has passed; return the rows closed."""
    now = now or _now()
    closed: list[dict[str, Any]] = []
    for row in load_asks(workspace):
        expires = machines.parse_ts(row.get("expires_at"))
        if row.get("status") == OPEN and expires is not None and expires <= now:
            closed.append(_close(workspace, str(row["id"]), status="expired", answer=None,
                                 by="expiry", now=now))
    if closed:
        machines.beat(workspace, min_interval_s=60)
    return closed


# ------------------------------------------------------------------------- CLI

def render_line(row: dict[str, Any], now: dt.datetime) -> str:
    """One ask on one line: id, asker, status, date asked, expiry, question, options."""
    status = row.get("status")
    if status == OPEN and not is_waiting(row, now):
        status = "open (past its expiry)"
    parts = [str(row.get("id")), str(row.get("agent")), str(status), str(row.get("asked_at") or "")[:10]]
    if row.get("expires_at") and row.get("status") == OPEN:
        parts.append(f"expires {str(row['expires_at'])[:10]}")
    if row.get("blocking") is True and row.get("status") == OPEN:
        parts.append(f"blocking, card {row['inbox']}" if row.get("inbox") else "blocking, no card yet")
    line = "  ".join(parts) + "  " + str(row.get("question") or "")
    if row.get("options"):
        line += "  [" + " / ".join(str(o) for o in row["options"]) + "]"
    if row.get("answer"):
        line += f"  -> {row['answer']}"
    return line


def _aware(value: str) -> dt.datetime:
    parsed = machines.parse_ts(value)
    if parsed is None:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 datetime: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    """The `asks` argument parser."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", type=Path, default=None,
                        help="Workspace path (default: workspace next to the framework).")
    common.add_argument("--now", type=_aware, default=None, help=argparse.SUPPRESS)
    parser = argparse.ArgumentParser(prog="asks", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", parents=[common], help="Append one ask; prints its id.")
    add.add_argument("--agent", required=True, help="Who asks: `updater`, `project:<slug>`, ...")
    add.add_argument("--question", required=True, help="One line the owner can answer.")
    add.add_argument("--context", default=None, help="What the owner needs to know to answer.")
    add.add_argument("--option", action="append", default=None, dest="options",
                     help="A suggested answer; repeatable.")
    add.add_argument("--tier", choices=TIERS, default=DEFAULT_TIER,
                     help="What the answer unlocks (default: approve).")
    add.add_argument("--key", default=None, help="One open ask per key: a repeat adds nothing.")
    add.add_argument("--related", action="append", default=None, metavar="HANDLE",
                     help="An operational handle the ask is about; repeatable.")
    add.add_argument("--expires", default=None, help="90m, 36h, 7d, 2w, or an ISO date; default never.")
    add.add_argument("--blocking", action="store_true",
                     help="Your work is stopped on this answer: raise one Inbox card for it at once "
                          f"(tier approve or prepare; {MAX_BLOCKING} open per agent at most).")

    lst = sub.add_parser("list", parents=[common], help="List asks (all, or --open).")
    lst.add_argument("--open", action="store_true", help="Only the asks still waiting.")
    lst.add_argument("--agent", default=None, help="Only the asks of this agent.")
    how = lst.add_mutually_exclusive_group()
    how.add_argument("--count", action="store_true", help="Print `open=N`.")
    how.add_argument("--json", action="store_true", help="Whole rows, as JSON.")

    ans = sub.add_parser("answer", parents=[common], help="Answer or withdraw one open ask.")
    ans.add_argument("id")
    what = ans.add_mutually_exclusive_group(required=True)
    what.add_argument("--answer", default=None, help="The owner's answer.")
    what.add_argument("--withdraw", default=None, metavar="REASON",
                      help="Close it without an answer: it is no longer needed.")
    ans.add_argument("--by", default=None, help="Who closed it (default: owner; the asker for --withdraw).")

    card = sub.add_parser("card", parents=[common],
                          help="Record the Inbox card raised for a blocking ask.")
    card.add_argument("id")
    card.add_argument("--inbox", required=True, metavar="CARD_ID",
                      help="The card's id in the runtime (an Autocode approval's id).")

    sub.add_parser("expire", parents=[common], help="Close the open asks past their `expires_at`.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    workspace: Path = args.workspace or FRAMEWORK.parent / "workspace"
    now = args.now or _now()
    try:
        if args.command == "add":
            ask_id, created = add_ask(workspace, agent=args.agent, question=args.question,
                                      context=args.context, options=args.options, tier=args.tier,
                                      key=args.key, related=args.related, expires=args.expires,
                                      blocking=args.blocking, now=now)
            print(ask_id)
            if not created:
                print(f"asks: an open ask with key {args.key!r} exists; nothing added", file=sys.stderr)
            elif args.blocking:
                print("asks: blocking. Where the runtime has an Inbox, raise one card for it now "
                      "(the question, why, the options; 24 hours at most), then record it: "
                      f"asks card {ask_id} --inbox <card id>  (rules/autocode.md § 4)", file=sys.stderr)
        elif args.command == "list":
            if args.count:
                print(f"open={len(open_asks(workspace, agent=args.agent, now=now))}")
                return 0
            rows = open_asks(workspace, agent=args.agent, now=now) if args.open else [
                r for r in load_asks(workspace) if args.agent is None or r.get("agent") == args.agent]
            if args.json:
                print(json.dumps(rows, indent=2, default=str, ensure_ascii=False))
            else:
                for row in rows:
                    print(render_line(row, now))
        elif args.command == "answer":
            row = answer_ask(workspace, args.id, answer=args.answer, withdraw=args.withdraw,
                             by=args.by, now=now)
            print(f"{args.id}: {row['status']}")
        elif args.command == "card":
            row = record_card(workspace, args.id, inbox=args.inbox, now=now)
            print(f"{args.id}: card {row['inbox']}")
        else:
            for row in expire_asks(workspace, now=now):
                print(render_line(row, now))
    except ValueError as exc:
        print(f"asks: {exc}", file=sys.stderr)
        return 2
    except (signals.CorruptFileError, signals.WriteRefusedError) as exc:
        print(f"asks: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
