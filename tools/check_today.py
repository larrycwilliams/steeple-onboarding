#!/usr/bin/env python3
"""Offline check of Home's Today panel and the background refresh (doc 69).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_today.py

Copies the app's CODE (never partners/, cache/, output/ or assets/) into a
temp folder, writes made-up partners and caches there, and drives Home
through Flask's test client with the real templates. Nothing real is read or
written, and no network call is made: the refresh steps are swapped for fakes.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[1]
# .env holds the Shopify credentials: never copied, even into a temp folder.
SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

tmp = Path(tempfile.mkdtemp(prefix="today-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP or n.startswith(".env")
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))

import app as portal  # noqa: E402  (the temp copy)
from onboarding import agreement, refresh, store, today  # noqa: E402

assert Path(store.__file__).resolve().is_relative_to(work.resolve()), "imported the real app, not the copy"

ok = fail = 0


def check(name: str, cond) -> None:
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


NOW = datetime.now(timezone.utc)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def line(vendor: str, price: float, qty: int = 1, cost: float | None = 5.0) -> dict:
    return {"title": f"{vendor} tee", "qty": qty, "qty_ordered": qty, "product_id": f"gid://p/{vendor}",
            "variant_missing": False, "vendor_at_sale": vendor, "vendor": vendor,
            "product_status": "ACTIVE", "unit_price": price, "unit_cost": cost}


def order(name: str, days: float, fulfillment: str, *lines, **extra) -> dict:
    o = {"name": name, "created_at": ago(days), "test": False, "cancelled": False,
         "status": "PAID", "fulfillment": fulfillment, "lines": list(lines)}
    o.update(extra)
    return o


cache = work / "cache"
cache.mkdir(exist_ok=True)

# ------------------------------------------------------------------ fixtures
gcs = store.save({"org_name": "Check Christian School", "margin_pct": "30", "plan": "Growth",
                  "shopify_vendor": "Check Christian School", "monthly_fee": "49", "setup_fee": "399",
                  "recurring_invoice_started": (NOW - timedelta(days=120)).date().isoformat(),
                  "recurring_paid_through": (NOW - timedelta(days=70)).date().isoformat()})
revive = store.save({"org_name": "Check Church", "margin_pct": "", "plan": "Starter",
                     "shopify_vendor": "Check Church", "monthly_fee": "39", "setup_fee": "299"})
agreement.mark_sent(store.partner_id(revive), when=(NOW - timedelta(days=10)).date().isoformat())

orders = [
    order("#2001", 16, "UNFULFILLED", line("Check Christian School", 25.0, 2)),
    order("#2002", 1, "IN_PROGRESS", line("Check Church", 20.0)),
    order("#2003", 3, "FULFILLED", line("Check Christian School", 30.0, cost=None)),
    order("#2004", 2, "UNFULFILLED", line("Check Church", 99.0), test=True),
    order("#2005", 2, "UNFULFILLED", line("Check Church", 99.0), cancelled=True),
]
(cache / "shopify_sales.json").write_text(json.dumps(
    {"pulled_at": ago(0.01), "since": "2020-01-01", "orders": orders, "catalog": [], "collections": []}))
(cache / "shopify_leads.json").write_text(json.dumps({"pulled_at": ago(0.02), "leads": [
    {"id": "gid://shopify/Company/1", "key": "1", "org_name": "Waiting Church", "created_at": ago(3),
     "note": "submitted to the form", "email": "pastor@waiting.example", "name": "Pastor W"},
    {"id": "gid://shopify/Company/2", "key": "2", "org_name": "Fresh Chapel", "created_at": ago(0.1),
     "note": "submitted to the form", "email": "office@fresh.example", "name": "Office"},
]}))
pur = cache / "purchasing"
pur.mkdir(exist_ok=True)
(pur / "snapshot.json").write_text(json.dumps({"at": ago(0.1), "ok": True, "proposal": {
    "lines": [{"sku": "B1"}],
    "tracked": [{"qty": 4, "status": "ordered", "ss_order": "777"},
                {"qty": 2, "status": "received", "ss_order": "776"}]}}))
(pur / "ss_status.json").write_text(json.dumps({"at": ago(0.1), "ok": True, "orders": {
    "777": {"order_number": "777", "suggest_receive": True}}}))
(cache / "studio_summary.json").write_text(json.dumps({
    "ok": True, "version": "1.8.0", "at": ago(0.05),
    "total": {"sent": 3, "changes": 1, "approved": 2, "production": 1},
    "brands": {"ss": {"sent": 2}, "twc": {"sent": 1}},
    "oldest_sent_days": 6}))

# ------------------------------------------------------------------ the tiles
data = today.tiles(studio_url="https://studio.example")
by = {t["key"]: t for t in data["tiles"]}

check("eight tiles, every one built", len(by) == 8 and all(t["title"] for t in by.values()))
check("red tiles come first", [t["state"] for t in data["tiles"]][:data["red"]] == ["red"] * data["red"])

o = by["orders"]
check("orders: test, cancelled and fulfilled orders are not counted", o["value"] == "2")
check("orders: 16 days old is red against the 7-10 day promise", o["state"] == "red" and o["items"][0]["name"] == "#2001")

check("leads: both unanswered leads are counted", by["leads"]["value"] == "2")
check("leads: a 3-day-old lead is past the promise", by["leads"]["state"] == "red")

a = by["agreements"]
check("agreements: 10 days out is a chase", a["value"] == "1" and a["state"] == "red")

b = by["blanks"]
check("blanks: only pieces not yet received are awaited", b["value"] == "4")
check("blanks: an arrived box and lines to buy make it amber", b["state"] == "amber"
      and any("mark received" in i["detail"] for i in b["items"]))

st = by["studio"]
check("studio: sent count and a 6-day-old approval is red", st["value"] == "3" and st["state"] == "red")
check("studio tile links out to the studio", st["external"] and st["link"] == "https://studio.example")

g = by["giveback"]
check("giveback: 30% of costed margin, uncosted sales flagged not zeroed",
      g["value"].startswith("$") and g["state"] == "amber" and "no cost" in g["note"])
check("giveback: a partner with no rate is named, not paid $0", "Check Church" in g["note"])

f = by["fees"]
check("fees: a partner paid through 2+ months ago is behind", f["state"] == "red" and f["items"])

check("freshness lists all four sources", [x["label"] for x in data["fresh"]] ==
      ["Shopify orders", "Leads", "S&S order status", "Studio"])

# A broken source costs one tile, never the page.
(cache / "studio_summary.json").write_text("{not json")
with mock.patch.object(today, "blanks_tile", side_effect=RuntimeError("boom")):
    broken = {t["key"]: t for t in today.tiles()["tiles"]}
check("a crashing tile becomes 'could not read', the rest still build",
      broken["blanks"]["state"] == "unknown" and "boom" in broken["blanks"]["note"] and len(broken) == 8)
check("an unreadable studio file is 'not fetched', not a crash", broken["studio"]["state"] == "unknown")

# Old caches from before this change carry no fulfilment field.
legacy = json.loads((cache / "shopify_sales.json").read_text())
for o_ in legacy["orders"]:
    o_.pop("fulfillment", None)
check("an old orders cache says 'refresh once' instead of claiming 0 to ship",
      today.orders_tile(legacy, NOW.date())["state"] == "unknown")

# ------------------------------------------------------------------ the page
client = portal.app.test_client()
page = client.get("/")
html = page.get_data(as_text=True)
check("Home renders with the Today panel", page.status_code == 200 and "td-grid" in html and "Orders to ship" in html)
check("Home still shows the partner path below it", "The path" in html)
check("the menu links to the Studio in a new tab", "Studio&nbsp;" in html and portal.STUDIO_URL in html)
check("version is 3.69 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 69))

# Refresh now: runs in the background and never blocks the request.
with mock.patch.object(portal.refresh_job, "run_all") as fake:
    r = client.post("/today/refresh", headers={"Host": "localhost"})
check("Refresh now redirects back to Home", r.status_code in (302, 303) and r.headers["Location"].endswith("/"))
r = client.post("/today/refresh", headers={"Host": "localhost", "Origin": "https://evil.example"})
check("Refresh now refuses a post from another site", r.status_code == 403)

# ------------------------------------------------------------------ the job
calls = []
fakes = [("Shopify orders", lambda: (calls.append("s") or True, "5 orders")),
         ("Leads", lambda: (_ for _ in ()).throw(RuntimeError("network down"))),
         ("Studio", lambda: (calls.append("t") or True, "Studio v1.8.0"))]
with mock.patch.object(refresh, "STEPS", fakes):
    state = refresh.run_all(trigger="test")
check("a failing step does not stop the next", calls == ["s", "t"])
check("the failure is recorded with its reason", [s["ok"] for s in state["steps"]] == [True, False, True]
      and "network down" in state["steps"][1]["detail"])
saved = json.loads((cache / "refresh.json").read_text())
check("the run is saved for Home", saved["finished_at"] and not saved["running"])

with refresh._single_run() as first:
    with refresh._single_run() as second:
        check("two refreshes cannot run at once", first and not second)

# Studio fetch failing keeps the last good counts, marked.
(cache / "studio_summary.json").write_text(json.dumps({"ok": True, "total": {"sent": 1}, "at": ago(1)}))
with mock.patch.object(refresh, "STUDIO_INTERNAL", "http://127.0.0.1:9"):
    ok_, detail = refresh.fetch_studio_summary()
kept = json.loads((cache / "studio_summary.json").read_text())
check("an unreachable Studio keeps the last counts and records why",
      not ok_ and kept["ok"] and kept["total"]["sent"] == 1 and kept.get("error"))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
