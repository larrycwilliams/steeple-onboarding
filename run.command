#!/bin/bash
# Double-click this file in Finder to start the onboarding app.
#
# The virtual environment deliberately lives OUTSIDE iCloud (~/.venvs).
# iCloud sync and Python venvs do not get along: sync churn on thousands of
# small package files causes slow starts and occasional broken installs.

cd "$(dirname "$0")"

VENV="$HOME/.venvs/steeple-onboarding"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
STAMP="$VENV/.requirements-hash"

fail() {
  echo ""
  echo "  ------------------------------------------------------------"
  echo "  $1"
  echo "  ------------------------------------------------------------"
  echo ""
  echo "  Press any key to close this window."
  read -r -n 1 -s
  exit 1
}

# ---------------------------------------------------------------- environment
if [ ! -d "$VENV" ]; then
  echo "First run — creating the environment at $VENV"
  python3 -m venv "$VENV" || fail "Could not create the Python environment.
  Check that python3 is installed:  python3 --version"
  "$PIP" install --quiet --upgrade pip
fi

# Only reinstall when requirements actually changed. Re-running pip on every
# launch was both slow and a silent single point of failure.
WANT="$(shasum requirements.txt 2>/dev/null | awk '{print $1}')"
HAVE="$(cat "$STAMP" 2>/dev/null)"

if [ "$WANT" != "$HAVE" ]; then
  echo "Installing dependencies…"
  if ! "$PIP" install -r requirements.txt; then
    fail "A required package failed to install (see the errors above).
  The app cannot start until that is resolved."
  fi
  echo "$WANT" > "$STAMP"

  # Optional extras must never block startup. The scan test is a nice-to-have;
  # if OpenCV will not install on this machine the app still works without it.
  if [ -f requirements-optional.txt ]; then
    echo "Installing optional extras (safe to fail)…"
    "$PIP" install --quiet -r requirements-optional.txt \
      || echo "  note: optional extras unavailable — QR scan verification is off."
  fi
fi

# ------------------------------------------------------------------ preflight
"$PY" - <<'PYCHECK' || fail "The app's own modules failed to load (see above)."
import sys
try:
    import flask, docx, docxtpl, segno, PIL, numpy   # noqa: F401
except Exception as exc:
    print(f"  missing dependency: {exc}")
    sys.exit(1)
PYCHECK

if [ ! -f "docx_templates/launch_week_kit.docx" ]; then
  echo "Building document templates from source_docs/…"
  "$PY" tools/build_templates.py || fail "Template build failed (see above)."
fi

echo ""
echo "  Close this window to stop the app."
# app.py prints the address(es) itself, because which ones are correct depends
# on SS_HOST. Printing a second, hardcoded URL here would contradict it the
# moment this is launched from run-mobile.command.

# When serving to the iPad or phone, a Mac that goes to sleep takes the app
# with it. -i blocks idle sleep only: closing the lid still sleeps, and the
# display still dims on its own.
if [ -n "$SS_KEEP_AWAKE" ] && command -v caffeinate >/dev/null 2>&1; then
  echo "  Keeping this Mac awake while the app is running."
  echo ""
  caffeinate -i "$PY" app.py
else
  echo ""
  "$PY" app.py
fi
STATUS=$?
if [ $STATUS -ne 0 ]; then
  fail "The app exited unexpectedly (code $STATUS)."
fi
