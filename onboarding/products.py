"""One partner's Shopify products, read from the saved catalog (doc 71).

The products are NOT copied into the partner record. The record is the
partner's own facts with its own history (doc 01); the catalog belongs to
Shopify and changes every time a product is edited. So this reads the cache the
background refresh already keeps (cache/shopify_sales.json, every 15 minutes)
and matches products to partners through dashboard.vendor_map() -- the same
mapping as the Dashboard, Statements and Programs, so a product is never shown
under two partners, and a vendor two records both guess at is reported rather
than handed to either.

Sales per product come from the same cache's orders: units are
currentQuantity (refunds out), money is the after-all-discounts price -- the
statement maths, excluding test and cancelled orders.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from . import dashboard, shopify_sales, store

STOREFRONT = os.environ.get("STOREFRONT_URL", "https://www.steepleandstitch.com").rstrip("/")
STATUS_ORDER = {"ACTIVE": 0, "DRAFT": 1, "ARCHIVED": 2}
STATUS_LABEL = {"ACTIVE": "Live", "DRAFT": "Draft", "ARCHIVED": "Archived"}


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    sign, value = ("-" if value < 0 else ""), abs(value)
    return sign + (f"${value:,.0f}" if value >= 1000 else f"${value:,.2f}")


def _price(p: dict) -> str:
    lo, hi = p.get("price_min"), p.get("price_max")
    if lo is None:
        return "—"
    if hi is None or abs(hi - lo) < 0.005:
        return _money(lo)
    return f"{_money(lo)}–{_money(hi)}"


def _day(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%b %-d, %Y")
    except (ValueError, AttributeError):
        return "—"


def _numeric(gid: str) -> str:
    return str(gid or "").rsplit("/", 1)[-1]


def _sales(snap: dict) -> dict[str, dict]:
    """{product gid: {"units", "revenue", "last"}} across every cached order."""
    out: dict[str, dict] = {}
    for order in snap.get("orders") or []:
        if order.get("test") or order.get("cancelled"):
            continue
        for line in order.get("lines") or []:
            gid = line.get("product_id")
            qty = int(line.get("qty") or 0)
            if not gid or qty <= 0:
                continue
            row = out.setdefault(gid, {"units": 0, "revenue": 0.0, "last": ""})
            row["units"] += qty
            if line.get("unit_price") is not None:
                row["revenue"] += qty * float(line["unit_price"])
            row["last"] = max(row["last"], order.get("created_at") or "")
    return out


def for_partner(pid: str, status: str = "all") -> dict | None:
    """Everything /partner/<pid>/products draws, or None if no such partner."""
    record = store.load(pid)
    if record is None:
        return None
    snap = shopify_sales.load_cache() or {}
    base = {"record": record, "pid": pid, "have_shopify": bool(snap),
            "pulled_at": _day(snap.get("pulled_at") or "") if snap else "—",
            "products": [], "counts": {"ACTIVE": 0, "DRAFT": 0, "ARCHIVED": 0},
            "units": 0, "revenue": "—", "no_cost": 0, "vendors": [], "collision": "",
            "status": status, "shop": os.environ.get("SHOPIFY_STORE", "")}
    if not snap:
        return base

    records = [r for r in store.list_partners() if not r.get("_seed_check")]
    mapping, collisions = dashboard.vendor_map(records)
    mine = {key for key, rec in mapping.items() if store.partner_id(rec) == pid}
    base["vendors"] = sorted({p.get("vendor", "") for p in snap.get("catalog") or []
                              if dashboard.vendor_key(p.get("vendor", "")) in mine})
    name = record.get("org_name", "")
    for c in collisions:
        involved = [c.get("winner", "")] + list(c.get("others") or [])
        if name in involved and not c.get("resolved"):
            # Two records guess the same vendor: products went to one of them,
            # possibly the wrong one. Setting shopify_vendor settles it.
            base["collision"] = " and ".join(n for n in involved if n)
    sales = _sales(snap)
    shop = base["shop"]

    rows = []
    for p in snap.get("catalog") or []:
        if dashboard.vendor_key(p.get("vendor", "")) not in mine:
            continue
        st = (p.get("status") or "").upper()
        base["counts"][st] = base["counts"].get(st, 0) + 1
        sold = sales.get(p.get("id"), {"units": 0, "revenue": 0.0, "last": ""})
        cost = p.get("unit_cost")
        margin = None
        if cost is not None and p.get("price_min") is not None:
            margin = p["price_min"] - cost
        if st == "ACTIVE" and cost is None:
            base["no_cost"] += 1
        rows.append({
            "id": p.get("id"), "title": p.get("title") or "—", "status": st,
            "status_label": STATUS_LABEL.get(st, st.title() or "—"),
            "type": p.get("type") or "", "image": p.get("image") or "",
            "variants": p.get("variants"),
            "price": _price(p), "cost": _money(cost), "margin": _money(margin),
            "margin_bad": margin is not None and margin <= 0,
            "units": sold["units"], "revenue": _money(sold["revenue"]) if sold["units"] else "—",
            "last_sold": _day(sold["last"]) if sold["last"] else "—",
            "updated": _day(p.get("updated_at") or ""),
            "admin_url": f"https://{shop}/admin/products/{_numeric(p.get('id'))}" if shop and p.get("id") else "",
            "store_url": f"{STOREFRONT}/products/{p['handle']}" if st == "ACTIVE" and p.get("handle") else "",
            "_sort": (STATUS_ORDER.get(st, 3), -sold["units"], (p.get("title") or "").casefold()),
        })
        base["units"] += sold["units"]
    total = sum(sales.get(r["id"], {}).get("revenue", 0.0) for r in rows)
    base["revenue"] = _money(total) if base["units"] else "—"
    rows.sort(key=lambda r: r["_sort"])
    want = status.upper()
    base["products"] = [r for r in rows if want == "ALL" or r["status"] == want]
    base["total"] = len(rows)
    return base
