"""The orders board (doc 69, phase 2) -- replaces the Reminders board.

One card per open order, in the lane it has actually reached:

    New -> Blanks ordered -> Blanks in -> Pressed -> Packed -> Shipped / picked up

Where each lane comes from, and why:

* **New, Shipped** -- Shopify (the Dashboard's order cache, which now carries
  `fulfillment`). Shopify is the only authority on whether an order has gone.
* **Blanks ordered / Blanks in** -- the Purchasing proposal ssorder writes
  (cache/purchasing/snapshot.json). It already records, per Shopify order line,
  whether the blank is still to buy, ordered, or received. Nothing is re-asked.
* **Pressed, Packed** -- a person's tick on this board. Nothing else knows.
  (Phase 4 will let the Studio's Press Station set Pressed itself.)

A lane is never *chosen*: it is the furthest point the evidence reaches, so a
tick cannot hide something real (an order Shopify says has shipped is shipped,
whatever was ticked) and a stale tick cannot pull an order backwards.

Only in-house lines -- titles ending " TWC", ssorder's own rule -- need blanks,
a press and a pack. An order with none is printed by a POD partner and sits in
its own lane until Shopify says it has gone.

Reads saved files only, like Home. The ticks are the one thing it writes, in
partners/_orders/ticks.json, which the nightly backup already covers.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import purchasing, shopify_sales, twc_orders
from .store import PARTNERS

TICKS_DIR = PARTNERS / "_orders"
TICKS = TICKS_DIR / "ticks.json"

INHOUSE_SUFFIX = " TWC"          # ssorder config: inhouse_title_suffix
DONE_FULFILMENT = {"FULFILLED", "RESTOCKED"}
SHOW_DONE_DAYS = 14              # finished orders placed this recently stay visible
# Shopify's displayFinancialStatus values that mean "not paid in full yet"
# (3.90): an invoice paid by bank transfer, Zelle or a check sits PENDING
# until Larry marks it paid. ssorder buys no blanks for these either.
PAYMENT_DUE = {"PENDING": "Awaiting payment", "PARTIALLY_PAID": "Part paid",
               "AUTHORIZED": "Payment authorised", "EXPIRED": "Payment expired",
               "UNPAID": "Awaiting payment"}


def payment_due(order: dict) -> str:
    return PAYMENT_DUE.get((order.get("status") or "").upper(), "")


def manual_source(order: dict) -> str:
    """'email' / 'text' / ... for a Portal manual order (its lines carry _source)."""
    for line in order.get("lines") or []:
        src = (line.get("attrs") or {}).get("_source")
        if src:
            return src
    return ""

LANES = [
    ("new", "New", "Blanks still to buy, or nothing bought yet."),
    ("ordered", "Blanks ordered", "Every blank is bought; waiting for them to arrive."),
    ("in", "Blanks in", "Every blank has arrived. Ready to press."),
    ("pressed", "Pressed", "Printed. Ready to pack."),
    ("packed", "Packed", "Packed, waiting for the carrier or the pickup."),
]
LANE_KEYS = [key for key, _label, _blurb in LANES]
TICKABLE = ("pressed", "packed")
RANK = {key: i for i, key in enumerate(LANE_KEYS)}

# Purchasing line statuses that mean "the blank is in the building".
RECEIVED = {"received"}


# ------------------------------------------------------------------ helpers

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(stamp) -> datetime | None:
    if not stamp:
        return None
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def business_days(start: datetime | None, now: datetime) -> int | None:
    if not start:
        return None
    a, b = start.astimezone().date(), now.astimezone().date()
    days, current = 0, a
    while current < b:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days += 1
    return days


def _local(stamp) -> str:
    moment = _parse(stamp)
    if not moment:
        return ""
    return moment.astimezone().strftime("%a %-d %b, %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def business_of(store: str) -> str:
    """'ss' or 'twc' from an ssorder store name. Anything not Steeple & Stitch
    is The Williams Collective's own shop."""
    return "ss" if "steeple" in (store or "").casefold() else "twc"


def delivery(order: dict, locations: list[str]) -> str:
    """'pickup', 'ship' or '' (unknown).

    Shopify titles a local pickup's shipping line with the pickup location's
    name, the same signal slipprint uses. Matched against the store's own
    location names, plus the word "pickup" for safety.
    """
    title = (order.get("delivery") or "").strip()
    if not title:
        return ""
    folded = title.casefold()
    names = {name.casefold() for name in locations or []}
    if folded in names or "pickup" in folded or "pick up" in folded or "pick-up" in folded:
        return "pickup"
    return "ship"


# ------------------------------------------------------------------ ticks

@contextlib.contextmanager
def _locked():
    TICKS_DIR.mkdir(parents=True, exist_ok=True)
    with open(TICKS_DIR / ".lock", "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load_ticks() -> dict:
    try:
        data = json.loads(TICKS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_ticks(data: dict) -> None:
    tmp = TICKS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, TICKS)


def order_key(business: str, name: str) -> str:
    return f"{business}|{name}"


def tick(key: str, step: str, by: str = "", undo: bool = False) -> tuple[bool, str]:
    """Record (or clear) Pressed / Packed for one order. Returns (ok, message)."""
    if step not in TICKABLE:
        return False, "Only Pressed and Packed are ticked by hand."
    if "|" not in key or not key.split("|", 1)[1]:
        return False, "That order reference is not recognised."
    with _locked():
        data = load_ticks()
        entry = data.setdefault(key, {})
        if undo:
            if step not in entry:
                return False, "Nothing to undo."
            entry.pop(step)
            if step == "pressed":
                entry.pop("packed", None)      # cannot be packed if it was never pressed
            if not entry:
                data.pop(key, None)
            _save_ticks(data)
            return True, f"{key.split('|', 1)[1]}: {step} cleared."
        entry[step] = {"at": _now().isoformat(timespec="seconds"), "by": by}
        if step == "packed" and "pressed" not in entry:
            # Packing something means it was pressed. Record it so the card
            # does not read as packed-but-not-pressed.
            entry["pressed"] = dict(entry["packed"], implied=True)
        _save_ticks(data)
        return True, f"{key.split('|', 1)[1]} marked {step}."


# ------------------------------------------------------------------ the board

def _purchasing_by_order(prop: dict) -> dict:
    """{(business, order name): {"to_buy", "statuses", "boxes", "store"}} from
    the Purchasing proposal."""
    out: dict = {}

    def slot(store: str, name: str) -> dict:
        return out.setdefault((business_of(store), name),
                              {"to_buy": 0, "statuses": [], "boxes": set(), "store": store})

    for line in prop.get("lines") or []:
        for src in (line or {}).get("sources") or []:
            if src.get("order"):
                key = src.get("key", "")
                store = key.split("|", 1)[0] if "|" in key else ""
                slot(store, src["order"])["to_buy"] += 1
    for group in prop.get("other_suppliers") or []:
        for line in (group or {}).get("lines") or []:
            if line.get("order"):
                slot(line.get("store", ""), line["order"])["to_buy"] += 1
    for row in prop.get("tracked") or []:
        if not isinstance(row, dict) or row.get("store") == "manual" or not row.get("order"):
            continue
        s = slot(row.get("store", ""), row["order"])
        s["statuses"].append(row.get("status") or "")
        if row.get("ss_order"):
            s["boxes"].add(f"S&S #{row['ss_order']}")
        elif row.get("source") == "other" and row.get("supplier"):
            s["boxes"].add(row["supplier"])
        elif row.get("status") == "sourced":
            s["boxes"].add("bought by hand")
    return out


def _blank_lane(info: dict | None) -> tuple[str, str]:
    """(lane, why) from the purchasing evidence alone."""
    if not info or (not info["to_buy"] and not info["statuses"]):
        return "new", "Not in Purchasing yet — refresh Purchasing."
    if info["to_buy"]:
        return "new", f"{info['to_buy']} line{'s' if info['to_buy'] != 1 else ''} still to buy"
    received = sum(1 for s in info["statuses"] if s in RECEIVED)
    if received == len(info["statuses"]):
        return "in", "All blanks received"
    return "ordered", f"{received} of {len(info['statuses'])} blanks received"


def board(business: str = "", partner: str = "", now: datetime | None = None) -> dict:
    """Everything /orders draws. Reads files only."""
    now = now or _now()
    snap = shopify_sales.load_cache() or {}
    purchase = purchasing.load() or {}
    prop = purchase.get("proposal") or {}
    by_order = _purchasing_by_order(prop)
    ticks = load_ticks()
    locations = snap.get("locations") or []
    # .env is read lazily; without this the first page after a restart has no
    # store domain and no order links (found by check_orders on the hub).
    shopify_sales._load_env()
    shop = os.environ.get("SHOPIFY_STORE", "")
    has_fulfilment = any("fulfillment" in o for o in snap.get("orders") or [])

    cards, done, pod = [], [], []
    seen = set()
    for order in snap.get("orders") or []:
        if order.get("test") or order.get("cancelled"):
            continue
        name = order.get("name", "")
        key = order_key("ss", name)
        seen.add(("ss", name))
        lines = order.get("lines") or []
        inhouse = [l for l in lines if (l.get("title") or "").endswith(INHOUSE_SUFFIX)]
        vendors = sorted({(l.get("vendor") or "").strip() for l in lines} - {""})
        fulfil = (order.get("fulfillment") or "").upper()
        placed = _parse(order.get("created_at"))
        card = {
            "key": key, "name": name, "business": "ss",
            "placed": _local(order.get("created_at")),
            "age": business_days(placed, now),
            "who": ", ".join(vendors) or "—", "vendors": vendors,
            "item_count": sum(int(l.get("qty") or 0) for l in lines),
            "inhouse_items": sum(int(l.get("qty") or 0) for l in inhouse),
            "delivery": delivery(order, locations),
            "delivery_title": order.get("delivery") or "",
            "fulfillment": fulfil.replace("_", " ").lower(),
            "admin_url": (f"https://{shop}/admin/orders/{order['id']}"
                          if shop and order.get("id") else ""),
            "ticks": ticks.get(key, {}),
            "boxes": [], "why": "",
            "payment": payment_due(order), "manual": manual_source(order),
        }
        if not has_fulfilment:
            continue                    # an old cache cannot say what is open; the page says so
        if fulfil in DONE_FULFILMENT:
            if placed and (now - placed).days <= SHOW_DONE_DAYS:
                card["lane"] = "done"
                done.append(card)
            continue
        if not inhouse:
            card["lane"] = "pod"
            card["why"] = "Printed by a POD partner — no blanks, press or pack here."
            pod.append(card)
            continue
        info = by_order.get(("ss", name))
        lane, why = _blank_lane(info)
        if info:
            card["boxes"] = sorted(info["boxes"])
        for step in TICKABLE:
            if step in card["ticks"] and RANK[step] > RANK[lane]:
                lane = step
        card.update(lane=lane, why=why)
        cards.append(card)

    # The Williams Collective's own store, pulled directly since 3.90
    # (twc_orders): paid/unpaid, shipped/picked up and every order -- not only
    # the ones ssorder has listed. Everything TWC sells is made in-house.
    twc = twc_orders.load() or {}
    twc_dom = twc.get("domain") or ""
    twc_locs = twc.get("locations") or []
    for order in twc.get("orders") or []:
        if order.get("test") or order.get("cancelled"):
            continue
        name = order.get("name", "")
        key = order_key("twc", name)
        seen.add(("twc", name))
        lines = [l for l in order.get("lines") or [] if int(l.get("qty") or 0) > 0]
        vendors = sorted({(l.get("vendor") or "").strip() for l in lines} - {""})
        fulfil = (order.get("fulfillment") or "").upper()
        placed = _parse(order.get("created_at"))
        items = sum(int(l.get("qty") or 0) for l in lines)
        card = {"key": key, "name": name, "business": "twc",
                "placed": _local(order.get("created_at")), "age": business_days(placed, now),
                "who": ", ".join(vendors) or "TWC shop", "vendors": vendors,
                "item_count": items, "inhouse_items": items,
                "delivery": delivery(order, twc_locs), "delivery_title": order.get("delivery") or "",
                "fulfillment": fulfil.replace("_", " ").lower(),
                "admin_url": (f"https://{twc_dom}/admin/orders/{order['id']}"
                              if twc_dom and order.get("id") else ""),
                "ticks": ticks.get(key, {}), "boxes": [], "why": "",
                "payment": payment_due(order), "manual": manual_source(order)}
        if not lines:
            continue
        if fulfil in DONE_FULFILMENT:
            if placed and (now - placed).days <= SHOW_DONE_DAYS:
                card["lane"] = "done"
                done.append(card)
            continue
        info = by_order.get(("twc", name)) or by_order.get(("twc", name.replace("TWC ", "")))
        lane, why = _blank_lane(info)
        if card["payment"] and not info:
            why = "Waiting for payment — blanks are bought once it is paid."
        if info:
            card["boxes"] = sorted(info["boxes"])
        for step in TICKABLE:
            if step in card["ticks"] and RANK[step] > RANK[lane]:
                lane = step
        card.update(lane=lane, why=why)
        cards.append(card)

    # The Williams Collective's own shop: the Dashboard does not pull it, but
    # Purchasing does, so its open in-house orders still get a card. Shopify's
    # fulfilment is not known for these; they leave the board when ssorder
    # stops listing them (it lists only unfulfilled lines).
    for (biz, name), info in sorted(by_order.items()):
        if biz != "twc" or (biz, name) in seen:
            continue
        key = order_key(biz, name)
        lane, why = _blank_lane(info)
        entry = ticks.get(key, {})
        for step in TICKABLE:
            if step in entry and RANK[step] > RANK[lane]:
                lane = step
        created = next((r.get("order_created") for r in prop.get("tracked") or []
                        if isinstance(r, dict) and r.get("order") == name
                        and business_of(r.get("store", "")) == biz), "")
        placed = _parse(created)
        cards.append({"key": key, "name": name, "business": biz, "placed": _local(created),
                      "age": business_days(placed, now), "who": info.get("store") or "TWC shop",
                      "vendors": [], "item_count": None, "inhouse_items": None,
                      "delivery": "", "delivery_title": "", "fulfillment": "",
                      "admin_url": "", "ticks": entry, "boxes": sorted(info["boxes"]),
                      "why": why, "lane": lane, "payment": "", "manual": ""})

    # Filters, applied after the cards are built so the counts can show what is hidden.
    def keep(card: dict) -> bool:
        if business and card["business"] != business:
            return False
        if partner and partner not in card["vendors"]:
            return False
        return True

    partners = sorted({v for c in cards + done + pod for v in c["vendors"]})
    shown = [c for c in cards if keep(c)]
    lanes = []
    for lane_key, label, blurb in LANES:
        members = sorted((c for c in shown if c["lane"] == lane_key),
                         key=lambda c: -(c["age"] or 0))
        lanes.append({"key": lane_key, "label": label, "blurb": blurb, "cards": members})
    recent_done = sorted((c for c in done if keep(c)), key=lambda c: c["name"], reverse=True)[:40]

    return {
        "lanes": lanes,
        "pod": sorted((c for c in pod if keep(c)), key=lambda c: -(c["age"] or 0)),
        "done": recent_done,
        "open_count": len(shown),
        "late": sum(1 for c in shown if (c["age"] or 0) >= 7),
        "unpaid": sum(1 for c in shown if c.get("payment")),
        "twc_at": _local(twc.get("pulled_at")),
        "partners": partners,
        "business": business, "partner": partner,
        "has_fulfilment": has_fulfilment,
        "has_delivery": any("delivery" in o for o in snap.get("orders") or []),
        "orders_at": _local(snap.get("pulled_at")),
        "purchasing_at": _local(purchase.get("at")),
        "purchasing_ok": bool(purchase),
        "ticks_seen": len(ticks),
    }
