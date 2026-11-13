#!/usr/bin/env bash
# Demo 8: a provider outage opens the breaker and runs escalate as degraded.
# Runs against the Compose stack; see scripts/demo/agent_demos.py for the checks and options
# (--base-url, --timeout, --no-compose). Exits non-zero if any check fails.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec "${PYTHON:-python}" scripts/demo/agent_demos.py provider-outage "$@"
