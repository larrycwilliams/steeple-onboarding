#!/bin/bash
# Double-click this file in Finder to rebuild every partner's package.
#
# Same environment as run.command. Nothing to type, no folder to cd into --
# the script finds itself, which is the whole point: the command-line version
# fails the moment it is run from the wrong directory.

cd "$(dirname "$0")"

VENV="$HOME/.venvs/steeple-onboarding"
PY="$VENV/bin/python"

hold() {
  echo ""
  echo "  Press any key to close this window."
  read -r -n 1 -s
}

if [ ! -x "$PY" ]; then
  echo ""
  echo "  The Python environment is missing."
  echo "  Double-click run.command once to build it, then try this again."
  hold
  exit 1
fi

echo ""
echo "  Checking this Mac first…"
echo ""
"$PY" tools/preflight.py
STATUS=$?
if [ $STATUS -ne 0 ]; then
  echo ""
  echo "  Preflight found a blocking problem (above). Nothing was regenerated."
  hold
  exit 1
fi

echo ""
echo "  Regenerating every partner. With Microsoft Word this takes a few"
echo "  minutes; with LibreOffice, seconds."
echo ""
"$PY" tools/regenerate_all.py
STATUS=$?

echo ""
if [ $STATUS -eq 0 ]; then
  echo "  Done. Files are in the output folder, one folder per partner."
else
  echo "  Finished with problems (above)."
fi
hold
exit $STATUS
