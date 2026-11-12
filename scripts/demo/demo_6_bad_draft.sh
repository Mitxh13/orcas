#!/usr/bin/env bash
# Demo 6: the verifier rejects a planted citation and the researcher retries.
# Runs against the Compose stack; see scripts/demo/agent_demos.py for the checks and options
# (--base-url, --timeout, --no-compose). Exits non-zero if any check fails.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec "${PYTHON:-python}" scripts/demo/agent_demos.py bad-draft "$@"
