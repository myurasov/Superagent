# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Operational-handle parsing and formatting.

Implements superagent/docs/_internal/ideas-better-structure.md item #20 — every entity gets a
canonical `<kind>:<slug>` operational handle. This module is the single
canonical parser / formatter so every skill and tool agrees on the shape.

Examples:
    contact:dr-smith-dentist
    bill:pge-electric
    appointment:20260512-dr-smith-cleaning
    project:tax-2026
    domain:health
    asset:car-blue-camry-2018
    watch:solar-permit           (a Sources/Watchlist/<id>.ref.md watcher)
    agent:updater                (an agent, per contracts/agents.md)
    agent:project:tax-2026       (a project agent: the slug is its instance name)

The colon separates the entity kind (the type of thing) from the entity
slug (the unique-within-kind identifier). Kinds are lowercase singular
nouns; slugs are lowercase, hyphen-separated.

Back-compat: some legacy ids carry their kind as a prefix already
(e.g. "contact-dr-smith-dentist", "bill-pge-electric"). The parser
detects this and folds them into the canonical form. Skills should
write the canonical form going forward.
"""
from __future__ import annotations

import dataclasses as dc
import re
from collections.abc import Iterable

# Canonical entity kinds (singular). Keep in sync with the world graph
# node-kind taxonomy in templates/memory/world.yaml.
KINDS = frozenset({
    "contact", "account", "asset", "bill", "subscription",
    "appointment", "important_date", "document", "domain", "project",
    "source", "medication", "vital", "task", "health_visit",
    "decision", "tag", "event", "skill",
    # A watcher (ext-source): `Sources/Watchlist/<slug>.ref.md`, per
    # contracts/watchlist.md. The slug is the ref filename stem.
    "watch",
    # An agent (contracts/agents.md). The slug is the instance name, which is
    # the agent's id without its prefix: `updater`, `project:<slug>`. It is the
    # one kind whose slug may contain a colon.
    "agent",
    "other",
})

#: Kinds whose slug may carry further `:`-separated segments.
MULTI_SEGMENT_KINDS = frozenset({"agent"})

# Common legacy prefixes some ids carry; the parser folds them into kinds.
LEGACY_PREFIXES = {
    "contact-": "contact",
    "task-": "task",
    "bill-": "bill",
    "sub-": "subscription",
    "appt-": "appointment",
    "date-": "important_date",
    "doc-": "document",
    "psig-": "personal_signal",  # not in KINDS — but we support it for back-compat
    "sig-": "action_signal",
    "pm-": "pm_suggestion",
    "med-": "medication",
    "dec-": "decision",
    "art-": "artifact",
    "evt-": "event",
    "ingest-": "ingest_run",
    "uw-": "upstream_write",
}


@dc.dataclass(frozen=True)
class Handle:
    """Parsed operational handle."""

    kind: str
    slug: str

    def __str__(self) -> str:
        return f"{self.kind}:{self.slug}"


def parse(value: str | Handle) -> Handle:
    """Parse a string into a Handle. Accepts canonical and legacy forms.

    >>> parse("contact:dr-smith-dentist")
    Handle(kind='contact', slug='dr-smith-dentist')
    >>> parse("contact-dr-smith-dentist")  # legacy
    Handle(kind='contact', slug='dr-smith-dentist')
    >>> parse("task-20260428-001")
    Handle(kind='task', slug='20260428-001')
    """
    if isinstance(value, Handle):
        return value
    if not isinstance(value, str):
        raise TypeError(f"handle must be str or Handle, got {type(value).__name__}")
    raw = value.strip()
    if not raw:
        raise ValueError("empty handle")
    if ":" in raw:
        kind, _, slug = raw.partition(":")
        kind = kind.strip().lower()
        slug = slug.strip()
        if not slug:
            raise ValueError(f"handle missing slug: {raw!r}")
        return Handle(kind=kind, slug=slug)
    for prefix, kind in LEGACY_PREFIXES.items():
        if raw.startswith(prefix):
            return Handle(kind=kind, slug=raw[len(prefix):])
    return Handle(kind="other", slug=raw)


def format(kind: str, slug: str) -> str:
    """Construct a canonical handle string."""
    if not kind or not slug:
        raise ValueError("kind and slug required")
    kind = kind.strip().lower()
    slug = slug.strip()
    return f"{kind}:{slug}"


def is_handle(value: str) -> bool:
    """True if `value` looks like a canonical handle (`<kind>:<slug>`)."""
    if not isinstance(value, str) or ":" not in value:
        return False
    kind, _, slug = value.partition(":")
    return bool(kind) and bool(slug) and " " not in kind


def slug_for(kind: str, name: str) -> str:
    """Derive a slug from a free-text name. Lower-case, hyphenated, no punctuation.

    Used by skills that accept human input ("Dr. Smith — dentist") and need
    to construct a handle.
    """
    if not name:
        raise ValueError("empty name")
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


def collect_handles_in(text: str) -> list[Handle]:
    """Find every canonical handle mention in a string.

    >>> sorted(str(h) for h in collect_handles_in('see contact:alice and project:tax-2026'))
    ['contact:alice', 'project:tax-2026']
    >>> [str(h) for h in collect_handles_in('ask agent:project:tax-2026 about it')]
    ['agent:project:tax-2026']
    """
    if not text:
        return []
    pattern = re.compile(r"\b([a-z_]{3,20}):([a-z0-9][a-z0-9_-]*)")
    more = re.compile(r"(?::[a-z0-9][a-z0-9_-]*)+")
    seen: set[str] = set()
    out: list[Handle] = []
    pos = 0
    while (match := pattern.search(text, pos)) is not None:
        kind = match.group(1)
        slug = match.group(2)
        pos = match.end()
        if kind in MULTI_SEGMENT_KINDS and (tail := more.match(text, pos)) is not None:
            slug += tail.group(0)
            pos = tail.end()
        key = f"{kind}:{slug}"
        if key in seen:
            continue
        seen.add(key)
        out.append(Handle(kind=kind, slug=slug))
    return out


def filter_kind(handles: Iterable[Handle | str], kind: str) -> list[Handle]:
    """Keep only handles of the given kind."""
    out: list[Handle] = []
    for h in handles:
        ph = parse(h) if isinstance(h, str) else h
        if ph.kind == kind:
            out.append(ph)
    return out
