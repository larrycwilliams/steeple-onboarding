#!/bin/zsh
# Install (or reinstall) the background refresh for Home's Today panel (doc 69).
#
#   Run on: the iMac hub, from ~/Dev/steeple-onboarding
#     zsh tools/install_refresh.sh            # preview: prints the plist, changes nothing
#     zsh tools/install_refresh.sh --apply    # write it and load it
#     zsh tools/install_refresh.sh --remove   # unload and delete it
#
# Every 15 minutes launchd runs tools/refresh_all.py. That script does nothing
# outside 7am-10pm, so the store is not polled all night. Reads only.
#
# Lessons carried over from the Studio install (doc 65):
#   * every variable followed by a colon is braced -- zsh reads "$x:a" as a modifier
#   * after bootout, wait until launchd has really let go before bootstrapping
#   * `launchctl enable` before bootstrap: a service left disabled fails with
#     "Bootstrap failed: 5: Input/output error" and nothing else
#   * the two doc-58 environment variables go in, same as the portal's own plist
set -eu

LABEL="com.steeplestitch.refresh"
APP_DIR="${HOME}/Dev/steeple-onboarding"
PY="${HOME}/.venvs/steeple-onboarding-312/bin/python"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
LOG="${HOME}/Library/Logs/steeple-refresh.log"
DOMAIN="gui/$(id -u)"
MODE="${1:-preview}"

if [[ "${MODE}" == "--remove" ]]; then
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
  [[ -f "${PLIST}" ]] && mv "${PLIST}" "${PLIST}.removed-$(date +%Y%m%d-%H%M%S)"
  echo "Removed ${LABEL}. The plist was set aside next to where it was, not deleted."
  exit 0
fi

[[ -x "${PY}" ]] || { echo "No portal venv at ${PY} — is this the hub?"; exit 1; }
[[ -f "${APP_DIR}/tools/refresh_all.py" ]] || { echo "tools/refresh_all.py is missing — apply the doc 69 patch first."; exit 1; }

read -r -d '' BODY <<PLISTEOF || true
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PY}</string>
    <string>${APP_DIR}/tools/refresh_all.py</string>
  </array>
  <key>WorkingDirectory</key><string>${APP_DIR}</string>
  <key>StartInterval</key><integer>900</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>${LOG}</string>
  <key>StandardErrorPath</key><string>${LOG}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>no_proxy</key><string>*</string>
    <key>OBJC_DISABLE_INITIALIZE_FORK_SAFETY</key><string>YES</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLISTEOF

if [[ "${MODE}" != "--apply" ]]; then
  echo "PREVIEW — nothing changed. This is what --apply would write to ${PLIST}:"
  echo
  echo "${BODY}"
  echo
  echo "Then: one test run now with --force, so you can see it work before launchd takes over."
  exit 0
fi

echo "1/4  Test run (ignores quiet hours)…"
( cd "${APP_DIR}" && "${PY}" tools/refresh_all.py --force ) || { echo "The test run failed — not installing."; exit 1; }

echo "2/4  Writing ${PLIST}"
mkdir -p "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
print -r -- "${BODY}" > "${PLIST}"
plutil -lint "${PLIST}" >/dev/null

echo "3/4  Loading…"
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
for i in {1..20}; do
  launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1 || break
  sleep 0.5
done
launchctl enable "${DOMAIN}/${LABEL}"
loaded=0
for attempt in {1..5}; do
  if launchctl bootstrap "${DOMAIN}" "${PLIST}" 2>/tmp/steeple-refresh-bootstrap.err; then
    loaded=1; break
  fi
  sleep 2
done
if [[ "${loaded}" != 1 ]]; then
  echo "launchd refused to load it:"; cat /tmp/steeple-refresh-bootstrap.err
  echo "Recovery: launchctl enable ${DOMAIN}/${LABEL} && launchctl bootstrap ${DOMAIN} ${PLIST}"
  exit 1
fi

echo "4/4  Check:"
launchctl print "${DOMAIN}/${LABEL}" | grep -E "state|run interval|last exit" || true
echo
echo "Installed. It runs every 15 minutes, 7am–10pm. Log: ${LOG}"
