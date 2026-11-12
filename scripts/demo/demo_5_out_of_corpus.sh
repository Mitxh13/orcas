#!/usr/bin/env bash
# Demo 5: an out-of-corpus complaint (health insurance) is escalated, not answered.
# Runs against the Compose stack; see scripts/demo/agent_demos.py for the checks and options
# (--base-url, --timeout, --no-compose). Exits non-zero if any check fails.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec "${PYTHON:-python}" scripts/demo/agent_demos.py out-of-corpus "$@"
