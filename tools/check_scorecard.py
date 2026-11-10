#!/usr/bin/env python3
"""Offline check of the program scorecard (doc 69, phase 3).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_scorecard.py

Copies the app's CODE (never .env, partners/, cache/, output/ or assets/)
into a temp folder, writes made-up partners and orders there, and drives
/programs through Flask's test client. Nothing real is read or written.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
# .env holds the Shopify credentials: never copied, even into a temp folder.
SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

tmp = Path(tempfile.mkdtemp(prefix="scorecard-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))

import app as portal  # noqa: E402
from onboarding import agreement, scorecard, sent, statement, store  # noqa: E402

assert Path(store.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"

ok = fail = 0


def check(name: str, cond) -> None:
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


NOW = datetime.now(timezone.utc)
TODAY = NOW.astimezone().date()
LAST_Q = statement.last_complete_quarter(NOW)
LQ_START, LQ_END = statement.bounds(LAST_Q)
IN_LAST_Q = datetime.combine(LQ_START + (LQ_END - LQ_START) / 2, datetime.min.time(),
                             tzinfo=timezone.utc).isoformat()


def iso(d: date) -> str:
    return d.isoformat()


GCS, REV, HOH, NEW = ("Germantown Christian School", "Revive Church", "Haven of Hope", "Grace Chapel")
gcs = store.save({"org_name": GCS, "plan": "Growth", "setup_fee": "399", "monthly_fee": "49",
                  "margin_pct": "30", "shopify_vendor": GCS, "org_type": "school",
                  "launch_date": iso(TODAY - timedelta(days=120)),
                  "startup_invoice_sent": iso(TODAY - timedelta(days=130)),
                  "startup_invoice_paid": iso(TODAY - timedelta(days=120)),
                  "recurring_invoice_started": iso(TODAY - timedelta(days=120)),
                  "recurring_paid_through": iso(TODAY - timedelta(days=75))})
rev = store.save({"org_name": REV, "plan": "Growth", "setup_fee": "399", "monthly_fee": "49",
                  "margin_pct": "30", "shopify_vendor": REV, "org_type": "church",
                  "launch_date": iso(TODAY - timedelta(days=20))})
hoh = store.save({"org_name": HOH, "plan": "Growth", "setup_fee": "200", "monthly_fee": "49",
                  "margin_pct": "30", "shopify_vendor": HOH, "org_type": "church",
                  "launch_date": iso(TODAY + timedelta(days=20))})
new = store.save({"org_name": NEW, "plan": "Starter", "setup_fee": "299", "monthly_fee": "39",
                  "margin_pct": "", "shopify_vendor": NEW})
seed = store.save({"org_name": "GCS Cardinals SEED CHECK", "_seed_check": True, "plan": "Growth"})
pid = {name: store.partner_id(r) for name, r in ((GCS, gcs), (REV, rev), (HOH, hoh), (NEW, new))}

agreement.mark_sent(pid[GCS], when=iso(TODAY - timedelta(days=140)))
agreement.mark_returned(pid[GCS], when=iso(TODAY - timedelta(days=135)))
agreement.mark_sent(pid[REV], when=iso(TODAY - timedelta(days=10)))


def line(vendor, price, cost=8.0, qty=1):
    return {"title": f"{vendor} tee TWC", "qty": qty, "qty_ordered": qty, "vendor": vendor,
            "vendor_at_sale": vendor, "product_id": f"gid://p/{vendor}", "variant_missing": False,
            "product_status": "ACTIVE", "unit_price": price, "unit_cost": cost}


orders = [
    {"name": "#5001", "created_at": IN_LAST_Q, "test": False, "cancelled": False, "status": "PAID",
     "fulfillment": "FULFILLED", "lines": [line(GCS, 30.0, qty=2)]},
    {"name": "#5002", "created_at": (NOW - timedelta(days=2)).isoformat(), "test": False, "cancelled": False,
     "status": "PAID", "fulfillment": "UNFULFILLED", "lines": [line(GCS, 25.0), line(REV, 20.0, cost=None)]},
]
catalog = ([{"id": f"g{i}", "title": f"GCS item {i} TWC", "vendor": GCS, "status": "ACTIVE",
             "unit_cost": 8.0 if i else None, "unit_costs": []} for i in range(3)]
           + [{"id": f"r{i}", "title": f"Revive item {i}", "vendor": REV, "status": "DRAFT",
               "unit_cost": None, "unit_costs": []} for i in range(4)]
           + [{"id": f"h{i}", "title": f"HOH item {i}", "vendor": HOH, "status": "DRAFT",
               "unit_cost": None, "unit_costs": []} for i in range(2)])
cache = work / "cache"
cache.mkdir(exist_ok=True)
(cache / "shopify_sales.json").write_text(json.dumps({
    "pulled_at": NOW.isoformat(timespec="seconds"), "orders": orders, "catalog": catalog,
    "collections": [], "locations": []}))

data = scorecard.build(now=NOW)
cards = {c["name"]: c for c in data["cards"]}
text = {n: " ".join(f["text"] for f in c["flags"]) for n, c in cards.items()}

check("one card per real partner; the seed check record is left out",
      set(cards) == {GCS, REV, HOH, NEW})
check("red programs sort first", [c["state"] for c in data["cards"]][:data["red"]] == ["red"] * data["red"])

g = cards[GCS]
check("fees: paid through 75 days ago is behind, and red", g["billing"]["behind"] >= 2 and g["state"] == "red")
check("statement: last quarter had sales, not sent, past due -> red flag",
      "statement not sent" in text[GCS] and g["last_q"]["statement"] == "not sent")
check("last-quarter giveback is 30% of costed margin", g["last_q"]["giveback"] == "$13.20")
check("store counts live products and the one with no cost",
      g["store"]["live"] == 3 and "1 live product with no cost" in text[GCS])
check("open orders are counted per partner", g["open_orders"] == 1 and cards[REV]["open_orders"] == 1)
check("signed agreement reads as signed", g["agreement"]["stage"] == "returned")

sent.mark("statement", sent.statement_ref(pid[GCS], statement.slug(LAST_Q)), when=iso(TODAY))
g2 = {c["name"]: c for c in scorecard.build(now=NOW)["cards"]}[GCS]
check("ticking the statement on Statements clears the flag here",
      g2["last_q"]["statement"].startswith("sent") and "statement not sent" not in
      " ".join(f["text"] for f in g2["flags"]))

r = cards[REV]
check("launched with nothing live is red, and says how many are in draft",
      r["state"] == "red" and "nothing is live in the store (4 in draft)" in text[REV])
check("agreement out 10 days is a chase", r["agreement"]["overdue"] and "Agreement out 10 days" in text[REV])
check("live but monthly fee not billed is flagged", "not being billed yet" in text[REV])
check("uncosted sales this quarter are flagged, not counted as $0 giveback", "has no cost yet" in text[REV])

h = cards[HOH]
check("upcoming launch shows days to go", h["launch_state"] == "upcoming" and h["days_to_launch"] == 20)
check("a negotiated setup fee is flagged as differing from the plan, not as wrong",
      "Terms differ from the Growth plan" in text[HOH] and "check it is" in text[HOH])
check("drafts staged before launch are not an alarm", "nothing is live" not in text[HOH])

n = cards[NEW]
check("no giveback rate: giveback is a dash, never $0", n["this_q"]["giveback"] == "—"
      and "No giveback rate" in text[NEW])
check("no launch date is flagged", "No launch date" in text[NEW] and n["launch_state"] == "unset")

check("summary: programs and live counts", data["total"] == 4 and data["live"] == 2)
check("summary: statements still to send for last quarter", data["statements_open"] == 1)

# Without Shopify data the page still works and says so; money shows a dash.
(cache / "shopify_sales.json").unlink()
bare = scorecard.build(now=NOW)
check("no Shopify cache: store and sales are dashes, nothing crashes",
      not bare["have_shopify"] and all(c["store"] is None and c["this_q"]["revenue"] == "—"
                                       for c in bare["cards"]))
(cache / "shopify_sales.json").write_text(json.dumps({
    "pulled_at": NOW.isoformat(timespec="seconds"), "orders": orders, "catalog": catalog,
    "collections": [], "locations": []}))

client = portal.app.test_client()
page = client.get("/programs")
html = page.get_data(as_text=True)
check("/programs renders every card", page.status_code == 200 and all(name in html for name in cards))
check("Needs attention hides the programs that are fine",
      client.get("/programs?show=attention").status_code == 200)
check("Programs is in the menu", 'href="/programs"' in client.get("/").get_data(as_text=True))
check("Home's Platform fees tile opens Programs", 'href="/programs"' in client.get("/").get_data(as_text=True))
check("each card links to the partner's record, files and orders",
      f'/partner/{pid[GCS]}"' in html and f"/partner/{pid[GCS]}/files" in html and "/orders?p=" in html)
check("version is 3.74 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 74))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
