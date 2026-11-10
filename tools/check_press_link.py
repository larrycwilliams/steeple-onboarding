#!/usr/bin/env python3
"""Offline check: Press Station runs mark orders Pressed (doc 69, phase 4).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_press_link.py

Copies the app's CODE (never .env, partners/, cache/) into a temp folder and
stands in for the Studio, so nothing real is read, written or called.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

tmp = Path(tempfile.mkdtemp(prefix="press-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))

from onboarding import orders, refresh  # noqa: E402

assert Path(refresh.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"
(work / "cache").mkdir(exist_ok=True)

ok = fail = 0


def check(name: str, cond) -> None:
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


RUNS: list[dict] = []
CALLS: list[str] = []
DOWN = False


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(url, timeout=None):
    CALLS.append(url)
    if DOWN:
        raise OSError("Connection refused")
    after = int(url.rsplit("after=", 1)[1])
    return _Resp(json.dumps({"ok": True, "runs": [r for r in RUNS if r["id"] > after]}).encode())


refresh.urllib.request.urlopen = fake_urlopen

check("'#1136' is a Steeple & Stitch order", refresh.order_key_for("#1136") == "ss|#1136")
check("'TWC #1050' is a TWC order", refresh.order_key_for("TWC #1050") == "twc|#1050")
check("Press Station is a refresh step", any(n == "Press Station" for n, _f in refresh.STEPS))

RUNS[:] = [{"id": 1, "order": "#1136", "station": "Epson 1", "by": "Larry"},
           {"id": 2, "order": "TWC #1050", "station": "Epson 2", "by": ""},
           {"id": 3, "order": "#1136", "station": "Epson 1", "by": "Larry"},
           {"id": 4, "order": "", "station": "Epson 1", "by": ""}]
good, msg = refresh.apply_press_runs()
ticks = orders.load_ticks()
check("two orders marked pressed from four runs", good and msg == "2 orders marked pressed")
check("who pressed it is the station and the person",
      ticks["ss|#1136"]["pressed"]["by"] == "Epson 1 · Larry"
      and ticks["twc|#1050"]["pressed"]["by"] == "Epson 2")
check("a run with no order is skipped", set(ticks) == {"ss|#1136", "twc|#1050"})
check("asks only for runs after the last one seen", CALLS[-1].endswith("after=0"))

good, msg = refresh.apply_press_runs()
check("next refresh asks after run 4 and changes nothing",
      CALLS[-1].endswith("after=4") and msg == "0 orders marked pressed")

orders.tick("ss|#1136", "pressed", undo=True)
refresh.apply_press_runs()
check("an undo on the board is not overwritten by an old run", "ss|#1136" not in orders.load_ticks())

orders.tick("ss|#1140", "packed", by="Larry")
RUNS.append({"id": 5, "order": "#1140", "station": "Epson 1", "by": ""})
_, msg = refresh.apply_press_runs()
check("an order already packed keeps its ticks", msg == "0 orders marked pressed"
      and "packed" in orders.load_ticks()["ss|#1140"])

DOWN = True
before = refresh.PRESS_SEEN.read_text()
good, msg = refresh.apply_press_runs()
check("Studio down: the step fails, and nothing is lost", not good and "Connection refused" in msg
      and refresh.PRESS_SEEN.read_text() == before)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
