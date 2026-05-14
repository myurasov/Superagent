# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
"""Email memory tools.

Backs `contracts/email-capture.md`. The only public module is `archive`,
which mirrors every Gmail MCP read / send into a local per-message
archive under `workspace/_memory/email/`. The helpers in `archive.py`
are pure (no MCP calls); skills invoke MCP themselves and pass the
results through `capture_inbound` / `capture_sent` / `maybe_capture_stubs`.
"""
