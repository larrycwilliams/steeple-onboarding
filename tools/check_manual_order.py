#!/usr/bin/env python3
"""Offline check of manual orders and Awaiting payment (Portal 3.90, doc 75).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_manual_order.py

Copies the app's CODE (never .env*, partners/, cache/ …) to a temp folder,
fakes Shopify and S&S, and drives the page through Flask's test client.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SKIP = {"partners", "cache", "output", "assets", "leads", "product_runs", ".git", "__pycache__",
        "Claude outputs", "cost_changes"}
tmp = Path(tempfile.mkdtemp(prefix="manual-order-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP or n.startswith(".env")
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))
os.environ["SHOPIFY_STORE"] = "check-ss.myshopify.com"
import app as portal  # noqa: E402
from onboarding import manual_order as mo, orders, purchasing, studio_product as sp, twc_orders, twc_shop  # noqa: E402

assert Path(mo.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"
for name in os.listdir(work):
    assert not name.startswith(".env"), "a .env file was copied"

ok = fail = 0


def check(name, cond):
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


class Form(dict):
    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([] if v is None else [v])


# ---- fakes
SCOPES = {"twc": ["read_orders", "write_draft_orders"], "ss": ["read_orders"]}
CALLS = []
VARIANTS = {"gid://shopify/ProductVariant/501": {"id": "gid://shopify/ProductVariant/501", "title": "M / Iris",
                                                  "price": "17.00",
                                                  "product": {"title": "NFG - Staff tee TWC", "status": "ACTIVE"}}}


def fake_gql(query, variables=None, store="ss"):
    variables = variables or {}
    CALLS.append((query.strip().split("(")[0].split("{")[0].strip(), variables, store))
    if "currentAppInstallation" in query:
        return {"ok": True, "data": {"currentAppInstallation": {"accessScopes": [{"handle": h} for h in SCOPES[store]]}}}
    if "nodes(ids" in query:
        return {"ok": True, "data": {"nodes": [VARIANTS.get(i) for i in variables["ids"]]}}
    if "products(first" in query:
        return {"ok": True, "data": {"products": {"nodes": [{"title": "NFG - Staff tee TWC", "status": "ACTIVE",
                                                              "variants": {"nodes": [{"id": "gid://shopify/ProductVariant/501",
                                                                                      "title": "M / Iris", "price": "17.00"}]}}]}}}
    if "draftOrderCreate" in query:
        return {"ok": True, "data": {"draftOrderCreate": {"userErrors": [], "draftOrder": {
            "id": "gid://shopify/DraftOrder/9001", "name": "#D7", "invoiceUrl": "https://pay.example/inv",
            "totalPriceSet": {"shopMoney": {"amount": "171.00"}}}}}}
    if "draftOrderInvoiceSend" in query:
        return {"ok": True, "data": {"draftOrderInvoiceSend": {"userErrors": [], "draftOrder": {"id": variables["id"], "status": "INVOICE_SENT"}}}}
    return {"ok": False, "error": f"unexpected {query[:40]}"}


sp._gql = fake_gql
twc_shop.configured = lambda: True
twc_shop.domain = lambda: "254cba-03.myshopify.com"
STYLE = {"Gildan 64000": {"ok": True, "error": "", "colors": ["Iris", "Natural", "Sport Grey"],
                          "sizes": ["S", "M", "L", "XL", "2XL", "3XL"]}}
purchasing.style_options = lambda s: STYLE.get(s, {"ok": False, "error": "S&S returned nothing", "colors": [], "sizes": []})


def form(**kw):
    base = {"store": "twc", "first": "Dana", "last": "Reyes", "company": "Reyes Plumbing",
            "email": "dana@example.com", "phone": "", "source": "email", "due": "2026-11-20", "po": "",
            "delivery": "pickup", "ship_price": "", "note": "Needs by the 20th",
            "l_kind": ["ss", "ss", "supplier", "product"],
            "l_variant": ["", "", "", "gid://shopify/ProductVariant/501"],
            "l_title": ["Crew tee — left chest logo", "Crew tee — left chest logo", "Rope hat — patch", ""],
            "l_style": ["Gildan 64000", "Gildan 64000", "", ""],
            "l_color": ["Iris", "Iris", "Black", ""], "l_size": ["M", "L", "OSFA", ""],
            "l_supplier": ["", "", "Lunar Headwear", ""], "l_supplier_url": ["", "", "https://lunarheadwear.com", ""],
            "l_qty": ["6", "4", "2", "1"], "l_price": ["12.50", "12.50", "28", ""],
            "l_decoration": ["DTF", "DTF", "Patch", "DTF"], "l_placement": ["Left chest 3 in", "Left chest 3 in", "Front", ""]}
    base.update(kw)
    return Form(base)


# ---- preview
p = mo.preview(form())
check("a mixed order previews clean: 2 S&S lines, 1 other supplier, 1 store product", p["ok"] and len(p["lines"]) == 4)
check("totals: 10 × 12.50 + 2 × 28 + 17 = $198", p["subtotal"] == 198.0 and p["shipping"] == 0.0)
l1 = p["lines"][0]
check("custom titles get the in-house ' TWC' suffix", l1["title"] == "Crew tee — left chest logo TWC")
a1 = {a["key"]: a["value"] for a in l1["attrs"]}
check("an S&S line carries its blank for ssorder (hidden) and colour/size for the customer",
      a1["_ss_style"] == "Gildan 64000" and a1["_ss_color"] == "Iris" and a1["_ss_size"] == "M"
      and a1["Colour"] == "Iris" and a1["Decoration"] == "DTF" and a1["_source"] == "email")
a3 = {a["key"]: a["value"] for a in p["lines"][2]["attrs"]}
check("an other-supplier line names its supplier for Purchasing", a3["_supplier"] == "Lunar Headwear"
      and a3["_supplier_url"] == "https://lunarheadwear.com" and "_ss_style" not in a3)
check("a store product uses the live Shopify price and title", p["lines"][3]["price"] == 17.0
      and p["lines"][3]["title"].startswith("NFG - Staff tee TWC"))
bad = mo.preview(form(l_color=["Teal", "Iris", "Black", ""], l_size=["M", "5XL", "OSFA", ""]))
check("colour and size are checked against S&S, not guessed",
      not bad["ok"] and any("“Teal” is not an S&S colour" in b for b in bad["blocking"])
      and any("“5XL” is not an S&S size" in b for b in bad["blocking"]))
bad = mo.preview(form(l_style=["Gildn 6400", "Gildan 64000", "", ""]))
check("an unknown S&S style blocks", any("does not know style" in b for b in bad["blocking"]))
bad = mo.preview(form(l_supplier=["", "", "", ""]))
check("an other-supplier line needs a supplier", any("name the supplier" in b for b in bad["blocking"]))
bad = mo.preview(form(email="", phone=""))
check("no email and no phone blocks", any("email" in b for b in bad["blocking"]))
w = mo.preview(form(email="", phone="937-555-0100"))
check("phone only: allowed, with a warning that Shopify cannot email the invoice",
      w["ok"] and any("cannot email the invoice" in x for x in w["warnings"]))
bad = mo.preview(form(l_qty=["0", "4", "2", "1"], l_price=["", "12.50", "28", ""]))
check("quantity and price are required", any("quantity" in b for b in bad["blocking"])
      and any("price each" in b for b in bad["blocking"]))
bad = mo.preview(form(**{k: ["", "", "", ""] for k in ("l_variant", "l_title", "l_style", "l_supplier", "l_price")}))
check("an order with no items blocks", any("at least one item" in b for b in bad["blocking"]))
SCOPES["twc"] = ["read_orders"]
bad = mo.preview(form())
check("without write_draft_orders: blocked, says how to fix", any("write_draft_orders" in b and "Reconnect" in b
                                                                   for b in bad["blocking"]))
SCOPES["twc"] = ["read_orders", "write_draft_orders"]

# ---- draft input
inp = mo.draft_input(p)
check("draft: custom lines priced in USD, taxable, need shipping",
      inp["lineItems"][0]["originalUnitPriceWithCurrency"] == {"amount": "12.50", "currencyCode": "USD"}
      and inp["lineItems"][0]["taxable"] and inp["lineItems"][0]["requiresShipping"])
check("draft: store product by variant id only", inp["lineItems"][3] == {"variantId": "gid://shopify/ProductVariant/501",
                                                                          "quantity": 1})
check("draft: tagged manual-order + source, due date and customer on the order",
      inp["tags"] == ["manual-order", "source:email"] and {"key": "Due date", "value": "2026-11-20"} in inp["customAttributes"]
      and inp["email"] == "dana@example.com" and inp["billingAddress"]["company"] == "Reyes Plumbing")
check("draft: pickup is a free shipping line", inp["shippingLine"]["title"].startswith("Pickup")
      and inp["shippingLine"]["priceWithCurrency"]["amount"] == "0.00")
fl = mo.draft_input(mo.preview(form(delivery="flat", ship_price="9.95")))
check("draft: flat shipping carries its amount", fl["shippingLine"]["priceWithCurrency"]["amount"] == "9.95")
ck = mo.draft_input(mo.preview(form(delivery="checkout")))
check("draft: 'customer picks at checkout' sends no shipping line", "shippingLine" not in ck)

# ---- create + invoice
CALLS.clear()
r = mo.create(form(), "wrong", "Larry")
check("create refuses a stale preview", not r["ok"] and not any(c[0] == "mutation D" for c in CALLS))
r = mo.create(form(), p["fingerprint"], "Larry")
check("create makes the draft in the chosen store and records it",
      r["ok"] and r["record"]["name"] == "#D7" and any(c[0] == "mutation D" and c[2] == "twc" for c in CALLS)
      and mo.records()[-1]["admin_url"] == "https://254cba-03.myshopify.com/admin/draft_orders/9001")
s = mo.send_invoice("gid://shopify/DraftOrder/9001", "Thanks Dana!", "Larry")
inv = next(c for c in CALLS if c[0] == "mutation I")[1]
check("send invoice emails the customer with the message, and is recorded",
      s["ok"] and inv["email"]["to"] == "dana@example.com" and inv["email"]["customMessage"] == "Thanks Dana!"
      and mo.find("gid://shopify/DraftOrder/9001")["invoice_sent"])
check("an unknown draft id is refused", not mo.send_invoice("gid://shopify/DraftOrder/1", "", "x")["ok"])

# ---- the page
c = portal.app.test_client()
html = c.get("/manual-order").get_data(as_text=True)
check("page: customer, items, delivery and recent orders", all(x in html for x in
      ("Customer", "Items", "Custom on an S&amp;S blank", "Recent manual orders", "#D7", "Payment link")))
html = c.post("/manual-order", data={**{k: v for k, v in form().items()}, "action": "preview"}).get_data(as_text=True)
check("page: Preview shows the totals and a Create button", "$198.00" in html and 'value="create"' in html)
j = c.get("/manual-order/products?store=twc&q=staff").get_json()
check("product search returns variants with prices", j and j[0]["label"] == "NFG - Staff tee TWC — M / Iris")

# ---- the board: TWC orders pulled directly, Awaiting payment
NOW = datetime.now(timezone.utc)


def tline(title, qty=1, attrs=None, vendor="NFG - National Fuel"):
    return {"title": title, "qty": qty, "qty_ordered": qty, "vendor": vendor, "vendor_at_sale": vendor,
            "product_id": "", "variant_missing": False, "product_status": "ACTIVE", "unit_price": 17.0,
            "unit_cost": 6.9, "attrs": attrs or {}}


snap = {"pulled_at": NOW.isoformat(), "domain": "254cba-03.myshopify.com", "locations": ["5351 Logan Ave"],
        "orders": [
            {"name": "#1201", "id": "11", "created_at": (NOW - timedelta(days=1)).isoformat(), "test": False,
             "cancelled": False, "status": "PENDING", "fulfillment": "UNFULFILLED", "delivery": "5351 Logan Ave",
             "lines": [tline("Crew tee TWC", 6, {"_ss_style": "Gildan 64000", "_source": "email"}, vendor="")]},
            {"name": "#1202", "id": "12", "created_at": (NOW - timedelta(days=2)).isoformat(), "test": False,
             "cancelled": False, "status": "PAID", "fulfillment": "UNFULFILLED", "delivery": "Standard",
             "lines": [tline("NFG - Staff tee TWC", 2)]},
            {"name": "#1199", "id": "13", "created_at": (NOW - timedelta(days=3)).isoformat(), "test": False,
             "cancelled": False, "status": "PAID", "fulfillment": "FULFILLED", "delivery": "Standard",
             "lines": [tline("NFG - Staff tee TWC", 1)]}]}
twc_orders.CACHE.parent.mkdir(parents=True, exist_ok=True)
twc_orders.CACHE.write_text(json.dumps(snap))
b = orders.board()
cards = {c["name"]: c for lane in b["lanes"] for c in lane["cards"]}
check("TWC orders come from the TWC store itself (not only via Purchasing)", "#1201" in cards and "#1202" in cards)
check("an unpaid order says Awaiting payment; a paid one does not",
      cards["#1201"]["payment"] == "Awaiting payment" and cards["#1202"]["payment"] == "")
check("a manual order is marked with where it came from", cards["#1201"]["manual"] == "email")
check("unpaid, nothing bought yet: the card says blanks wait for payment",
      "Waiting for payment" in cards["#1201"]["why"])
check("TWC cards link to the TWC admin, with pickup and the business's name",
      cards["#1202"]["admin_url"] == "https://254cba-03.myshopify.com/admin/orders/12"
      and cards["#1201"]["delivery"] == "pickup" and cards["#1202"]["who"] == "NFG - National Fuel")
check("a fulfilled TWC order is in Done, not a lane", "#1199" not in cards and any(d["name"] == "#1199" for d in b["done"]))
check("the header counts orders awaiting payment", b["unpaid"] == 1)
html = c.get("/orders").get_data(as_text=True)
check("board page: Awaiting payment tag, Manual tag, + Manual order button",
      "Awaiting payment" in html and "Manual · email" in html and "+ Manual order" in html
      and "1 awaiting payment" in html)
# ---- in a real browser: add item, switch type, remove, submit keeps every row aligned
try:
    import threading
    from playwright.sync_api import sync_playwright
    from werkzeug.serving import make_server
    portal.app.config["TESTING"] = True
    srv = make_server("127.0.0.1", 0, portal.app, threaded=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    with sync_playwright() as pw:
        br = pw.chromium.launch()
        pg = br.new_page(viewport={"width": 390, "height": 844})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(base + "/manual-order")
        pg.fill("#first", "Dana"); pg.fill("#email", "dana@example.com")
        first = pg.locator(".mo-line").nth(0)
        first.locator("[name=l_title]").fill("Crew tee — left chest logo")
        first.locator("[name=l_style]").fill("Gildan 64000")
        first.locator("[name=l_color]").fill("Iris"); first.locator("[name=l_size]").fill("M")
        first.locator("[name=l_qty]").fill("6"); first.locator("[name=l_price]").fill("12.50")
        pg.click("#mo-add")
        second = pg.locator(".mo-line").nth(1)
        check("browser: + Add item copies the name and style, clears size/qty",
              second.locator("[name=l_title]").input_value() == "Crew tee — left chest logo"
              and second.locator("[name=l_style]").input_value() == "Gildan 64000"
              and second.locator("[name=l_size]").input_value() == "" and second.locator("[name=l_qty]").input_value() == "1")
        second.locator("[name=l_size]").fill("L"); second.locator("[name=l_color]").fill("Iris")
        second.locator("[name=l_qty]").fill("4"); second.locator("[name=l_price]").fill("12.50")
        pg.click("#mo-add")
        third = pg.locator(".mo-line").nth(2)
        third.locator("input[value=supplier]").check()
        check("browser: switching an item to 'other supplier' shows the supplier field",
              third.locator("[name=l_supplier]").is_visible() and not third.locator("[name=l_style]").is_visible())
        third.locator("[name=l_title]").fill("Rope hat — patch")
        third.locator("[name=l_supplier]").fill("Lunar Headwear")
        third.locator("[name=l_size]").fill("OSFA"); third.locator("[name=l_color]").fill("Black")
        third.locator("[name=l_qty]").fill("2"); third.locator("[name=l_price]").fill("28")
        pg.click("#mo-add")
        pg.locator(".mo-line").nth(3).locator("[data-remove]").click()
        check("browser: Remove takes an item off and renumbers",
              pg.locator(".mo-line").count() == 3 and pg.locator(".mo-line-top b").nth(2).inner_text() == "Item 3")
        pg.click("button[value=preview]")
        pg.wait_for_selector(".mo-sum")
        txt = pg.inner_text(".mo-sum")
        check("browser: Preview gets all three items intact ($75 + $50 + $56 = $181)",
              "$181.00" in txt and "Lunar" in txt and txt.count("Gildan 64000") == 2)
        check("browser: no script errors", not errors)
        br.close()
    srv.shutdown()
except ImportError:
    print("  (playwright not installed here: browser checks skipped)")

check("version is 3.90 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 90))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
