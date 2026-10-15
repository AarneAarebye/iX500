#!/bin/bash
# Installs a scanix500 release as this Mac's copy, separate from the git
# checkout: <install>/venv -> venvs/<version>-<stamp>, the scanix500-menubar
# LaunchAgent, and the scanix500 command on PATH.
# Usage: scripts/install-app.sh <tag>   (e.g. v0.5.0; any git ref works)
set -euo pipefail

REF="${1:-}"
[ -n "$REF" ] || { echo "Usage: $0 <tag>   (e.g. v0.5.0)" >&2; exit 2; }

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${SCANIX500_INSTALL_DIR:-/Applications/Dossiary/scanix500}"
AGENT_DIR="${SCANIX500_AGENT_DIR:-$HOME/Library/LaunchAgents}"
LOG_FILE="${SCANIX500_LOG:-$HOME/Library/Logs/scanix500.log}"
BIN_LINK="${SCANIX500_BIN_LINK:-/opt/homebrew/bin/scanix500}"
LAUNCHCTL="${LAUNCHCTL:-launchctl}"
BREW="${BREW:-brew}"
LABEL="com.scanix500.menubar"

fail() { echo "install-app: $*" >&2; exit 1; }

# 1. Homebrew prerequisites
for formula in sane-backends ocrmypdf tesseract; do
  "$BREW" list --versions "$formula" > /dev/null 2>&1 \
    || fail "$formula is missing. Install with: brew install sane-backends ocrmypdf tesseract tesseract-lang"
done

# 2. Export the ref; the working copy and its branch are never touched
git -C "$REPO_ROOT" rev-parse --verify --quiet "$REF^{commit}" > /dev/null \
  || fail "no such tag or commit: $REF"
SRC="$(mktemp -d)"
NEW=""
SWAPPED=0
cleanup() {
  rm -rf "$SRC"
  if [ -n "$NEW" ] && [ "$SWAPPED" = 0 ]; then rm -rf "$NEW"; fi
}
trap cleanup EXIT
git -C "$REPO_ROOT" archive "$REF" | tar -x -C "$SRC"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || fail "no version in pyproject.toml at $REF"

# 3. A new environment in its own folder (a venv can't be moved once made)
PYTHON="${PYTHON:-$("$BREW" --prefix)/bin/python3}"
mkdir -p "$INSTALL_DIR/venvs"
NEW="$INSTALL_DIR/venvs/$VERSION-$(date +%Y%m%dT%H%M%S)"
echo "Installing scanix500 $VERSION ($REF) into $NEW"
"$PYTHON" -m venv "$NEW"
SANE_PREFIX="$("$BREW" --prefix sane-backends)"
CPATH="$SANE_PREFIX/include" LIBRARY_PATH="$SANE_PREFIX/lib" \
  "$NEW/bin/pip" install --quiet python-sane
"$NEW/bin/pip" install --quiet "$SRC[menubar]"

# 4. Check it before switching
REPORTED="$("$NEW/bin/scanix500" --version)" || fail "scanix500 --version failed in the new environment"
[ "$REPORTED" = "scanix500 $VERSION" ] \
  || fail "scanix500 --version printed '$REPORTED', expected 'scanix500 $VERSION'"
"$NEW/bin/python" -c "import sane" || fail "python-sane does not load in the new environment"
[ -x "$NEW/bin/scanix500-menubar" ] || fail "scanix500-menubar is missing from the new environment"

# 5. Switch
"$LAUNCHCTL" bootout "gui/$UID/$LABEL" > /dev/null 2>&1 || true
ln -sfn "venvs/$(basename "$NEW")" "$INSTALL_DIR/venv"
SWAPPED=1
echo "$VERSION ($REF)" > "$INSTALL_DIR/VERSION"

# 6. LaunchAgent pointing at the installed copy
mkdir -p "$AGENT_DIR" "$(dirname "$LOG_FILE")"
PLIST="$AGENT_DIR/$LABEL.plist"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$INSTALL_DIR/venv/bin/scanix500-menubar</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>$INSTALL_DIR/venv/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <key>StandardOutPath</key>
    <string>$LOG_FILE</string>
    <key>StandardErrorPath</key>
    <string>$LOG_FILE</string>
</dict>
</plist>
PLIST
loaded=0
for _ in 1 2 3 4 5; do
  if "$LAUNCHCTL" bootstrap "gui/$UID" "$PLIST" 2>/dev/null; then loaded=1; break; fi
  sleep 1  # bootout can take a moment to finish
done
[ "$loaded" = 1 ] || fail "step 6: launchctl bootstrap gui/$UID $PLIST failed; earlier environments are still in $INSTALL_DIR/venvs"

# 7. The scanix500 command
mkdir -p "$(dirname "$BIN_LINK")"
ln -sfn "$INSTALL_DIR/venv/bin/scanix500" "$BIN_LINK" || fail "step 7: could not link $BIN_LINK"

# 8. Remove the environments no longer in use
for dir in "$INSTALL_DIR"/venvs/*; do
  [ "$dir" = "$NEW" ] || rm -rf "$dir"
done
echo "scanix500 $VERSION ($REF) is installed in $INSTALL_DIR and running."
