#!/bin/zsh
# Stops the scanix500 menu bar app.
#
# The LaunchAgent restarts the app whenever it doesn't exit cleanly
# (KeepAlive), so a plain kill would bring it straight back: the agent is
# unloaded instead. It loads again at the next login, or with
# ./start-menubar.sh. A copy started by hand is stopped too.

set -u

REPO_DIR="${0:A:h}"
LABEL="com.scanix500.menubar"
BIN="$REPO_DIR/.venv/bin/scanix500-menubar"
HEALTH_URL="http://127.0.0.1:8765/health"

if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$(id -u)/$LABEL"
fi
pkill -f "$BIN" 2>/dev/null

for _ in {1..20}; do
  if ! curl -s --max-time 1 "$HEALTH_URL" >/dev/null; then
    echo "scanix500 is stopped."
    exit 0
  fi
  sleep 0.5
done

echo "scanix500 still answers on $HEALTH_URL after 10 seconds." >&2
exit 1
