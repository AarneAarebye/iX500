#!/bin/bash
# Runs dossiary-scan-helper's conformance suite against this bridge in fake
# scanner mode. Usage: scripts/run-conformance.sh [path to dossiary-scan-helper]
set -euo pipefail
cd "$(dirname "$0")/.."
SUITE_REPO="${1:-../Paperless/dossiary-scan-helper}"
PORT="${PORT:-8798}"
[ -d "$SUITE_REPO/conformance" ] || { echo "No conformance suite in $SUITE_REPO"; exit 1; }
.venv/bin/scanix500-bridge --fake-scanners --port "$PORT" > /tmp/scanix500-conformance.log 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do
  if curl -sf "http://127.0.0.1:$PORT/health" > /dev/null; then break; fi
  kill -0 "$PID" 2>/dev/null || { echo "bridge exited"; cat /tmp/scanix500-conformance.log; exit 1; }
  sleep 0.2
done
curl -sf "http://127.0.0.1:$PORT/health" > /dev/null || { echo "bridge did not start"; cat /tmp/scanix500-conformance.log; exit 1; }
cd "$SUITE_REPO"
SCAN_HELPER_URL="http://127.0.0.1:$PORT" python3 -m unittest discover -s conformance -v
