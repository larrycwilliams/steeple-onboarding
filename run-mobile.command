#!/bin/bash
# Double-click this instead of run.command when you want to use the app from
# the iPad or the phone.
#
# Difference from run.command, and that is all it is:
#
#   SS_HOST=tailscale   serve on this Mac's Tailscale address, so your own
#                       devices can reach it from anywhere and nobody else
#                       can reach it at all. There is no login on this app,
#                       so the tailnet IS the lock.
#   SS_KEEP_AWAKE=1     stop the Mac idling to sleep while it is serving.
#                       Closing the lid still sleeps it.
#
# If Tailscale is not running, app.py says so and stays on localhost rather
# than quietly falling back to something less private.

cd "$(dirname "$0")"
export SS_HOST=tailscale
export SS_KEEP_AWAKE=1
exec ./run.command
