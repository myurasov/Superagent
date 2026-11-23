#!/bin/sh
# SPDX-FileCopyrightText: 2026 Mikhail Yurasov
# SPDX-License-Identifier: Apache-2.0
#
# The check a granted wake runs first (rules/autocode.md § 3, contracts/agents.md § 8).
#
#   sh superagent/tools/wake_check.sh <agent> [<duty>] --wake <name>
#
# The runtime runs it in the checkout, with a PATH that may not hold `uv` (a
# background service starts with a bare one). It adds the usual places, then
# hands over to `tools/autocode.py check`: the daily cost cap, then the
# duty's own check. Exit 0 = due (the reason is printed), 1 = skip this run
# at no cost, anything else = the check itself failed.
cd "$(dirname "$0")/../.." || exit 3
PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
UV_PROJECT_ENVIRONMENT="$PWD/.venv"
export PATH UV_PROJECT_ENVIRONMENT
if ! command -v uv >/dev/null 2>&1; then
  echo "wake check: uv is not on PATH" >&2
  exit 3
fi
exec uv run python -m superagent.tools.autocode check "$@"
