# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Registry of every supported Superagent ingestor.

The registry is metadata-only — it describes what's possible, the auth /
install requirements, and where the ingestor module lives. Actual
ingestors load lazily on use.

When you add a new ingestor module, also add a row here. Tests verify that
every entry can be imported and exposes the `IngestorBase` contract.
"""
from __future__ import annotations

import dataclasses as dc


@dc.dataclass(frozen=True)
class IngestorSpec:
    """Metadata for one supported ingestor."""
    source: str                  # slug (matches data-sources.yaml row id)
    module: str                  # importable module path under `superagent.tools.ingest`
    kind: str                    # mcp | cli | api | file
    description: str             # one-line
    install_hint: str            # one-line "how to set this up"
    docs_anchor: str             # anchor in docs/data-sources.md
    writes_destinations: tuple[str, ...]  # which workspace files this ingestor writes to


REGISTRY: tuple[IngestorSpec, ...] = (
    # --- Email + calendar -----------------------------------------------------
    IngestorSpec(
        source="gmail",
        module="gmail",
        kind="api",
        description="Gmail message metadata -> _memory/_gmail/<YYYY-MM>.jsonl (read-only).",
        install_hint=(
            "Install gongrzhe Gmail MCP (`sudo npm install -g @gongrzhe/server-gmail-autoauth-mcp`) "
            "for the chat-time tools, then `npx -y @gongrzhe/server-gmail-autoauth-mcp auth` once. "
            "The headless ingestor reuses ~/.gmail-mcp/ tokens via the Gmail API."
        ),
        docs_anchor="gmail",
        writes_destinations=("_memory/_gmail/<YYYY-MM>.jsonl",),
    ),
    IngestorSpec(
        source="icloud_mail",
        module="icloud_mail",
        kind="mcp",
        description="Apple Mail (IMAP via iCloud MCP) → same as gmail.",
        install_hint="Install iCloud MCP; create an app-specific password in Apple ID settings.",
        docs_anchor="icloud-mail",
        writes_destinations=(
            "interaction-log.yaml", "appointments.yaml", "bills.yaml",
            "subscriptions.yaml", "Domains/<inferred>/history.md",
        ),
    ),
    IngestorSpec(
        source="outlook",
        module="outlook",
        kind="mcp",
        description="Outlook personal mailbox via Microsoft Graph.",
        install_hint="Install Outlook MCP; OAuth your Microsoft 365 / Outlook.com account.",
        docs_anchor="outlook",
        writes_destinations=(
            "interaction-log.yaml", "appointments.yaml", "bills.yaml",
            "subscriptions.yaml", "Domains/<inferred>/history.md",
        ),
    ),
    IngestorSpec(
        source="google_calendar",
        module="google_calendar",
        kind="mcp",
        description="Google Calendar events → appointments.yaml + history.md.",
        install_hint="Same Google Workspace MCP as gmail; calendar scope must be granted.",
        docs_anchor="google-calendar",
        writes_destinations=("appointments.yaml", "Domains/<inferred>/history.md"),
    ),
    IngestorSpec(
        source="icloud_calendar",
        module="icloud_calendar",
        kind="mcp",
        description="Apple Calendar via CalDAV (iCloud MCP).",
        install_hint="Same iCloud MCP as icloud_mail; CalDAV permission required.",
        docs_anchor="icloud-calendar",
        writes_destinations=("appointments.yaml", "Domains/<inferred>/history.md"),
    ),
    IngestorSpec(
        source="outlook_calendar",
        module="outlook_calendar",
        kind="mcp",
        description="Outlook Calendar via Microsoft Graph.",
        install_hint="Same Outlook MCP as outlook.",
        docs_anchor="outlook-calendar",
        writes_destinations=("appointments.yaml", "Domains/<inferred>/history.md"),
    ),
    # --- Reminders + notes ----------------------------------------------------
    IngestorSpec(
        source="apple_reminders",
        module="apple_reminders",
        kind="cli",
        description="Apple Reminders → todo.yaml (one-way: Reminders -> Superagent).",
        install_hint="brew install rem (https://rem.sidv.dev/) and grant Reminders access.",
        docs_anchor="apple-reminders",
        writes_destinations=("todo.yaml",),
    ),
    IngestorSpec(
        source="apple_notes",
        module="apple_notes",
        kind="cli",
        description="Apple Notes (osascript / JXA) -> snapshots in Domains/<inferred>/Resources/notes/.",
        install_hint="Built-in macOS; grant Automation access on first run.",
        docs_anchor="apple-notes",
        writes_destinations=("Domains/<inferred>/Resources/notes/",),
    ),
    IngestorSpec(
        source="obsidian",
        module="obsidian",
        kind="mcp",
        description="Obsidian vault index — frontmatter, tags, links.",
        install_hint="Install MCPVault or another Obsidian MCP; point at your vault directory.",
        docs_anchor="obsidian",
        writes_destinations=("_memory/obsidian-index.yaml",),
    ),
    IngestorSpec(
        source="notion",
        module="notion",
        kind="api",
        description="Notion pages / databases (official Notion API).",
        install_hint="Generate an integration token at notion.so/my-integrations and grant database access.",
        docs_anchor="notion",
        writes_destinations=("_memory/notion-index.yaml",),
    ),
    # --- Finance --------------------------------------------------------------
    IngestorSpec(
        source="plaid",
        module="plaid",
        kind="cli",
        description="Plaid bank / card / brokerage transactions.",
        install_hint="brew install plaid-cli OR pip install yapcli; obtain Plaid API keys.",
        docs_anchor="plaid",
        writes_destinations=(
            "_memory/transactions.yaml", "accounts-index.yaml",
            "bills.yaml", "subscriptions.yaml",
        ),
    ),
    IngestorSpec(
        source="monarch",
        module="monarch",
        kind="cli",
        description="Monarch Money transactions.",
        install_hint="pip install monarch-cli; log in with your Monarch credentials.",
        docs_anchor="monarch",
        writes_destinations=(
            "_memory/transactions.yaml", "accounts-index.yaml",
            "bills.yaml", "subscriptions.yaml",
        ),
    ),
    IngestorSpec(
        source="ynab",
        module="ynab",
        kind="api",
        description="YNAB transactions via official API.",
        install_hint="Generate a personal access token at api.ynab.com.",
        docs_anchor="ynab",
        writes_destinations=("_memory/transactions.yaml", "accounts-index.yaml"),
    ),
    IngestorSpec(
        source="csv",
        module="csv",
        kind="file",
        description="Generic bank-statement CSV import (Chase, BoA, Wells Fargo, Amex, Schwab, Fidelity).",
        install_hint="No setup; pass --file <path> at invocation.",
        docs_anchor="csv",
        writes_destinations=("_memory/transactions.yaml",),
    ),
    # --- Health + wearables ---------------------------------------------------
    IngestorSpec(
        source="apple_health",
        module="apple_health",
        kind="cli",
        description="Apple Health export.zip → health-records vitals.",
        install_hint="brew install healthsync; export Health data on iPhone (Settings > Health > Export All).",
        docs_anchor="apple-health",
        writes_destinations=("health-records.yaml",),
    ),
    IngestorSpec(
        source="whoop",
        module="whoop",
        kind="mcp",
        description="WHOOP recovery / strain / sleep / cycles.",
        install_hint="Install WHOOP MCP; authorize your WHOOP account.",
        docs_anchor="whoop",
        writes_destinations=("health-records.yaml",),
    ),
    IngestorSpec(
        source="strava",
        module="strava",
        kind="mcp",
        description="Strava workouts and routes.",
        install_hint="Install a Strava MCP; OAuth your Strava account.",
        docs_anchor="strava",
        writes_destinations=("Domains/Hobbies/history.md", "health-records.yaml"),
    ),
    IngestorSpec(
        source="garmin",
        module="garmin",
        kind="mcp",
        description="Garmin Connect — comprehensive health + fitness.",
        install_hint="Install Garmin MCP; authorize your Garmin Connect account.",
        docs_anchor="garmin",
        writes_destinations=("health-records.yaml", "Domains/Hobbies/history.md"),
    ),
    IngestorSpec(
        source="oura",
        module="oura",
        kind="mcp",
        description="Oura Ring sleep / readiness / activity.",
        install_hint="Install an Oura MCP; OAuth your Oura account.",
        docs_anchor="oura",
        writes_destinations=("health-records.yaml",),
    ),
    IngestorSpec(
        source="fitbit",
        module="fitbit",
        kind="mcp",
        description="Fitbit (coming-soon stub).",
        install_hint="No mature MCP yet; track via Open Wearables aggregator.",
        docs_anchor="fitbit",
        writes_destinations=("health-records.yaml",),
    ),
    # --- Smart home + vehicles ------------------------------------------------
    IngestorSpec(
        source="home_assistant",
        module="home_assistant",
        kind="mcp",
        description="Home Assistant — devices, automations, sensor anomalies.",
        install_hint="Install ha-mcp (or HA's official MCP); set HOMEASSISTANT_URL + token.",
        docs_anchor="home-assistant",
        writes_destinations=(
            "Domains/Home/history.md", "Domains/Home/Resources/ha-snapshots/",
        ),
    ),
    IngestorSpec(
        source="smartthings",
        module="smartthings",
        kind="mcp",
        description="Samsung SmartThings devices and scenes.",
        install_hint="Install smartthings-mcp; OAuth your SmartThings account.",
        docs_anchor="smartthings",
        writes_destinations=("Domains/Home/history.md",),
    ),
    IngestorSpec(
        source="tesla",
        module="tesla",
        kind="mcp",
        description="Tesla vehicle telemetry, charging, location.",
        install_hint="Install tesla-mcp; complete the Tesla Fleet auth flow.",
        docs_anchor="tesla",
        writes_destinations=("assets-index.yaml", "Domains/Vehicles/history.md"),
    ),
    # --- Communications -------------------------------------------------------
    IngestorSpec(
        source="imessage",
        module="imessage",
        kind="cli",
        description="iMessage chat.db (read-only export of important contacts).",
        install_hint="brew install imessage-exporter; grant Full Disk Access on macOS.",
        docs_anchor="imessage",
        writes_destinations=("interaction-log.yaml",),
    ),
    IngestorSpec(
        source="slack",
        module="slack",
        kind="mcp",
        description="Personal Slack workspaces.",
        install_hint="Install a Slack MCP; OAuth your personal workspace(s).",
        docs_anchor="slack",
        writes_destinations=("interaction-log.yaml",),
    ),
    # --- Files + media + location ---------------------------------------------
    IngestorSpec(
        source="photos",
        module="photos",
        kind="cli",
        description="Photo metadata via exiftool — date / location timeline.",
        install_hint="brew install exiftool; point ingestor at your Photos library export.",
        docs_anchor="photos",
        writes_destinations=("_memory/photo-locations.jsonl",),
    ),
    IngestorSpec(
        source="gmaps_timeline",
        module="gmaps_timeline",
        kind="file",
        description="Google Maps Timeline (Takeout JSON) → location-timeline.jsonl.",
        install_hint="Download Location History from takeout.google.com; pass --file at invocation.",
        docs_anchor="gmaps-timeline",
        writes_destinations=("_memory/location-timeline.jsonl",),
    ),
)


def find(source: str) -> IngestorSpec | None:
    """Return the spec for `source` if registered, else None."""
    for spec in REGISTRY:
        if spec.source == source:
            return spec
    return None
