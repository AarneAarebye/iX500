#!/bin/zsh
# Starts (or restarts) the scanix500 menu bar app and waits until its
# HTTP bridge answers on localhost:8765.
#
# Uses the LaunchAgent when it's installed (see README "Start at login"),
# so the app keeps running after this terminal closes; otherwise starts the
# venv's scanix500-menubar in the background, logging to the same file.

set -u

REPO_DIR="${0:A:h}"
LABEL="com.scanix500.menubar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="/tmp/scanix500-menubar.log"
HEALTH_URL="http://127.0.0.1:8765/health"

if [[ -f "$PLIST" ]]; then
  if ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
  fi
  launchctl kickstart -k "gui/$(id -u)/$LABEL"
else
  BIN="$REPO_DIR/.venv/bin/scanix500-menubar"
  if [[ ! -x "$BIN" ]]; then
    echo "Not installed: $BIN is missing." >&2
    echo "Run: cd $REPO_DIR && python3 -m venv .venv && .venv/bin/pip install -e \".[menubar]\"" >&2
    exit 1
  fi
  pkill -f "$BIN" 2>/dev/null
  nohup "$BIN" >>"$LOG" 2>&1 &
fi

for _ in {1..20}; do
  if health=$(curl -s --max-time 1 "$HEALTH_URL"); then
    echo "scanix500 is running: $health"
    exit 0
  fi
  sleep 0.5
done

echo "scanix500 didn't answer on $HEALTH_URL after 10 seconds. Last log lines:" >&2
tail -n 20 "$LOG" >&2
exit 1
