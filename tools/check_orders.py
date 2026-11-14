#!/usr/bin/env python3
"""Offline check of the orders board (doc 69, phase 2).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_orders.py

Copies the app's CODE (never partners/, cache/, output/ or assets/) into a
temp folder, writes made-up orders and Purchasing data there, and drives
/orders through Flask's test client. Nothing real is read or written.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
# .env holds the Shopify credentials: never copied, even into a temp folder.
SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

tmp = Path(tempfile.mkdtemp(prefix="orders-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP or n.startswith(".env")
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))

import os  # noqa: E402
os.environ["SHOPIFY_STORE"] = "check-store.myshopify.com"   # a made-up domain, never called
import app as portal  # noqa: E402
from onboarding import orders, store  # noqa: E402

assert Path(store.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"

ok = fail = 0


def check(name: str, cond) -> None:
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


NOW = datetime.now(timezone.utc)


def ago(days: float) -> str:
    return (NOW - timedelta(days=days)).isoformat(timespec="seconds")


def line(title: str, vendor: str, qty: int = 1) -> dict:
    return {"title": title, "qty": qty, "qty_ordered": qty, "vendor": vendor, "vendor_at_sale": vendor,
            "product_id": "", "variant_missing": False, "product_status": "ACTIVE",
            "unit_price": 20.0, "unit_cost": 8.0}


def order(name: str, days: float, fulfil: str, delivery: str, *lines) -> dict:
    return {"name": name, "id": name.strip("#"), "created_at": ago(days), "test": False,
            "cancelled": False, "status": "PAID", "fulfillment": fulfil, "delivery": delivery,
            "lines": list(lines)}


GCS, HOH = "Germantown Christian School", "Haven of Hope"
cache = work / "cache"
(cache / "purchasing").mkdir(parents=True, exist_ok=True)
(cache / "shopify_sales.json").write_text(json.dumps({
    "pulled_at": ago(0.01), "locations": ["Shop Pickup – Dayton", "Germantown Christian School"],
    "orders": [
        order("#3001", 12, "UNFULFILLED", "Standard", line("GCS Hoodie TWC", GCS, 2)),                  # to buy
        order("#3002", 4, "UNFULFILLED", "Germantown Christian School", line("GCS Tee TWC", GCS)),       # ordered
        order("#3003", 3, "IN_PROGRESS", "Shop Pickup – Dayton", line("HOH Hat TWC", HOH),
              line("HOH Mug", HOH)),                                                                      # in
        order("#3004", 2, "UNFULFILLED", "Standard", line("HOH Tee TWC", HOH)),                          # in + pressed tick
        order("#3005", 1, "UNFULFILLED", "Standard", line("Phone case", HOH)),                           # POD only
        order("#3006", 3, "FULFILLED", "Standard", line("GCS Tee TWC", GCS)),                            # done
        order("#3007", 1, "UNFULFILLED", "Standard", line("GCS Crew TWC", GCS)),                         # not in purchasing
        {**order("#3008", 1, "UNFULFILLED", "Standard", line("GCS Crew TWC", GCS)), "test": True},
        order("#3009", 40, "FULFILLED", "Standard", line("GCS Tee TWC", GCS)),                           # too old for done
    ]}))
(cache / "purchasing" / "snapshot.json").write_text(json.dumps({"at": ago(0.02), "ok": True, "proposal": {
    "lines": [{"sku": "B1", "sources": [{"key": "Steeple & Stitch|11", "order": "#3001", "qty": 2}]}],
    "other_suppliers": [],
    "tracked": [
        {"key": "Steeple & Stitch|21", "store": "Steeple & Stitch", "order": "#3002", "qty": 1,
         "status": "ordered", "ss_order": "777"},
        {"key": "Steeple & Stitch|31", "store": "Steeple & Stitch", "order": "#3003", "qty": 1,
         "status": "received", "ss_order": "776"},
        {"key": "Steeple & Stitch|41", "store": "Steeple & Stitch", "order": "#3004", "qty": 1,
         "status": "received", "ss_order": "776"},
        {"key": "TWC|51", "store": "The Williams Collective", "order": "#1050", "qty": 3,
         "order_created": ago(2), "status": "ordered", "ss_order": "778"},
        {"key": "manual:1", "store": "manual", "order": "Sample", "qty": 1, "status": "ordered"},
    ]}}))

ok_tick, _ = orders.tick("ss|#3004", "pressed", by="Larry")
check("ticking Pressed works", ok_tick)

b = orders.board(now=NOW)
lane = {c["name"]: c["lane"] for L in b["lanes"] for c in L["cards"]}
check("blanks still to buy -> New", lane.get("#3001") == "new")
check("blanks ordered, not all in -> Blanks ordered", lane.get("#3002") == "ordered")
check("every blank received -> Blanks in", lane.get("#3003") == "in")
check("a Pressed tick moves a ready order on", lane.get("#3004") == "pressed")
check("an in-house order Purchasing has not seen -> New, and says why",
      lane.get("#3007") == "new" and any("refresh Purchasing" in c["why"]
                                          for L in b["lanes"] for c in L["cards"] if c["name"] == "#3007"))
check("a POD-only order is not on the in-house lanes", "#3005" not in lane
      and [c["name"] for c in b["pod"]] == ["#3005"])
check("shipped orders go to Done; old ones drop off", [c["name"] for c in b["done"]] == ["#3006"])
check("test orders are never shown", "#3008" not in lane)
check("the TWC shop's open order comes from Purchasing", lane.get("#1050") == "ordered")
check("a manual sample is not an order", "Sample" not in lane)

cards = {c["name"]: c for L in b["lanes"] for c in L["cards"]}
check("pickup is read from the location name", cards["#3003"]["delivery"] == "pickup"
      and cards["#3002"]["delivery"] == "pickup" and cards["#3001"]["delivery"] == "ship")
check("in-house items counted apart from POD items",
      cards["#3003"]["inhouse_items"] == 1 and cards["#3003"]["item_count"] == 2)
check("age is in business days and the oldest sorts first",
      b["lanes"][0]["cards"][0]["name"] == "#3001" and cards["#3001"]["age"] >= 7)
check("late count is orders at 7+ business days", b["late"] == 1)
check("the S&S box is shown on the card", cards["#3002"]["boxes"] == ["S&S #777"])

# Shopify outranks a tick: once fulfilled, an order leaves the lanes whatever was ticked.
orders.tick("ss|#3006", "pressed")
check("a tick never keeps a shipped order on the board",
      "#3006" not in {c["name"] for L in orders.board(now=NOW)["lanes"] for c in L["cards"]})

# Packing implies pressing; undoing Pressed undoes Packed too.
orders.tick("ss|#3003", "packed", by="Larry")
t = orders.load_ticks()["ss|#3003"]
check("Packed without Pressed records Pressed as implied", t["pressed"].get("implied") and "packed" in t)
orders.tick("ss|#3003", "pressed", undo=True)
check("undoing Pressed clears Packed as well", "ss|#3003" not in orders.load_ticks())
check("only Pressed and Packed can be ticked", not orders.tick("ss|#3001", "shipped")[0])
check("a malformed reference is refused", not orders.tick("nonsense", "pressed")[0])

# Filters
ss_only = orders.board(business="twc", now=NOW)
check("business filter: TWC shows only the TWC shop",
      [c["name"] for L in ss_only["lanes"] for c in L["cards"]] == ["#1050"])
hoh = orders.board(partner=HOH, now=NOW)
check("store filter keeps only that partner's orders",
      {c["name"] for L in hoh["lanes"] for c in L["cards"]} == {"#3003", "#3004"})

# An old cache without fulfilment cannot say what is open; the page says so.
old = json.loads((cache / "shopify_sales.json").read_text())
for o in old["orders"]:
    o.pop("fulfillment", None)
(cache / "shopify_sales.json").write_text(json.dumps(old))
check("an old cache shows no in-house lanes and flags it", not orders.board(now=NOW)["has_fulfilment"])
(cache / "shopify_sales.json").write_text(json.dumps({**old, "orders": [
    {**o, "fulfillment": "UNFULFILLED"} for o in old["orders"]]}))

# 3.91: laser-engraved TWC orders (e.g. #1033, two cutting boards) need
# nothing bought, so they never wait on Purchasing and say "engraved".
from onboarding import twc_orders  # noqa: E402
twc_orders.CACHE.parent.mkdir(parents=True, exist_ok=True)
def tl(title, ptype="", **attrs):
    return {"title": title, "qty": 1, "vendor": "The Williams Collective",
            "product_type": ptype, "attrs": attrs}
def to(name, status, *lines):
    return {"name": name, "id": name[1:], "created_at": ago(2), "status": status,
            "fulfillment": "UNFULFILLED", "delivery": "Standard", "lines": list(lines)}
twc_orders.CACHE.write_text(json.dumps({"pulled_at": ago(0), "domain": "twc.myshopify.com", "orders": [
    to("#1033", "PAID", tl("Acacia Paddle Cutting Board", "Engraved Gifts"), tl("Acacia Paddle Cutting Board", "Engraved Gifts")),
    to("#1034", "PENDING", tl("Tumbler", "", Decoration="Laser engraving")),
    to("#1035", "PAID", tl("Crew Tee TWC", "T-Shirts"), tl("Slate Coaster", "Engraved Gifts")),
    to("#1036", "PAID", tl("Crew Tee TWC", "T-Shirts")),
]}))
tb = orders.board(now=NOW)
tc = {c["name"]: c for L in tb["lanes"] for c in L["cards"]}
check("engraved-only paid order skips Purchasing -> ready",
      tc["#1033"]["lane"] == "in" and tc["#1033"]["make"] == "engraved"
      and "nothing to buy" in tc["#1033"]["why"])
check("engraved by a manual line's Decoration, unpaid -> waits for payment",
      tc["#1034"]["make"] == "engraved" and tc["#1034"]["lane"] == "new" and "payment" in tc["#1034"]["why"])
check("print + engrave mix still waits on Purchasing, says 'made'",
      tc["#1035"]["make"] == "made" and tc["#1035"]["lane"] == "new")
check("a plain apparel order is unchanged", tc["#1036"]["make"] == "pressed"
      and "refresh Purchasing" in tc["#1036"]["why"])
eng_html = portal.app.test_client().get("/orders").get_data(as_text=True)
check("the engraved card's button says Mark engraved, with a Laser tag",
      "Mark engraved" in eng_html and ">Laser<" in eng_html and "Laser + print" in eng_html)
orders.tick("twc|#1033", "pressed", by="Larry")
check("ticking it moves it on and reads 'Engraved'",
      {c["name"]: c["lane"] for L in orders.board(now=NOW)["lanes"] for c in L["cards"]}["#1033"] == "pressed"
      and "✓ Engraved" in portal.app.test_client().get("/orders").get_data(as_text=True))
twc_orders.CACHE.unlink()

# 3.92: an order Purchasing lists under "need mapping" says so.
_p = json.loads((cache / "purchasing" / "snapshot.json").read_text()) if (cache / "purchasing" / "snapshot.json").is_file() else None
if _p is not None:
    _p["proposal"]["unmapped"] = [{"key": "TWC|91", "order": "#1050", "qty": 1, "title": "x"}]
    (cache / "purchasing" / "snapshot.json").write_text(json.dumps(_p))
    w = {c["name"]: c for L in orders.board(now=NOW)["lanes"] for c in L["cards"]}["#1050"]
    check("an order with lines needing mapping says so (not 'Not in Purchasing')",
          w["lane"] == "new" and "need" in w["why"] and "mapping" in w["why"])
    _p["proposal"]["unmapped"] = []
    (cache / "purchasing" / "snapshot.json").write_text(json.dumps(_p))
else:
    check("purchasing cache found for the mapping check", False)

# The page
client = portal.app.test_client()
page = client.get("/orders")
html = page.get_data(as_text=True)
check("/orders renders", page.status_code == 200 and "Blanks ordered" in html and "#3002" in html)
check("Orders is in the menu", 'href="/orders"' in client.get("/").get_data(as_text=True))
check("a card links to the order in Shopify",
      "https://check-store.myshopify.com/admin/orders/3002" in html)
r = client.post("/orders/tick", data={"key": "ss|#3002", "step": "pressed", "anchor": "o-ss-3002"},
                headers={"Host": "localhost"})
check("the tick button saves and returns to the card", r.status_code == 303
      and r.headers["Location"].endswith("/orders#o-ss-3002") and "pressed" in orders.load_ticks()["ss|#3002"])
r = client.post("/orders/tick", data={"key": "ss|#3002", "step": "packed"},
                headers={"Host": "localhost", "Origin": "https://evil.example"})
check("a tick posted from another site is refused", r.status_code == 403)
check("filters survive a tick", client.post("/orders/tick", data={
    "key": "ss|#3002", "step": "packed", "b": "ss", "p": GCS, "anchor": "x"},
    headers={"Host": "localhost"}).headers["Location"].startswith("/orders?b=ss&p="))
check("the Home tile now opens the orders board", 'href="/orders"' in client.get("/").get_data(as_text=True))
check("version is 3.70 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 70))
check("ticks live with the partner data, so the nightly backup keeps them",
      orders.TICKS.parent.parent == store.PARTNERS)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
