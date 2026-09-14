"""Does a payout statement agree with the Dashboard, to the cent?

    ~/.venvs/steeple-onboarding-312/bin/python tools/check_statements.py

Runs the real rollup and the real statement builder against a synthetic
snapshot and asserts every figure. Needs no Shopify connection, no partner
records and no network -- which is the point: it can be run before a payout
run, on any machine, in a second.

The snapshot is built to contain every case the live store actually produces:
a deleted variant (estimated), a line with no cost entered (excluded), one
order carrying two partners' goods, a test order, a cancelled order, a fully
refunded line, a vendor frozen wrong on the line, a parent/child pair whose
legal names collide, and a partner still on the 0% seed rate.

Change the payout arithmetic and this is the file that should fail first.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import dashboard, statement, store  # noqa: E402

GCS = {"org_name": "Germantown Christian Schools", "org_legal_name": "Germantown Christian School",
       "shopify_vendor": "Germantown Christian School", "plan": "Growth",
       "margin_pct": "30", "poc_name": "Sarah Allen", "poc_email": "sallen@alt-gcs.com"}
ATH = {"org_name": "GCS Athletics", "org_legal_name": "Germantown Christian School",
       "shopify_vendor": "GCS Athletics", "plan": "Growth", "margin_pct": "30",
       "poc_name": "Coach Reed", "poc_email": "reed@alt-gcs.com"}
REVIVE = {"org_name": "Revive Church", "shopify_vendor": "Revive Church",
          "plan": "Starter", "margin_pct": "0", "poc_name": "Pastor Kay",
          "poc_email": "kay@revive.org"}
RECORDS = [GCS, ATH, REVIVE]


def line(title, qty, price, cost=None, vendor="Germantown Christian School",
         product_id="gid://p1", variant_missing=False):
    return {"title": title, "qty": qty, "qty_ordered": qty, "unit_price": price,
            "unit_cost": cost, "vendor": vendor, "product_id": product_id,
            "variant_missing": variant_missing, "product_status": "ACTIVE"}


def order(name, when, lines, test=False, cancelled=False):
    return {"name": name, "created_at": f"{when}T15:00:00Z", "test": test,
            "cancelled": cancelled, "status": "PAID", "lines": lines}


SNAPSHOT = {
    "pulled_at": "2026-09-14T01:00:00+00:00",
    "orders": [
        # --- 2026 Q3, the quarter under test -----------------------------
        order("#1001", "2026-07-04", [
            line("GCS Tultex Tee", 3, 22.00, 9.00),
            line("GCS Tote Bag", 1, 18.00, None, product_id="gid://p2",
                 variant_missing=True),          # deleted variant -> estimated
        ]),
        order("#1002", "2026-07-19", [           # two partners, one order
            line("GCS Tultex Tee", 2, 20.00, 9.00),
            line("Revive Crewneck", 1, 40.00, 16.00, vendor="Revive Church",
                 product_id="gid://p9"),
        ]),
        order("#1003", "2026-08-02", [
            line("GCS Hoodie", 1, 48.00, None, product_id="gid://p3"),  # no cost
            line("Cardinal Script Hat TWC", 2, 25.00, 8.00,
                 vendor="GCS Athletics", product_id="gid://p4"),
        ]),
        order("#1004", "2026-08-30", [
            # currentQuantity 1 of 2 ordered: one unit came back
            line("GCS Tultex Tee", 1, 22.00, 9.00),
        ]),
        order("#1005", "2026-09-11", [
            line("GCS Tote Bag", 2, 18.00, None, product_id="gid://p2",
                 variant_missing=True),
        ]),
        order("#1006", "2026-09-12", [line("GCS Hoodie", 1, 48.00, 24.00,
                                           product_id="gid://p3")], test=True),
        order("#1007", "2026-09-13", [line("GCS Hoodie", 1, 48.00, 24.00,
                                           product_id="gid://p3")], cancelled=True),
        order("#1008", "2026-09-13", [line("GCS Hoodie", 0, 48.00, 24.00,
                                           product_id="gid://p3")]),  # all refunded
        # --- a previous quarter, which must not leak in -------------------
        order("#0990", "2026-05-05", [line("GCS Tultex Tee", 5, 22.00, 9.00)]),
    ],
    "catalog": [
        {"id": "gid://p1", "title": "GCS Tultex Tee", "vendor": "Germantown Christian School",
         "status": "ACTIVE", "unit_cost": 9.00, "unit_costs": [9.00, 9.00]},
        # the tote's own variant is gone; siblings at 6 and 8 -> median 7
        {"id": "gid://p2", "title": "GCS Tote Bag", "vendor": "Germantown Christian School",
         "status": "ACTIVE", "unit_cost": 6.00, "unit_costs": [6.00, 8.00]},
        {"id": "gid://p3", "title": "GCS Hoodie", "vendor": "Germantown Christian School",
         "status": "ACTIVE", "unit_cost": None, "unit_costs": []},
        {"id": "gid://p4", "title": "Cardinal Script Hat TWC", "vendor": "GCS Athletics",
         "status": "ACTIVE", "unit_cost": 8.00, "unit_costs": [8.00, 8.00]},
        {"id": "gid://p9", "title": "Revive Crewneck", "vendor": "Revive Church",
         "status": "ACTIVE", "unit_cost": 16.00, "unit_costs": [16.00]},
    ],
    "collections": [],
}

QUARTER = "2026 Q3"
failures = []


def check(label, got, want):
    ok = got == want
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: {got!r}"
          + ("" if ok else f"  (expected {want!r})"))
    if not ok:
        failures.append(label)


roll = dashboard.rollup(SNAPSHOT, RECORDS)
rows = {r["partner_id"]: r for r in roll["partners"]}

print("\nQuarter helpers")
check("slug", statement.slug(QUARTER), "2026-Q3")
check("unslug", statement.unslug("2026-Q3"), "2026 Q3")
check("unslug rejects junk", statement.unslug("../../etc"), "")
check("unslug rejects Q5", statement.unslug("2026-Q5"), "")
check("bounds", statement.bounds(QUARTER), (date(2026, 7, 1), date(2026, 9, 30)))
check("quarters with sales", statement.quarters_with_sales(SNAPSHOT),
      ["2026 Q3", "2026 Q2"])
check("last complete quarter from Q3", statement.last_complete_quarter(
      __import__("datetime").datetime(2026, 9, 14)), "2026 Q2")
check("last complete quarter from Q1", statement.last_complete_quarter(
      __import__("datetime").datetime(2026, 2, 1)), "2025 Q4")

print("\nGCS statement — arithmetic")
gcs = statement.build(SNAPSHOT, RECORDS, GCS, QUARTER)
# Tees: 3x22 + 2x20 + 1x22 = 128.00, cost 6x9 = 54.00
# Tote: 1x18 + 2x18 = 54.00, variant deleted -> estimated at median 7 => 21.00
# Hoodie: 1x48, no cost anywhere -> excluded
check("orders", gcs["orders"], 5)
check("units", gcs["units"], 10)
check("revenue", gcs["revenue"], 230.00)
check("cost", gcs["cost"], 54.00)
check("margin", gcs["margin"], 74.00)
check("payout at 30%", gcs["payout"], 22.20)
check("estimated revenue", gcs["estimated_revenue"], 54.00)
check("estimated cost (median 7)", gcs["estimated_cost"], 21.00)
check("payout incl. estimates", gcs["payout_with_estimates"], 32.10)
check("uncosted revenue", gcs["uncosted_revenue"], 48.00)
check("uncosted units", gcs["uncosted_units"], 1)
check("no blockers", gcs["blockers"], [])

print("\nGCS statement — item detail, by quantity")
items = {i["title"]: i for i in gcs["lines"]}
check("items ordered by qty", [i["title"] for i in gcs["lines"]],
      ["GCS Tultex Tee", "GCS Tote Bag", "GCS Hoodie"])
check("tee units", items["GCS Tultex Tee"]["units"], 6)
check("tee spans 3 orders", items["GCS Tultex Tee"]["orders"], 3)
check("tee revenue", items["GCS Tultex Tee"]["revenue"], 128.00)
check("tee margin", items["GCS Tultex Tee"]["margin"], 74.00)
check("tee basis", items["GCS Tultex Tee"]["basis"], "costed")
check("tote basis", items["GCS Tote Bag"]["basis"], "estimated")
check("tote margin withheld", items["GCS Tote Bag"]["margin"], None)
check("hoodie basis", items["GCS Hoodie"]["basis"], "uncosted")
check("item revenue foots", round(sum(i["revenue"] for i in gcs["lines"]), 2),
      gcs["revenue"])
check("item units foot", sum(i["units"] for i in gcs["lines"]), gcs["units"])

print("\nGCS statement — orders listed")
check("order names", [o["name"] for o in gcs["order_rows"]],
      ["#1001", "#1002", "#1003", "#1004", "#1005"])
check("order revenue foots", round(sum(o["revenue"] for o in gcs["order_rows"]), 2),
      gcs["revenue"])
check("#1002 counts only the GCS line",
      [o["revenue"] for o in gcs["order_rows"] if o["name"] == "#1002"], [40.0])

print("\nAgreement with the dashboard")
for record in RECORDS:
    pid = store.partner_id(record)
    row = next((q for q in rows[pid]["quarters"] if q["quarter"] == QUARTER), None)
    st = statement.build(SNAPSHOT, RECORDS, record, QUARTER, cross_check=row)
    check(f"{record['org_name']}: agrees with dashboard", st["checked"]["agrees"], True)
    if not st["checked"]["agrees"]:
        print("      ", st["checked"]["differences"])

print("\nThe 0% seed refuses to print a payout")
revive = statement.build(SNAPSHOT, RECORDS, REVIVE, QUARTER)
check("revive revenue still reported", revive["revenue"], 40.00)
check("revive payout withheld", revive["payout"], None)
check("revive blocked", len(revive["blockers"]), 1)

print("\nAthletics is not paid out of the school's sales")
ath = statement.build(SNAPSHOT, RECORDS, ATH, QUARTER)
check("athletics revenue", ath["revenue"], 50.00)
check("athletics units", ath["units"], 2)
check("athletics payout", ath["payout"], 10.20)

print("\nOverview and totals")
over = statement.overview(SNAPSHOT, RECORDS, QUARTER, roll)
tot = statement.totals(over)
check("distinct orders across the store", tot["orders"], 5)
check("store revenue for the quarter", tot["revenue"], 320.00)
check("total payable", tot["payout"], round(22.20 + 10.20, 2))
check("one partner blocked", tot["blocked"], 1)
check("statement number", gcs["number"], "SS-2026Q3-GCS")

print("\nA quarter with nothing in it")
empty = statement.build(SNAPSHOT, RECORDS, GCS, "2026 Q1")
check("no orders", empty["orders"], 0)
check("payout is zero, not None", empty["payout"], 0.0)
check("warned", any("No orders" in w for w in empty["warnings"]), True)

print()
if failures:
    print(f"{len(failures)} FAILED: " + "; ".join(failures))
    raise SystemExit(1)
print("All checks passed.")
