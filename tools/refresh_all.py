#!/usr/bin/env python3
"""Refresh every cache Home reads. Run by launchd every 15 minutes (doc 69).

    ~/.venvs/steeple-onboarding-312/bin/python tools/refresh_all.py          # obeys quiet hours
    ~/.venvs/steeple-onboarding-312/bin/python tools/refresh_all.py --force  # run now regardless

Quiet hours: outside 7am-10pm hub time a scheduled run does nothing, so the
store is not polled all night. --force (and Home's Refresh now) ignore them.

Reads only: Shopify, the leads, S&S order status, the Studio summary.
Always exits 0 when it ran, so launchd does not throttle it after a bad
network hour; the outcome is in cache/refresh.json and on Home.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import refresh  # noqa: E402

FIRST_HOUR, LAST_HOUR = 7, 22     # runs from 07:00 up to 21:59


def main(argv: list[str]) -> int:
    force = "--force" in argv
    hour = datetime.now().hour
    if not force and not (FIRST_HOUR <= hour < LAST_HOUR):
        print(f"{datetime.now():%Y-%m-%d %H:%M} quiet hours — nothing to do")
        return 0
    state = refresh.run_all(trigger="manual" if force else "schedule")
    stamp = f"{datetime.now():%Y-%m-%d %H:%M}"
    if state.get("skipped"):
        print(f"{stamp} skipped: {state['reason']}")
        return 0
    for step in state["steps"]:
        mark = "ok  " if step["ok"] else "FAIL"
        print(f"{stamp} {mark} {step['name']:<17} {step['seconds']:>5}s  {step['detail']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
