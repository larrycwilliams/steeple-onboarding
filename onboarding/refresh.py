"""Bring every cache Home reads up to date (doc 69, phase 1).

Run every 15 minutes by launchd (tools/refresh_all.py, installed by
tools/install_refresh.sh) and by Home's **Refresh now** button.

Each step reuses the code its own page already uses, so there is exactly one
way each cache gets written:

  * Shopify orders   -> shopify_sales.snapshot(refresh=True)   (Dashboard's Refresh)
  * Leads            -> leads.snapshot(refresh=True)           (Pipeline's Refresh)
  * S&S order status -> purchasing.check_ss_status()           (Check S&S status)
  * Studio summary   -> GET <studio>/api/summary, saved to cache/studio_summary.json
  * Press Station    -> GET <studio>/api/pressed: runs that named a Shopify order
                        mark that order Pressed on the Orders board (doc 69 phase 4)

Every one of those keeps the previous good answer when it fails, so a failed
run never blanks Home. Steps run one after another and a failure in one never
stops the next. The purchasing *proposal* is deliberately NOT refreshed here:
a new proposal invalidates any review or test in progress (purchasing.py), and
that must only ever happen because a person pressed Refresh.

Reads only. Nothing here writes to Shopify, S&S or the Studio.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from . import leads, orders, purchasing, shopify_sales, twc_orders

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
STATE = CACHE / "refresh.json"
LOCK = CACHE / "refresh.lock"
STUDIO_SUMMARY = CACHE / "studio_summary.json"
PRESS_SEEN = CACHE / "press_seen.json"      # the last Studio press-log id already applied

# The studio process on the hub, reached over loopback -- not the tailnet URL
# people click, which goes through `tailscale serve` and its certificate.
STUDIO_INTERNAL = os.environ.get("STUDIO_INTERNAL_URL", "http://127.0.0.1:5100").rstrip("/")
STUDIO_TIMEOUT = 8


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, path)          # whole file or nothing; a reader never sees half


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _single_run():
    """Yields True if this process holds the run lock, False if another run has it."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        handle.close()


# ------------------------------------------------------------------ the steps

def _shopify() -> tuple[bool, str]:
    snap, source = shopify_sales.snapshot(refresh=True)
    return source == "live", source if source != "live" else f"{len((snap or {}).get('orders') or [])} orders"


def _leads() -> tuple[bool, str]:
    snap, source = leads.snapshot(refresh=True)
    return source == "live", source if source != "live" else f"{len((snap or {}).get('leads') or [])} leads"


def _ss_status() -> tuple[bool, str]:
    problems = purchasing.setup_problems()
    if problems:
        return False, " ".join(problems)
    snap = purchasing.check_ss_status()
    if not snap.get("ok"):
        return False, snap.get("error") or "S&S status check failed"
    return True, f"{len(snap.get('orders') or {})} S&S orders checked"


def fetch_studio_summary() -> tuple[bool, str]:
    """Save the Studio's counts. On failure keep the last good counts, marked."""
    old = _read(STUDIO_SUMMARY)
    try:
        with urllib.request.urlopen(f"{STUDIO_INTERNAL}/api/summary",
                                    timeout=STUDIO_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict) or not data.get("ok"):
            raise ValueError("the Studio answered, but not with a summary")
        data["at"] = _stamp()
        _write(STUDIO_SUMMARY, data)
        return True, f"Studio v{data.get('version', '?')}"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        if old.get("ok"):
            old["failed_at"], old["error"] = _stamp(), message
            _write(STUDIO_SUMMARY, old)
        else:
            _write(STUDIO_SUMMARY, {"ok": False, "error": message, "failed_at": _stamp()})
        return False, message


def order_key_for(ref: str) -> str:
    """'#1136' -> 'ss|#1136'; 'TWC #1050' -> 'twc|#1050' (the Studio's clean_order_ref forms)."""
    ref = (ref or "").strip()
    if ref.upper().startswith("TWC"):
        return orders.order_key("twc", "#" + ref.split("#", 1)[-1].strip())
    return orders.order_key("ss", ref if ref.startswith("#") else "#" + ref)


def apply_press_runs() -> tuple[bool, str]:
    """Mark Pressed on the Orders board for every Studio press run that named an
    order (doc 69 phase 4). Reads the Studio's /api/pressed; writes only the
    board's own tick file. A run for an order already ticked changes nothing."""
    seen = _read(PRESS_SEEN)
    after = int(seen.get("last_id") or 0)
    try:
        with urllib.request.urlopen(f"{STUDIO_INTERNAL}/api/pressed?after={after}",
                                    timeout=STUDIO_TIMEOUT) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict) or not data.get("ok"):
            raise ValueError("the Studio answered, but not with its press log")
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    ticks = orders.load_ticks()
    marked = 0
    last = after
    for run in data.get("runs") or []:
        last = max(last, int(run.get("id") or 0))
        key = order_key_for(run.get("order") or "")
        if not key.split("|", 1)[1].lstrip("#") or "pressed" in ticks.get(key, {}):
            continue
        by = " · ".join(x for x in (run.get("station"), run.get("by")) if x)
        ok, _message = orders.tick(key, "pressed", by=by or "Press Station")
        if ok:
            marked += 1
            ticks.setdefault(key, {})["pressed"] = True
    _write(PRESS_SEEN, {"last_id": last, "at": _stamp()})
    return True, f"{marked} order{'s' if marked != 1 else ''} marked pressed"


STEPS = (
    ("Shopify orders", _shopify),
    ("TWC orders", twc_orders.refresh),
    ("Leads", _leads),
    ("S&S order status", _ss_status),
    ("Studio", fetch_studio_summary),
    ("Press Station", apply_press_runs),
)


def run_all(trigger: str = "schedule") -> dict:
    """Run every step once. Returns the saved state. Never raises."""
    with _single_run() as mine:
        if not mine:
            return {"skipped": True, "reason": "another refresh is already running"}
        state = {"running": True, "trigger": trigger, "started_at": _stamp(), "steps": []}
        _write(STATE, state)
        for name, step in STEPS:
            began = time.monotonic()
            try:
                ok, detail = step()
            except Exception as exc:
                ok, detail = False, f"{type(exc).__name__}: {exc}"
            state["steps"].append({"name": name, "ok": bool(ok), "detail": str(detail)[:400],
                                   "seconds": round(time.monotonic() - began, 1)})
        state.update(running=False, finished_at=_stamp())
        _write(STATE, state)
        return state
