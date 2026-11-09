"""Today -- the tiles at the top of Home (doc 69, phase 1).

One screen that answers "what needs me right now?" across the whole business:
orders waiting to ship, leads waiting on a reply, agreements out, blanks on
order, Studio approvals, the giveback building up this quarter, sales so far
this month, and platform fees.

**This module reads saved files only.** Nothing here calls Shopify, S&S or the
Studio. Opening Home must be instant on a phone, and a page that blocks on a
network call is a page that looks broken whenever the network blinks. The
caches are written by the buttons that already exist and by the background
job in onboarding/refresh.py.

Every tile is built inside its own try/except. One bad file must cost one
tile, never the whole of Home -- Home is the page people open first, so it is
the last page that may fail.

A missing number is a dash and says why, never $0 (doc 06, decision 4).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import agreement, dashboard, leads, purchasing, shopify_sales, store

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache"
STUDIO_SUMMARY = CACHE / "studio_summary.json"
REFRESH_STATE = CACHE / "refresh.json"

# The customer-facing promise is "made to order, ships in 7-10 business days".
# Amber two days before the promise starts, red the day it does.
SHIP_AMBER_BUSINESS_DAYS = 5
SHIP_RED_BUSINESS_DAYS = 7

# Shopify's displayFulfillmentStatus. Anything not in DONE still needs work.
DONE_FULFILMENT = {"FULFILLED", "RESTOCKED"}

# An item sent for approval and untouched this long needs a nudge.
APPROVAL_RED_DAYS = 5

# Order of severity, for sorting and for the summary line.
RANK = {"red": 0, "amber": 1, "unknown": 2, "ok": 3}


# ------------------------------------------------------------------ helpers

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp) -> datetime | None:
    if not stamp:
        return None
    if isinstance(stamp, (int, float)):
        return datetime.fromtimestamp(float(stamp), timezone.utc)
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _local(stamp) -> str:
    """'Mon 9 Nov, 5:40 pm' in the hub's own time zone, or ''."""
    moment = _parse(stamp)
    if not moment:
        return ""
    local = moment.astimezone()
    return local.strftime("%a %-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def business_days_between(start: date, end: date) -> int:
    """Whole weekdays after `start` up to and including `end`. 0 if end <= start."""
    if end <= start:
        return 0
    days, current = 0, start
    while current < end:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}" if abs(value) >= 1000 else f"${value:,.2f}"


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _tile(key: str, title: str, **fields) -> dict:
    tile = {"key": key, "title": title, "value": "—", "sub": "", "state": "unknown",
            "link": "", "link_label": "", "external": False, "items": [], "note": ""}
    tile.update(fields)
    return tile


def _guard(key: str, title: str, build, *args) -> dict:
    """Build one tile; a crash becomes a tile that says so, not a 500."""
    try:
        return build(*args)
    except Exception as exc:          # pragma: no cover - the point is to never raise
        return _tile(key, title, state="unknown",
                     sub="Could not read this source.", note=f"{type(exc).__name__}: {exc}")


# ------------------------------------------------------------------ the tiles

def _open_orders(snap: dict | None) -> tuple[list[dict], bool]:
    """(orders still to ship, whether the cache carries fulfilment at all)."""
    if not snap:
        return [], False
    orders = [o for o in snap.get("orders") or [] if not o.get("test") and not o.get("cancelled")]
    has_field = any("fulfillment" in o for o in orders)
    if not has_field:
        return [], False
    return [o for o in orders
            if (o.get("fulfillment") or "").upper() not in DONE_FULFILMENT], True


def orders_tile(snap: dict | None, today: date) -> dict:
    tile = _tile("orders", "Orders to ship", link_label="Purchasing", link="purchasing_page")
    if not snap:
        tile["sub"] = "No Shopify data yet. Press Refresh now."
        return tile
    waiting, known = _open_orders(snap)
    if not known:
        tile["sub"] = "This copy of the orders predates fulfilment tracking. Press Refresh now once."
        return tile

    rows = []
    for order in waiting:
        placed = _parse(order.get("created_at"))
        age = business_days_between(placed.astimezone().date(), today) if placed else 0
        vendors = sorted({(l.get("vendor") or "").strip() for l in order.get("lines") or []} - {""})
        rows.append({"name": order.get("name", ""), "age": age,
                     "detail": f"{age} business day{'s' if age != 1 else ''} · " + (", ".join(vendors) or "—"),
                     "flag": "late" if age >= SHIP_RED_BUSINESS_DAYS else "",
                     "placed": _local(order.get("created_at")),
                     "who": ", ".join(vendors) or "—",
                     "status": (order.get("fulfillment") or "").replace("_", " ").lower()})
    rows.sort(key=lambda r: -r["age"])

    oldest = rows[0]["age"] if rows else 0
    tile.update(value=str(len(rows)), items=rows[:6])
    if not rows:
        tile.update(state="ok", sub="Nothing waiting to ship.")
    else:
        tile["sub"] = (f"Oldest {oldest} business day{'s' if oldest != 1 else ''} "
                       f"({rows[0]['name']}). Promise: ships in 7–10.")
        tile["state"] = ("red" if oldest >= SHIP_RED_BUSINESS_DAYS
                         else "amber" if oldest >= SHIP_AMBER_BUSINESS_DAYS else "ok")
    return tile


def leads_tile() -> dict:
    tile = _tile("leads", "Leads waiting on a reply", link="pipeline_page", link_label="Pipeline")
    if leads.load_cache() is None:
        tile["sub"] = "No pipeline data yet. Open Pipeline and press Refresh."
        return tile
    rows, _source = leads.list_leads(refresh=False, include_closed=False)
    stats = leads.summary(rows)
    overdue = [r for r in rows if r.get("overdue")]
    awaiting = [r for r in rows if not r.get("answered")]
    tile["value"] = str(stats["awaiting"])
    tile["items"] = [{"name": r.get("org_name") or r.get("name") or r.get("email") or "—",
                      "detail": (f"{r['days_open']} day{'s' if r['days_open'] != 1 else ''} open"
                                 if r.get("days_open") is not None else ""),
                      "flag": "overdue" if r.get("overdue") else
                              (f"held until {r['hold_label']}" if r.get("holding") else "")}
                     for r in (overdue + [r for r in awaiting if not r.get("overdue")])[:6]]
    if overdue:
        tile.update(state="red",
                    sub=f"{len(overdue)} past the one-business-day promise.")
    elif awaiting:
        held = stats.get("holding", 0)
        tile.update(state="amber",
                    sub=f"{len(awaiting)} not answered yet" + (f", {held} held on purpose." if held else "."))
    else:
        tile.update(state="ok", sub=f"All answered. {stats['open']} open in the pipeline.")
    return tile


def agreements_tile(records: list[dict]) -> dict:
    tile = _tile("agreements", "Agreements out", link="index", link_label="Partners")
    rows = agreement.outstanding(records)
    tile["value"] = str(len(rows))
    tile["items"] = [{"name": r.get("org_name", ""),
                      "detail": f"{r.get('days', 0)} days out",
                      "flag": "chase" if r.get("overdue") else ""} for r in rows[:6]]
    overdue = [r for r in rows if r.get("overdue")]
    if overdue:
        tile.update(state="red", sub=f"{len(overdue)} out more than "
                                    f"{agreement.CHASE_AFTER_DAYS} days — chase today.")
    elif rows:
        tile.update(state="ok", sub="Waiting on signatures, none overdue.")
    else:
        tile.update(state="ok", sub="Nothing waiting on a signature.")
    return tile


def blanks_tile() -> dict:
    tile = _tile("blanks", "Blanks on order", link="purchasing_page", link_label="Purchasing")
    snap = purchasing.load()
    if not snap:
        tile["sub"] = "Purchasing has not been refreshed yet."
        return tile
    prop = snap.get("proposal") or {}
    tracked = [t for t in prop.get("tracked") or [] if isinstance(t, dict)]
    awaiting = [t for t in tracked if t.get("status") != "received"]
    pieces = 0
    for t in awaiting:
        try:
            pieces += int(t.get("qty") or 0)
        except (TypeError, ValueError):
            pass
    to_buy = len(prop.get("lines") or [])

    status = purchasing.ss_status_cached()
    known = status.get("orders") if isinstance(status.get("orders"), dict) else {}
    arrived = [n for n, o in known.items() if isinstance(o, dict) and o.get("suggest_receive")]
    gone = [n for n, o in known.items() if isinstance(o, dict) and o.get("ledger_mismatch")]
    short = [n for n, o in known.items() if isinstance(o, dict) and o.get("short_shipped")]
    failed = bool(status) and not status.get("ok", True)

    tile["value"] = str(pieces)
    parts = [f"{pieces} piece{'s' if pieces != 1 else ''} awaited"]
    if to_buy:
        parts.append(f"{to_buy} line{'s' if to_buy != 1 else ''} still to order")
    tile["sub"] = "; ".join(parts) + "."
    items = []
    for n in gone:
        items.append({"name": f"S&S #{n}", "detail": "cancelled or not found", "flag": "fix"})
    for n in short:
        items.append({"name": f"S&S #{n}", "detail": "short-shipped", "flag": "check"})
    for n in arrived:
        items.append({"name": f"S&S #{n}", "detail": "looks arrived — mark received", "flag": ""})
    tile["items"] = items[:6]

    if gone or failed:
        tile["state"] = "red"
        if failed:
            tile["note"] = "The last S&S status check failed; showing the previous answer."
    elif arrived or short or to_buy:
        tile["state"] = "amber"
    else:
        tile["state"] = "ok"
    return tile


def studio_tile(studio_url: str) -> dict:
    tile = _tile("studio", "Studio approvals", link=studio_url, link_label="Open Studio",
                 external=True)
    data = _read_json(STUDIO_SUMMARY)
    if not data.get("ok"):
        tile["sub"] = ("The Studio summary has not been fetched yet."
                       if not data else "The last Studio check failed.")
        if data.get("error"):
            tile["note"] = data["error"]
        return tile
    counts = data.get("total") or {}
    sent = int(counts.get("sent") or 0)
    changes = int(counts.get("changes") or 0)
    approved = int(counts.get("approved") or 0)
    production = int(counts.get("production") or 0)
    oldest = data.get("oldest_sent_days")
    tile["value"] = str(sent)
    bits = [f"{sent} awaiting the customer"]
    if oldest is not None and sent:
        bits[0] += f" (oldest {oldest} day{'s' if oldest != 1 else ''})"
    if changes:
        bits.append(f"{changes} change request{'s' if changes != 1 else ''} to answer")
    bits.append(f"{approved} approved, {production} in production")
    tile["sub"] = "; ".join(bits) + "."
    tile["items"] = [{"name": label, "detail": str(int((data.get("brands") or {}).get(key, {}).get("sent") or 0))
                      + " awaiting customer", "flag": ""}
                     for key, label in (("ss", "Steeple & Stitch"), ("twc", "The Williams Collective"))
                     if (data.get("brands") or {}).get(key)]
    if oldest is not None and sent and oldest >= APPROVAL_RED_DAYS:
        tile["state"] = "red"
    elif changes:
        tile["state"] = "amber"
    else:
        tile["state"] = "ok"
    return tile


def giveback_tile(snap: dict | None, records: list[dict]) -> dict:
    tile = _tile("giveback", "Giveback owed this quarter", link="statements_page",
                 link_label="Statements")
    if not snap:
        tile["sub"] = "No Shopify data yet."
        return tile
    data = dashboard.rollup(snap, records)
    quarter = data.get("quarter") or dashboard.current_quarter()
    owed, uncosted, unrated, rows = 0.0, 0.0, [], []
    for partner in data.get("partners") or []:
        row = next((q for q in partner.get("quarters") or [] if q.get("quarter") == quarter), None)
        if not row or not row.get("revenue"):
            continue
        uncosted += row.get("uncosted_revenue") or 0
        if row.get("payout") is None:
            unrated.append(partner.get("org_name") or partner.get("vendor_label") or "?")
            continue
        owed += row["payout"]
        rows.append({"name": partner.get("org_name") or partner.get("vendor_label"),
                     "detail": _money(row["payout"]), "flag": ""})
    rows.sort(key=lambda r: r["name"] or "")
    tile.update(value=_money(owed), items=rows[:6],
                sub=f"{quarter}, on costed sales. Paid quarterly by check.")
    if uncosted or unrated:
        tile["state"] = "amber"
        notes = []
        if uncosted:
            notes.append(f"{_money(uncosted)} of sales has no cost yet, so its giveback is not counted")
        if unrated:
            notes.append("no rate on file for " + ", ".join(sorted(unrated)))
        tile["note"] = "; ".join(notes) + "."
    else:
        tile["state"] = "ok"
    return tile


def _sales_between(snap: dict, start: datetime, end: datetime) -> tuple[float, int]:
    total, count = 0.0, 0
    for order in snap.get("orders") or []:
        if order.get("test") or order.get("cancelled"):
            continue
        placed = _parse(order.get("created_at"))
        if not placed or not (start <= placed < end):
            continue
        count += 1
        for line in order.get("lines") or []:
            if line.get("unit_price") is not None:
                total += line["unit_price"] * (line.get("qty") or 0)
    return total, count


def sales_tile(snap: dict | None, now: datetime) -> dict:
    tile = _tile("sales", "Sales this month", link="dashboard_page", link_label="Dashboard")
    if not snap:
        tile["sub"] = "No Shopify data yet."
        return tile
    local = now.astimezone()
    month_start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_start = (month_start - timedelta(days=1)).replace(day=1)
    # Same point in last month: day-of-month clamped to that month's length.
    same_point = last_start + (local - month_start)
    if same_point > month_start:
        same_point = month_start
    this_total, this_count = _sales_between(snap, month_start, local + timedelta(seconds=1))
    last_total, last_count = _sales_between(snap, last_start, same_point)
    tile["value"] = _money(this_total)
    tile["state"] = "ok"
    if last_total:
        change = (this_total - last_total) / last_total
        tile["sub"] = (f"{this_count} order{'s' if this_count != 1 else ''}; "
                       f"{'up' if change >= 0 else 'down'} {abs(change):.0%} on the same point "
                       f"last month ({_money(last_total)}).")
    else:
        tile["sub"] = f"{this_count} order{'s' if this_count != 1 else ''} so far this month."
    tile["note"] = "Product sales after discounts, before shipping and tax."
    return tile


def fees_tile(records: list[dict]) -> dict:
    tile = _tile("fees", "Platform fees", link="dashboard_page", link_label="Billing")
    data = dashboard.fees(records)
    tile["value"] = _money(data["mrr_billed"]) + "/mo"
    behind = data["behind"]
    bits = [f"{_money(data['mrr_current'])}/mo paid up to date"]
    if data["unbilled_count"]:
        bits.append(f"{data['unbilled_count']} partner{'s' if data['unbilled_count'] != 1 else ''} "
                    f"not billed yet ({_money(data['mrr_unbilled'])}/mo)")
    if data["setup_open_count"]:
        bits.append(f"{_money(data['setup_open'])} in setup fees invoiced, unpaid")
    tile["sub"] = "; ".join(bits) + "."
    tile["items"] = [{"name": r["name"], "detail": f"{r['months_behind']} month"
                      f"{'s' if r['months_behind'] != 1 else ''} behind", "flag": "behind"}
                     for r in behind[:6]]
    tile["state"] = ("red" if behind else
                     "amber" if data["unbilled_count"] or data["setup_open_count"] else "ok")
    return tile


# ------------------------------------------------------------------ the page

def freshness(snap: dict | None) -> list[dict]:
    """When each source was last brought up to date, for the line under the tiles."""
    leads_snap = leads.load_cache() or {}
    status = purchasing.ss_status_cached()
    studio = _read_json(STUDIO_SUMMARY)
    rows = [
        ("Shopify orders", (snap or {}).get("pulled_at")),
        ("Leads", leads_snap.get("pulled_at")),
        ("S&S order status", status.get("at")),
        ("Studio", studio.get("at")),
    ]
    return [{"label": label, "when": _local(stamp) or "never"} for label, stamp in rows]


def tiles(studio_url: str = "", now: datetime | None = None) -> dict:
    """Everything the Today panel draws. Reads files only."""
    now = now or _now()
    today = now.astimezone().date()
    snap = shopify_sales.load_cache()
    records = store.list_partners()

    built = [
        _guard("orders", "Orders to ship", orders_tile, snap, today),
        _guard("leads", "Leads waiting on a reply", leads_tile),
        _guard("agreements", "Agreements out", agreements_tile, records),
        _guard("blanks", "Blanks on order", blanks_tile),
        _guard("studio", "Studio approvals", studio_tile, studio_url),
        _guard("giveback", "Giveback owed this quarter", giveback_tile, snap, records),
        _guard("sales", "Sales this month", sales_tile, snap, now),
        _guard("fees", "Platform fees", fees_tile, records),
    ]
    # Red first, then amber; within a colour keep the fixed order above, so a
    # tile does not jump about between visits for no reason.
    ordered = sorted(built, key=lambda t: RANK.get(t["state"], 9))
    job = _read_json(REFRESH_STATE)
    return {
        "tiles": ordered,
        "red": sum(1 for t in built if t["state"] == "red"),
        "amber": sum(1 for t in built if t["state"] == "amber"),
        "fresh": freshness(snap),
        "refresh": {
            "finished": _local(job.get("finished_at")),
            "running": bool(job.get("running")),
            "failed": [s["name"] for s in job.get("steps") or [] if not s.get("ok")],
        },
    }
