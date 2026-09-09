"""Roll Shopify orders up into per-partner sales, margin and payout owed.

The one rule this module exists to enforce: **a missing cost is not a zero
cost.** Shopify's `unitCost` is blank on a fair number of items -- deleted
variants, hand-created products, some POD imports. Treating a blank as $0 would
make margin equal revenue and inflate what a partner is owed by exactly the
cost of goods. Nobody would notice until a church asked how the number was
reached.

Every line lands in one of three buckets.

**Costed** -- the variant that sold still exists and carries a cost. Margin and
payout are facts.

**Estimated** -- the variant that sold has been DELETED from Shopify, so the
line points at nothing and no cost can be read, no matter what is entered on
the product afterwards. This is common here: the POD apps delete and recreate
variants. Sibling variants of the same product usually still carry costs, so
the line is estimated from their median and reported separately. Never folded
silently into the payout.

**Uncosted** -- the variant exists and simply has no cost entered. This one a
person can fix, and the screen says so.

Collapsing the last two would be the real error: it would tell Larry to go
enter a cost that can never attach to anything.

This is the same principle as `store.readiness()` refusing to let a 0% giveback
print as a negotiated term: a number that is quietly wrong is worse than a
number that is visibly missing.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from . import store

# Sales are historical, so they include products that have since been unlisted
# or archived -- "GCS Women's Weekend Fleece" sold $40 and is UNLISTED today.
# Filtering sales by product status would delete real revenue. ACTIVE-only
# belongs to the catalog panel and nowhere else.
CATALOG_STATUS = "ACTIVE"

# The POD original is duplicated as a " TWC" listing and the original is
# unlisted. Both carry the same vendor, so a catalog count that ignored status
# would show every garment twice.
DUPLICATE_SUFFIX = "TWC"


def _parse(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def quarter_of(moment: datetime) -> str:
    return f"{moment.year} Q{(moment.month - 1) // 3 + 1}"


def current_quarter(now: datetime | None = None) -> str:
    return quarter_of(now or datetime.now(timezone.utc))


# --------------------------------------------------------- vendor → partner --

def vendor_key(value: str) -> str:
    return " ".join((value or "").split()).casefold()


def explicit_vendor(record: dict) -> str:
    return vendor_key(record.get("shopify_vendor") or "")


def partner_vendors(record: dict) -> list[str]:
    """Every Shopify vendor string that should credit this partner.

    The join cannot assume the names match. The GCS record is called
    "Germantown Christian Schools"; the Shopify vendor is "Germantown Christian
    School", singular. `shopify_vendor` on the record is the explicit answer
    when it is set; the rest are guesses that happen to work today.
    """
    names = [
        record.get("shopify_vendor"),
        record.get("org_legal_name"),
        record.get("org_name"),
    ]
    seen: list[str] = []
    for name in names:
        key = vendor_key(name or "")
        if key and key not in seen:
            seen.append(key)
    return seen


def vendor_map(records: list[dict]) -> tuple[dict[str, dict], list[dict]]:
    """(vendor key -> partner, collisions).

    Two passes, and the order is the whole point. The GCS Athletics record
    inherited its `org_legal_name` from GCS, so both records guess the vendor
    "Germantown Christian School". A single pass with setdefault gave it to
    whichever record was iterated first -- alphabetically GCS Athletics -- and
    quietly moved $2,973.76 of Germantown's sales onto the wrong partner. It
    looked plausible on screen, which is what made it dangerous.

    So an explicit `shopify_vendor` always wins over a guess, and any key two
    records both guess is reported rather than silently assigned.
    """
    mapping: dict[str, dict] = {}
    claimed_explicitly: set[str] = set()

    for record in records:
        key = explicit_vendor(record)
        if key:
            mapping[key] = record
            claimed_explicitly.add(key)

    guessed: dict[str, list[dict]] = {}
    for record in records:
        for key in partner_vendors(record):
            if key in claimed_explicitly:
                continue
            guessed.setdefault(key, []).append(record)

    collisions = []
    for key, contenders in guessed.items():
        if len(contenders) == 1:
            mapping[key] = contenders[0]
            continue
        # Prefer the record whose own org_name is the match; a legal name
        # inherited from a parent organisation is the weaker claim.
        exact = [r for r in contenders if vendor_key(r.get("org_name", "")) == key]
        winner = exact[0] if len(exact) == 1 else contenders[0]
        mapping[key] = winner
        collisions.append({
            "vendor": key,
            "winner": winner.get("org_name", ""),
            "others": [r.get("org_name", "") for r in contenders if r is not winner],
            "resolved": bool(len(exact) == 1),
        })

    return mapping, collisions


# ------------------------------------------------------------------- rollup --

def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def product_costs(snapshot: dict) -> dict[str, float]:
    """product id -> a representative cost, from its costed variants.

    The median, not the first variant: costs vary inside a product here, and
    the first variant is an arbitrary one of them.
    """
    index: dict[str, float] = {}
    for product in snapshot.get("catalog", []):
        costs = product.get("unit_costs")
        if costs is None:
            single = product.get("unit_cost")
            costs = [single] if single is not None else []
        value = _median([c for c in costs if c is not None])
        if value is not None and product.get("id"):
            index[product["id"]] = value
    return index


def _blank_totals() -> dict:
    return {
        "revenue": 0.0, "units": 0, "orders": set(),
        "cost": 0.0, "costed_revenue": 0.0, "uncosted_revenue": 0.0,
        "uncosted_units": 0, "uncosted_titles": set(),
        "est_revenue": 0.0, "est_cost": 0.0, "est_titles": set(),
        "products": defaultdict(lambda: {
            "revenue": 0.0, "units": 0, "cost": 0.0, "costed": True}),
        "quarters": defaultdict(lambda: {
            "revenue": 0.0, "units": 0, "cost": 0.0,
            "costed_revenue": 0.0, "uncosted_revenue": 0.0}),
    }


def rollup(snapshot: dict, records: list[dict]) -> dict:
    """Per-partner sales, margin and payout, plus what could not be attributed."""
    mapping, collisions = vendor_map(records)
    costs_by_product = product_costs(snapshot)
    by_vendor: dict[str, dict] = defaultdict(_blank_totals)
    all_orders: set[str] = set()
    vendor_labels: dict[str, str] = {}
    skipped = {"test": 0, "cancelled": 0}

    for order in snapshot.get("orders", []):
        # Test orders and cancelled orders are not money. Refunds are handled
        # per line, because a partial refund leaves the order itself paid.
        if order.get("test"):
            skipped["test"] += 1
            continue
        if order.get("cancelled"):
            skipped["cancelled"] += 1
            continue

        moment = _parse(order.get("created_at", ""))
        quarter = quarter_of(moment) if moment else "unknown"

        for line in order.get("lines", []):
            qty = int(line.get("qty") or 0)
            if qty <= 0:          # fully refunded or removed
                continue
            price = line.get("unit_price")
            if price is None:
                continue

            key = vendor_key(line.get("vendor", ""))
            vendor_labels.setdefault(key, line.get("vendor") or "(no vendor)")
            bucket = by_vendor[key]

            revenue = price * qty
            bucket["revenue"] += revenue
            bucket["units"] += qty
            bucket["orders"].add(order["name"])
            all_orders.add(order["name"])
            bucket["quarters"][quarter]["revenue"] += revenue
            bucket["quarters"][quarter]["units"] += qty

            title = line.get("title") or "(untitled)"
            product = bucket["products"][title]
            product["revenue"] += revenue
            product["units"] += qty

            unit_cost = line.get("unit_cost")
            estimate = None
            if unit_cost is None and line.get("variant_missing"):
                # The variant that sold no longer exists, so its cost can
                # never be read again. Its siblings are the best available
                # answer, and it is an answer, not a fact.
                estimate = costs_by_product.get(line.get("product_id", ""))

            if unit_cost is None and estimate is not None:
                bucket["est_revenue"] += revenue
                bucket["est_cost"] += estimate * qty
                bucket["est_titles"].add(title)
                product["costed"] = False
            elif unit_cost is None:
                bucket["uncosted_revenue"] += revenue
                bucket["uncosted_units"] += qty
                bucket["uncosted_titles"].add(title)
                bucket["quarters"][quarter]["uncosted_revenue"] += revenue
                product["costed"] = False
            else:
                cost = unit_cost * qty
                bucket["cost"] += cost
                bucket["costed_revenue"] += revenue
                bucket["quarters"][quarter]["cost"] += cost
                bucket["quarters"][quarter]["costed_revenue"] += revenue
                product["cost"] += cost

    partners, unmatched = [], []
    for key, bucket in by_vendor.items():
        record = mapping.get(key)
        row = _finish(key, vendor_labels.get(key, key), bucket, record)
        (partners if record else unmatched).append(row)

    partners.sort(key=lambda r: r["revenue"], reverse=True)
    unmatched.sort(key=lambda r: r["revenue"], reverse=True)

    # A signed partner with no sales still belongs on the dashboard — a zero is
    # information, and a partner who quietly vanishes from the list is the one
    # nobody follows up on.
    listed = {r["partner_id"] for r in partners}
    for record in records:
        pid = store.partner_id(record)
        if pid not in listed:
            partners.append(_finish(
                partner_vendors(record)[0] if partner_vendors(record) else pid,
                record.get("org_name", pid), _blank_totals(), record))

    return {
        "partners": partners,
        "unmatched": unmatched,
        "catalog": catalog_health(snapshot, records),
        "skipped": skipped,
        "collisions": collisions,
        "totals": _totals(partners, unmatched, len(all_orders)),
        "quarter": current_quarter(),
    }


def _finish(key: str, label: str, bucket: dict, record: dict | None) -> dict:
    margin = bucket["costed_revenue"] - bucket["cost"]
    est_margin = bucket["est_revenue"] - bucket["est_cost"]
    pct = _margin_pct(record)
    payout = margin * pct / 100 if pct is not None else None
    payout_est = ((margin + est_margin) * pct / 100
                  if pct is not None else None)

    products = [
        {"title": title, **{k: v for k, v in totals.items()}}
        for title, totals in bucket["products"].items()
    ]
    products.sort(key=lambda p: p["revenue"], reverse=True)

    quarters = []
    for name, totals in sorted(bucket["quarters"].items(), reverse=True):
        q_margin = totals["costed_revenue"] - totals["cost"]
        quarters.append({
            "quarter": name,
            "revenue": round(totals["revenue"], 2),
            "units": totals["units"],
            "margin": round(q_margin, 2),
            "uncosted_revenue": round(totals["uncosted_revenue"], 2),
            "payout": round(q_margin * pct / 100, 2) if pct is not None else None,
        })

    return {
        "vendor_key": key,
        "vendor_label": label,
        "partner_id": store.partner_id(record) if record else "",
        "org_name": record.get("org_name") if record else label,
        "plan": record.get("plan", "") if record else "",
        "margin_pct": pct,
        "revenue": round(bucket["revenue"], 2),
        "units": bucket["units"],
        "orders": len(bucket["orders"]),
        "cost": round(bucket["cost"], 2),
        "margin": round(margin, 2),
        "payout": round(payout, 2) if payout is not None else None,
        "uncosted_revenue": round(bucket["uncosted_revenue"], 2),
        "uncosted_units": bucket["uncosted_units"],
        "uncosted_titles": sorted(bucket["uncosted_titles"]),
        "estimated_revenue": round(bucket["est_revenue"], 2),
        "estimated_margin": round(est_margin, 2),
        "estimated_titles": sorted(bucket["est_titles"]),
        "margin_with_estimates": round(margin + est_margin, 2),
        "payout_with_estimates": (round(payout_est, 2)
                                  if payout_est is not None else None),
        "products": products[:12],
        "quarters": quarters,
    }


def _margin_pct(record: dict | None) -> float | None:
    """The negotiated giveback, or None when it is still the 0% seed value.

    0 is `default_record()`'s placeholder, not a real term. Returning 0.0 here
    would print "$0.00 owed" for a partner whose rate simply has not been
    entered yet -- indistinguishable from a partner who genuinely owes nothing.
    """
    if not record:
        return None
    raw = str(record.get("margin_pct", "")).strip().rstrip("%")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _totals(partners: list[dict], unmatched: list[dict],
            order_count: int) -> dict:
    def add(rows, field):
        return round(sum(row[field] or 0 for row in rows), 2)
    return {
        "revenue": add(partners + unmatched, "revenue"),
        "partner_revenue": add(partners, "revenue"),
        "unmatched_revenue": add(unmatched, "revenue"),
        "margin": add(partners, "margin"),
        "payout": round(sum(r["payout"] or 0 for r in partners), 2),
        "payout_with_estimates": round(
            sum(r["payout_with_estimates"] or 0 for r in partners), 2),
        "estimated_revenue": add(partners + unmatched, "estimated_revenue"),
        "uncosted_revenue": add(partners + unmatched, "uncosted_revenue"),
        "units": sum(row["units"] for row in partners + unmatched),
        # Distinct, not the sum of the per-partner counts. Order #1002 carried
        # both a Revive tee and a GCS fleece, so summing the columns reports
        # more orders than the store took.
        "orders": order_count,
    }


# ------------------------------------------------------------------ catalog --

def catalog_health(snapshot: dict, records: list[dict]) -> dict:
    """What is actually live per partner, and what is misfiled.

    ACTIVE-only, because the POD original and its " TWC" duplicate share a
    vendor and only one of the pair is ever live. Counting every status would
    report a store as twice the size it is.

    Products are attributed through the SAME resolved mapping the sales rollup
    uses -- never through `partner_vendors()` directly. Walking each record's
    guesses independently let GCS Athletics claim all 20 of Germantown's live
    products, because its inherited legal name is one of its guesses. One
    mapping, one answer, or the two panels disagree with each other.
    """
    mapping, _ = vendor_map(records)
    by_partner: dict[str, list[dict]] = defaultdict(list)
    orphans: list[dict] = []
    live_total = 0

    for product in snapshot.get("catalog", []):
        if product.get("status") != CATALOG_STATUS:
            continue
        live_total += 1
        record = mapping.get(vendor_key(product.get("vendor", "")))
        if record is None:
            orphans.append(product)
            continue
        by_partner[store.partner_id(record)].append(product)

    rows = []
    for record in records:
        items = by_partner.get(store.partner_id(record), [])
        rows.append({
            "partner_id": store.partner_id(record),
            "org_name": record.get("org_name", ""),
            "live": len(items),
            "no_cost": sum(1 for p in items if p.get("unit_cost") is None),
            "twc": sum(1 for p in items
                       if p["title"].rstrip().upper().endswith(DUPLICATE_SUFFIX)),
        })
    rows.sort(key=lambda r: r["live"], reverse=True)

    return {
        "rows": rows,
        "orphans": sorted(orphans, key=lambda p: (p["vendor"], p["title"])),
        "no_cost_count": sum(
            1 for items in by_partner.values()
            for p in items if p.get("unit_cost") is None),
        "live_total": live_total,
        "orphan_count": len(orphans),
    }
