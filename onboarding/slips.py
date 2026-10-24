"""The Slips tab: reprint a 4x6 packing slip without the terminal.

Reprinting was `python3 ~/Dev/slipprint/slipprint.py print 1088` -- a terminal
step, and terminal steps get gone around (doc 46). This puts it on a page.

Decisions:

* slipprint is IMPORTED, not subprocessed. It is stdlib-only and its pieces
  are plain functions (find_order, build_slip, write_pdf, send_to_printer),
  and importing is what lets the preview come back to the browser -- the CLI's
  `preview` runs `open` on the hub, which is no use from the phone.
* SLIPPRINT DECIDES EVERYTHING ABOUT THE SLIP. Which lines are in-house, the
  layout, the printer queue, the lp options, the 15-second gap the Jadens
  needs -- all read from slipprint and its settings.json, so a reprint from
  here is byte-for-byte the slip the launchd job would have printed.
* The module is re-loaded when slipprint.py changes on disk, so an edit to
  slipprint shows up here without restarting gunicorn.
* Nothing is written to Shopify, and state.json is never touched: a reprint
  is not a "this order has printed" event, same as the CLI.
* slipprint reports "order not found" and "store not connected" with
  SystemExit. That is a BaseException, so it is caught explicitly -- left
  alone it would take the worker down rather than show a message.

Nothing here raises.
"""

from __future__ import annotations

import importlib.util
import os
import re
import threading
from datetime import datetime
from pathlib import Path

SLIPPRINT_DIR = Path(os.environ.get("SLIPPRINT_DIR", Path.home() / "Dev" / "slipprint"))
SCRIPT = SLIPPRINT_DIR / "slipprint.py"

_lock = threading.Lock()
_mod = None
_mod_mtime = None

_NAME = re.compile(r"^slip_([a-z0-9]+)_(\d+)\.pdf$")


def _sp():
    """slipprint as a module, re-loaded if the file changed since last time."""
    global _mod, _mod_mtime
    mtime = SCRIPT.stat().st_mtime
    with _lock:
        if _mod is None or mtime != _mod_mtime:
            spec = importlib.util.spec_from_file_location("slipprint", SCRIPT)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            _mod, _mod_mtime = mod, mtime
        return _mod


def setup_problems() -> list[str]:
    if not SCRIPT.exists():
        return [f"slipprint is not installed at {SLIPPRINT_DIR} (or set SLIPPRINT_DIR)."]
    try:
        _sp()
    except Exception as e:
        return [f"slipprint.py failed to load: {e}"]
    return []


def clean_number(raw: str) -> str:
    """'#1088', ' 1088 ' -> '1088'. Empty string if it is not an order number."""
    n = (raw or "").strip().lstrip("#").strip()
    return n if n.isdigit() else ""


def stores() -> list[dict]:
    """Every store in slipprint's settings, and whether it is connected."""
    try:
        sp = _sp()
        settings = sp.load_settings()
    except Exception:
        return []
    out = []
    for key, s in settings["stores"].items():
        try:
            connected = sp.store_ctx(settings, key) is not None
        except BaseException:
            connected = False
        out.append({"key": key, "name": s.get("name", key), "connected": connected})
    return out


def printer() -> str | None:
    try:
        sp = _sp()
        return sp.find_printer(sp.load_settings())
    except Exception:
        return None


def _message(e: BaseException) -> str:
    text = str(e).strip()
    if isinstance(e, SystemExit) and not text:
        return "slipprint stopped without saying why."
    return text or e.__class__.__name__


def _build(number: str, key: str):
    """(settings, ctx, order, pdf_path, inhouse, pod). Raises on failure."""
    sp = _sp()
    settings = sp.load_settings()
    ctx = sp.need(settings, key)
    order = sp.find_order(ctx, number)
    pdf, inhouse, pod = sp.build_slip(order, settings, ctx)
    path = sp.write_pdf(ctx, order, pdf)
    return sp, settings, ctx, order, path, inhouse, pod


def _summary(ctx, order, inhouse, pod) -> dict:
    return {
        "store": ctx["key"],
        "store_name": ctx["name"],
        "order": order["name"],
        "number": order["name"].lstrip("#"),
        "cancelled": bool(order.get("cancelledAt")),
        "inhouse_lines": len(inhouse),
        "inhouse_items": sum(li["currentQuantity"] for li in inhouse),
        "pod_lines": len(pod),
    }


def preview(raw_number: str, key: str = "ss") -> dict:
    """Build the slip and save its PDF. Prints nothing."""
    number = clean_number(raw_number)
    if not number:
        return {"ok": False, "error": "Type an order number, like 1088."}
    try:
        _, _, ctx, order, path, inhouse, pod = _build(number, key)
    except BaseException as e:          # SystemExit included -- see module doc
        if isinstance(e, KeyboardInterrupt):
            raise
        return {"ok": False, "error": _message(e)}
    return {"ok": True, "path": str(path), **_summary(ctx, order, inhouse, pod)}


def print_slip(raw_number: str, key: str = "ss") -> dict:
    """Build the slip fresh from Shopify and send it to the label printer."""
    number = clean_number(raw_number)
    if not number:
        return {"ok": False, "error": "Type an order number, like 1088."}
    try:
        sp, settings, ctx, order, path, inhouse, pod = _build(number, key)
        queue, job = sp.send_to_printer(path, settings)
        sp.log(f"{ctx['name']} {order['name']}: manual print on {queue} ({job}) [onboarding app]")
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            raise
        return {"ok": False, "error": _message(e)}
    return {"ok": True, "queue": queue, "job": job, **_summary(ctx, order, inhouse, pod)}


def pdf_path(key: str, number: str) -> Path | None:
    """The saved slip for one order, if slipprint has made one."""
    number = clean_number(number)
    if not number or not re.fullmatch(r"[a-z0-9]+", key or ""):
        return None
    p = SLIPPRINT_DIR / "slips" / f"slip_{key}_{number}.pdf"
    return p if p.is_file() else None


def recent(limit: int = 25) -> list[dict]:
    """The newest slips slipprint has built, newest first."""
    folder = SLIPPRINT_DIR / "slips"
    names = {s["key"]: s["name"] for s in stores()}
    rows = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    for e in entries:
        m = _NAME.match(e.name)
        if not m or not e.is_file():
            continue
        rows.append({"store": m.group(1), "store_name": names.get(m.group(1), m.group(1)),
                     "number": m.group(2), "mtime": e.stat().st_mtime})
    rows.sort(key=lambda r: r["mtime"], reverse=True)
    for r in rows[:limit]:
        r["when"] = datetime.fromtimestamp(r["mtime"]).strftime("%b %-d, %-I:%M %p")
    return rows[:limit]


def view() -> dict:
    problems = setup_problems()
    return {
        "problems": problems,
        "stores": [] if problems else stores(),
        "printer": None if problems else printer(),
        "recent": [] if problems else recent(),
        "slipprint_dir": str(SLIPPRINT_DIR),
    }
