#!/bin/bash
#
# Installs the Steeple & Stitch draft helper on THIS Mac.
#
# Run it on every Mac you want to open Mail drafts from: the iMac, the Neo, the
# Air. It is the same on all three, and installing it on the hub too means the
# button behaves identically everywhere rather than the hub being the odd one
# that works by a different route.
#
# What it installs:
#   ~/Library/Application Support/SteepleStitch/draft_helper.py
#   ~/Library/LaunchAgents/com.steeplestitch.drafthelper.plist
#
# The script is COPIED out of the repo rather than run from it, on purpose. The
# Neo and the Air do not necessarily have the repo checked out, and a launch
# agent pointing into a folder that may be moved, renamed or iCloud-evicted is
# a service that dies silently months later.
#
# Double-click it in Finder, or run it from Terminal.

set -euo pipefail

NAME="com.steeplestitch.drafthelper"
SUPPORT="$HOME/Library/Application Support/SteepleStitch"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$NAME.plist"
TARGET="$SUPPORT/draft_helper.py"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$HERE/draft_helper.py"
PORT="${SS_DRAFT_PORT:-5055}"

echo "Steeple & Stitch — draft helper"
echo "Installing on $(scutil --get ComputerName 2>/dev/null || hostname)"
echo

if [ ! -f "$SOURCE" ]; then
  echo "ERROR: draft_helper.py is not next to this installer."
  echo "Expected: $SOURCE"
  exit 1
fi

# python3 is the one prerequisite. On a Mac with no developer tools this is a
# stub that prompts to install them, so check for a real version rather than
# for the file existing.
if ! PYVER="$(/usr/bin/env python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"; then
  echo "ERROR: no working python3 on this Mac."
  echo "Install the command line tools first:  xcode-select --install"
  exit 1
fi
PYTHON="$(/usr/bin/env which python3)"
echo "  python3 $PYVER at $PYTHON"

mkdir -p "$SUPPORT" "$AGENTS"
cp "$SOURCE" "$TARGET"
chmod 644 "$TARGET"
echo "  helper  -> $TARGET"

# Written with a quoted heredoc. macOS is case-insensitive about commands, so
# pasting XML or config text into a shell can run something unexpected -- doc 23
# records `HostName` running `hostname`, which tries to RENAME the machine.
cat > "$PLIST" <<'PLISTEOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.steeplestitch.drafthelper</string>
  <key>ProgramArguments</key>
  <array>
    <string>__PYTHON__</string>
    <string>__TARGET__</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SS_DRAFT_PORT</key>
    <string>__PORT__</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>__LOG__.out.log</string>
  <key>StandardErrorPath</key>
  <string>__LOG__.err.log</string>
</dict>
</plist>
PLISTEOF

LOGBASE="$HOME/Library/Logs/steeple-draft-helper"
/usr/bin/sed -i '' \
  -e "s|__PYTHON__|$PYTHON|" \
  -e "s|__TARGET__|$TARGET|" \
  -e "s|__PORT__|$PORT|" \
  -e "s|__LOG__|$LOGBASE|" \
  "$PLIST"
echo "  agent   -> $PLIST"

# bootout then bootstrap, as two commands with a pause. Doc 23: pasted together
# they race and fail with "Bootstrap failed: 5: Input/output error". kickstart
# is no good here either -- it restarts a loaded job but will not pick up a
# changed plist.
launchctl bootout "gui/$(id -u)/$NAME" 2>/dev/null || true
sleep 1
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "  service -> loaded"

echo
echo "Checking it answers..."
sleep 1
for attempt in 1 2 3 4 5; do
  if REPLY_JSON="$(/usr/bin/curl -s --max-time 3 "http://127.0.0.1:$PORT/ping")"; then
    if [ -n "$REPLY_JSON" ]; then
      echo "  $REPLY_JSON"
      echo
      echo "Done. Reload a statement page and the button will read"
      echo "\"Open draft in Mail on $(scutil --get ComputerName 2>/dev/null || hostname)\"."
      echo
      echo "The first draft will raise a macOS prompt asking to control Mail."
      echo "Approve it once; it does not come back."
      exit 0
    fi
  fi
  sleep 1
done

echo "  no answer on port $PORT."
echo
echo "Check the log:  tail -20 $LOGBASE.err.log"
echo "Port in use?    lsof -nP -iTCP:$PORT -sTCP:LISTEN"
exit 1
