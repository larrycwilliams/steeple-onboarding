"""The quarterly payout statement a partner receives with their check.

The dashboard answers "what do I owe everyone". This module answers the
question the partner asks when the envelope arrives: *what did we sell, and
how did you arrive at this number.* Same figures, different audience — and
that difference is the whole design.

**Nothing here recomputes the arithmetic.** The vendor→partner mapping, the
per-product cost index and the three cost buckets all come from
`dashboard.py`, because a statement that disagreed with the dashboard by a
cent would be worse than having no statement at all: the partner has the
paper, Larry has the screen, and there would be no way to tell which one was
wrong. If the rules change, they change in one file and both follow.

What this module adds is the per-quarter detail the dashboard rolls away:
which orders, which items, how many of each. A church treasurer reconciling a
deposit needs the line items; a dashboard tile cannot carry them.

Three things it refuses to do.

**It will not state a payout without a negotiated rate.** `margin_pct` seeds
at 0, which would print "$0.00 owed" over a signature — indistinguishable
from a partner who genuinely earned nothing. Same guard as
`store.readiness()`.

**It will not quietly include estimated costs in the payable figure.** Lines
whose variant was deleted from Shopify are priced from the median of the
product's surviving variants. That is an answer, not a fact, so it is
reported as its own total and the partner is told the basis.

**It will not hide revenue it could not cost.** Items sold with no cost on
file contribute nothing to margin and nothing to the payout. Dropping them
from the statement would make the item list disagree with the store's own
order history, which is the one document the partner can check independently.
They are listed, marked, and excluded from the total — visibly.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, timezone

from . import dashboard, store

# Where a statement says the money is coming from. Read from terms.json by the
# caller when it wants the live value; this is only the fallback wording.
PAYMENT_METHOD = "check"


# ------------------------------------------------------------- quarters ----

def slug(quarter: str) -> str:
    """'2026 Q3' -> '2026-Q3', so it can live in a URL and a filename."""
    return (quarter or "").strip().replace(" ", "-")


def unslug(value: str) -> str:
    """'2026-Q3' -> '2026 Q3'. Returns '' for anything that is not a quarter.

    Validated rather than merely un-hyphenated: this value arrives from a URL,
    and it is used to build a filename.
    """
    text = (value or "").strip().replace("-", " ")
    return text if re.fullmatch(r"\d{4} Q[1-4]", text) else ""


def bounds(quarter: str) -> tuple[date, date]:
    year, part = quarter.split(" Q")
    year, part = int(year), int(part)
    first = (part - 1) * 3 + 1
    last = first + 2
    return (date(year, first, 1),
            date(year, last, calendar.monthrange(year, last)[1]))


def period_label(quarter: str) -> str:
    start, end = bounds(quarter)
    return (f"{start.strftime('%-d %B %Y')} to {end.strftime('%-d %B %Y')}")


def quarters_with_sales(snapshot: dict) -> list[str]:
    """Every quarter that has at least one real order line, newest first."""
    found: set[str] = set()
    for order in snapshot.get("orders", []):
        if order.get("test") or order.get("cancelled"):
            continue
        moment = _parse(order.get("created_at", ""))
        if moment:
            found.add(dashboard.quarter_of(moment))
    return sorted(found, reverse=True)


def last_complete_quarter(now: datetime | None = None) -> str:
    """The quarter a payout would actually be for.

    Defaulting the picker to the *current* quarter would be defaulting it to
    the one that cannot be paid yet — the money is still arriving. Payouts
    follow the quarter that has closed.
    """
    moment = now or datetime.now(timezone.utc)
    part = (moment.month - 1) // 3 + 1
    return f"{moment.year} Q{part - 1}" if part > 1 else f"{moment.year - 1} Q4"


def default_quarter(snapshot: dict, now: datetime | None = None) -> str:
    """The quarter to open on: the last closed one if it sold anything.

    Falling back to the newest quarter with sales matters for a store this
    young — a partner who launched in the current quarter has no closed
    quarter at all, and opening on an empty statement looks like a bug.
    """
    available = quarters_with_sales(snapshot)
    closed = last_complete_quarter(now)
    if closed in available:
        return closed
    return available[0] if available else dashboard.current_quarter(now)


def _parse(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


# -------------------------------------------------------------- building ----

def _blank_item() -> dict:
    return {"units": 0, "revenue": 0.0, "cost": 0.0, "revenue_costed": 0.0,
            "units_costed": 0, "units_estimated": 0, "units_uncosted": 0,
            "estimated_cost": 0.0, "orders": set()}


def build(snapshot: dict, records: list[dict], record: dict,
          quarter: str, cross_check: dict | None = None) -> dict:
    """One partner, one quarter: orders, items by quantity, and what is owed.

    `cross_check` is the matching row out of `dashboard.rollup()`'s `quarters`
    list for this partner. When it is supplied the statement compares itself
    against it and reports any disagreement rather than leaving two numbers on
    two screens for someone to notice later. See `checked` in the result.
    """
    mapping, _ = dashboard.vendor_map(records)
    costs_by_product = dashboard.product_costs(snapshot)
    pct = dashboard.negotiated_pct(record)
    pid = store.partner_id(record)

    items: dict[str, dict] = {}
    orders: dict[str, dict] = {}
    revenue = cost = costed_revenue = 0.0
    est_revenue = est_cost = uncosted_revenue = 0.0
    units = uncosted_units = 0

    for order in snapshot.get("orders", []):
        # Identical exclusions to the dashboard. A test order is not money and
        # a cancelled one was never collected; a partial refund is handled per
        # line, because the order itself stays paid.
        if order.get("test") or order.get("cancelled"):
            continue
        moment = _parse(order.get("created_at", ""))
        if not moment or dashboard.quarter_of(moment) != quarter:
            continue

        for line in order.get("lines", []):
            qty = int(line.get("qty") or 0)
            if qty <= 0:               # fully refunded or removed
                continue
            price = line.get("unit_price")
            if price is None:
                continue
            # Credited by the product's CURRENT vendor, never the value frozen
            # on the line at sale time -- see shopify_sales.py.
            owner = mapping.get(dashboard.vendor_key(line.get("vendor", "")))
            if owner is None or store.partner_id(owner) != pid:
                continue

            title = line.get("title") or "(untitled)"
            item = items.setdefault(title, _blank_item())
            line_revenue = price * qty

            item["units"] += qty
            item["revenue"] += line_revenue
            item["orders"].add(order["name"])
            revenue += line_revenue
            units += qty

            row = orders.setdefault(order["name"], {
                "name": order["name"], "date": moment.date(),
                "units": 0, "revenue": 0.0})
            row["units"] += qty
            row["revenue"] += line_revenue

            unit_cost = line.get("unit_cost")
            estimate = (costs_by_product.get(line.get("product_id", ""))
                        if unit_cost is None and line.get("variant_missing")
                        else None)

            if unit_cost is not None:
                item["cost"] += unit_cost * qty
                item["revenue_costed"] += line_revenue
                item["units_costed"] += qty
                cost += unit_cost * qty
                costed_revenue += line_revenue
            elif estimate is not None:
                item["estimated_cost"] += estimate * qty
                item["units_estimated"] += qty
                est_cost += estimate * qty
                est_revenue += line_revenue
            else:
                item["units_uncosted"] += qty
                uncosted_revenue += line_revenue
                uncosted_units += qty

    margin = costed_revenue - cost
    est_margin = est_revenue - est_cost
    payout = round(margin * pct / 100, 2) if pct is not None else None
    payout_est = (round((margin + est_margin) * pct / 100, 2)
                  if pct is not None else None)

    rows = [_item_row(title, totals) for title, totals in items.items()]
    rows.sort(key=lambda r: (-r["units"], -r["revenue"], r["title"]))
    order_rows = sorted(orders.values(), key=lambda r: (r["date"], r["name"]))

    start, end = bounds(quarter)
    result = {
        "quarter": quarter,
        "quarter_slug": slug(quarter),
        "period_start": start,
        "period_end": end,
        "period": period_label(quarter),
        "issued": date.today(),
        "number": statement_number(record, quarter),
        "partner_id": pid,
        "org_name": record.get("org_name", ""),
        "poc_name": record.get("poc_name", ""),
        "poc_email": record.get("poc_email", ""),
        "plan": record.get("plan", ""),
        "margin_pct": pct,
        "orders": len(order_rows),
        "units": units,
        "revenue": round(revenue, 2),
        "cost": round(cost, 2),
        "margin": round(margin, 2),
        "payout": payout,
        # Named "lines", not "items": Jinja resolves `statement.items` to the
        # dict's own .items() method, so a template iterating it silently gets
        # a bound method instead of the product list. Renaming the key is the
        # only fix that cannot be forgotten in the next template.
        "lines": rows,
        "order_rows": order_rows,
        # Reported separately, never folded into `payout`.
        "estimated_revenue": round(est_revenue, 2),
        "estimated_cost": round(est_cost, 2),
        "estimated_margin": round(est_margin, 2),
        "estimated_units": sum(r["units_estimated"] for r in rows),
        "payout_with_estimates": payout_est,
        "uncosted_revenue": round(uncosted_revenue, 2),
        "uncosted_units": uncosted_units,
    }
    result["blockers"] = blockers(result)
    result["warnings"] = warnings(result)
    result["checked"] = _cross_check(result, cross_check)
    return result


def _item_row(title: str, totals: dict) -> dict:
    """One product line. Margin is stated only on the units that have a cost.

    A row mixing costed and uncosted units reports the margin it can stand
    behind — computed from the revenue of those units specifically, not from
    the row's revenue prorated by unit count, which would be an assumption
    that every unit sold at the same price. They do not: the same garment
    sells at a discount during a launch week.
    """
    payable = totals["units_costed"] > 0
    return {
        "title": title,
        "units": totals["units"],
        "orders": len(totals["orders"]),
        "revenue": round(totals["revenue"], 2),
        "cost": round(totals["cost"], 2) if payable else None,
        "margin": (round(totals["revenue_costed"] - totals["cost"], 2)
                   if payable else None),
        "units_costed": totals["units_costed"],
        "units_estimated": totals["units_estimated"],
        "units_uncosted": totals["units_uncosted"],
        "estimated_cost": round(totals["estimated_cost"], 2),
        "basis": ("costed" if totals["units"] == totals["units_costed"]
                  else "estimated" if totals["units_estimated"]
                  else "uncosted" if totals["units"] == totals["units_uncosted"]
                  else "partial"),
    }


def statement_number(record: dict, quarter: str) -> str:
    """SS-2026Q3-GCS. Stable for a given partner and quarter, so a re-issued
    statement carries the same reference the partner already has on file."""
    year, part = quarter.split(" Q")
    words = re.findall(r"[A-Za-z0-9]+", record.get("org_name", "") or "")
    initials = "".join(word[0] for word in words if word[0].isalpha()).upper()
    tag = initials[:5] or store.partner_id(record)[:5].upper() or "PARTNER"
    return f"SS-{year}Q{part}-{tag}"


def blockers(statement: dict) -> list[str]:
    """Reasons this must not be sent. Empty means it may be.

    Only the rate is here. Everything else about a statement can be honestly
    disclosed on the page; a payout figure derived from a placeholder rate
    cannot, because the partner has no way to see that the number is a seed
    value rather than their agreement.
    """
    reasons = []
    if statement["margin_pct"] is None:
        reasons.append(
            "No negotiated rate on this partner's record — the 0% seed value "
            "would print as a real term. Set Client margin before sending.")
    return reasons


def warnings(statement: dict) -> list[str]:
    """Things to look at before sending. None of them stop a statement."""
    notes = []
    if statement["uncosted_revenue"]:
        notes.append(
            f"${statement['uncosted_revenue']:,.2f} of sales has no cost of "
            f"goods on file, so it earns nothing in this statement. Where the "
            f"variant still exists in Shopify, entering the cost and "
            f"refreshing raises the payout.")
    if statement["estimated_revenue"]:
        notes.append(
            f"${statement['estimated_revenue']:,.2f} of sales is priced from "
            f"the median of the product's surviving variants, because the "
            f"variant that sold has been deleted. Including it would make the "
            f"payout ${statement['payout_with_estimates']:,.2f} rather than "
            f"${statement['payout']:,.2f}.")
    if not statement["orders"]:
        notes.append("No orders in this quarter — there is nothing to pay.")
    if not statement["poc_email"]:
        notes.append("No email address on this partner's record, so a draft "
                     "cannot be addressed. Add one on the partner screen.")
    return notes


def _cross_check(statement: dict, row: dict | None) -> dict:
    """Does this statement agree with the dashboard's own quarter row?

    The two are computed from the same functions, so they should never differ.
    That is exactly why it is worth asserting: the day they do differ, it will
    be because something upstream changed shape, and the statement is the copy
    that leaves the building.
    """
    if not row:
        return {"compared": False, "agrees": True, "differences": []}
    fields = (("revenue", "Revenue"), ("margin", "Margin"),
              ("units", "Items"), ("payout", "Payout"))
    differences = []
    for key, label in fields:
        mine, theirs = statement.get(key), row.get(key)
        if mine is None and theirs is None:
            continue
        if round(float(mine or 0), 2) != round(float(theirs or 0), 2):
            differences.append(f"{label}: statement {mine}, dashboard {theirs}")
    return {"compared": True, "agrees": not differences,
            "differences": differences}


# --------------------------------------------------------------- summary ----

def overview(snapshot: dict, records: list[dict], quarter: str,
             rollup: dict | None = None) -> list[dict]:
    """Every partner's figures for one quarter, for the statements screen.

    Built by running `build()` per partner rather than by reading the
    dashboard's own quarter rows, so the row Larry clicks Send on is the same
    arithmetic as the statement that goes out — not a second summary that
    agrees with it today.
    """
    by_partner = {}
    if rollup:
        for row in rollup.get("partners", []):
            for entry in row.get("quarters", []):
                if entry["quarter"] == quarter:
                    by_partner[row["partner_id"]] = entry

    rows = []
    for record in records:
        pid = store.partner_id(record)
        rows.append(build(snapshot, records, record, quarter,
                          cross_check=by_partner.get(pid)))
    rows.sort(key=lambda r: (-(r["payout"] or 0), -r["revenue"], r["org_name"]))
    return rows


def totals(rows: list[dict]) -> dict:
    payable = [r for r in rows if r["payout"] is not None]
    return {
        "partners": len(rows),
        "paying": sum(1 for r in payable if r["payout"]),
        "blocked": sum(1 for r in rows if r["blockers"]),
        "revenue": round(sum(r["revenue"] for r in rows), 2),
        "margin": round(sum(r["margin"] for r in rows), 2),
        "payout": round(sum(r["payout"] for r in payable), 2),
        # Distinct across the store, not the sum of the per-partner counts:
        # one order can carry two partners' goods.
        "orders": len({row["name"] for r in rows for row in r["order_rows"]}),
        "units": sum(r["units"] for r in rows),
        "uncosted_revenue": round(sum(r["uncosted_revenue"] for r in rows), 2),
    }
