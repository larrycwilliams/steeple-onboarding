#!/usr/bin/env python3
"""Offline check: the "bought from" supplier dropdown (3.94).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_suppliers.py

Copies the app's CODE only (never .env*, partners/, cache/) into a temp folder.
ssorder is never run: the one call that would reach it is captured instead.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SKIP = {"partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}
tmp = Path(tempfile.mkdtemp(prefix="suppliers-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP
                                                        or n.startswith(".env") or n.endswith(".app")])
assert not any(p.name.startswith(".env") for p in work.rglob("*")), "a .env file was copied"
sys.path.insert(0, str(work))
os.environ.setdefault("SHOPIFY_STORE", "check-store.myshopify.com")

import app as portal  # noqa: E402
from onboarding import purchasing  # noqa: E402
assert Path(purchasing.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"

ok = fail = 0


def check(name, cond):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {name}")
    else:
        fail += 1
        print(f"  FAIL {name}")


now = datetime.now(timezone.utc).isoformat(timespec="seconds")
purchasing.STATE.mkdir(parents=True, exist_ok=True)
(purchasing.STATE / "snapshot.json").write_text(json.dumps({"at": now, "ok": True, "proposal": {
    "lines": [],
    "out_of_stock": [{"sku": "B00576993", "qty": 1, "desc": "Independent Trading Co. PRM180PT Deadwood Tree Camo S",
                      "reason": "only 0 available, need 1",
                      "sources": [{"key": "Steeple & Stitch|9", "order": "#1146", "qty": 1}]}],
    "other_suppliers": [{"label": "SanMar", "supplier": "SanMar", "supplier_url": "https://www.sanmar.com/p/31691",
                         "lines": [{"key": "The Williams Collective|8", "order": "#1145", "qty": 2, "age_days": 1,
                                    "title": "Basketball 1/4 Zip TWC", "variant_title": "Deep Black / XL"}]}],
    "tracked": [{"key": "x|1", "order": "#1", "qty": 1, "status": "ordered", "source": "other",
                 "supplier": "Jiffy Shirts", "supplier_order": "J-1"}]}}))

sup = [s["name"] for s in purchasing.suppliers(purchasing.view()["prop"], purchasing.view().get("groups") and
                                               json.loads((purchasing.STATE / "snapshot.json").read_text())["proposal"]["tracked"])]
check("the dropdown offers SanMar, Shirt Space and All Day Shirts, plus suppliers seen on the page",
      {"SanMar", "Shirt Space", "All Day Shirts", "Jiffy Shirts"} <= set(sup) and "S&S Activewear" not in sup)
urls = {s["name"]: s["url"] for s in purchasing.suppliers({})}
check("the saved suppliers carry their websites",
      urls.get("Shirt Space") == "https://www.shirtspace.com/" and urls.get("All Day Shirts") == "https://www.alldayshirts.com/")

portal.app.config["PROPAGATE_EXCEPTIONS"] = bool(os.environ.get("DUMP"))
client = portal.app.test_client()
html = client.get("/purchasing").get_data(as_text=True)
if os.environ.get("DUMP"): Path("/tmp/pu.html").write_text(html)
check("the out-of-stock card offers 'Bought these somewhere else?'",
      "Bought these somewhere else?" in html and 'name="instead" value="1"' in html
      and 'value="Steeple &amp; Stitch|9"' in html)
check("both forms have the dropdown with an Other… choice",
      html.count('class="pu-supplier"') == 2 and html.count('value="__other__"') == 2)
check("the SanMar group has SanMar preselected", 'value="SanMar" selected' in html)
check("each line has a Qty bought box, filled with the line's qty",
      'name="qty:The Williams Collective|8" value="2"' in html and 'name="qty:Steeple &amp; Stitch|9" value="1"' in html)

calls = []


def fake_edit(args, timeout=0):
    calls.append(args)
    return {"ok": True, "error": "", "changed": 1, "stale": "", "doc": {}}


purchasing._edit = fake_edit
host = {"Origin": "http://localhost"}
r = client.post("/purchasing/other/mark", headers=host, data={
    "key": "Steeple & Stitch|9", "instead": "1", "supplier": "Shirt Space",
    "supplier_order": "SS-123", "cost": "9.75", "qty:Steeple & Stitch|9": "1"})
check("out of stock -> Shirt Space: mark-line --instead with supplier, order #, cost and qty",
      r.status_code in (302, 303) and calls and calls[-1] == [
          "mark-line", "--supplier", "Shirt Space", "--supplier-order", "SS-123", "--cost", "9.75",
          "--qty", "Steeple & Stitch|9=1", "--instead", "Steeple & Stitch|9"])

client.post("/purchasing/other/mark", headers=host, data={
    "key": "The Williams Collective|8", "supplier": "__other__", "supplier_new": "Jiffy  Shirts Co",
    "supplier_url": "www.jiffyshirts.com", "qty:The Williams Collective|8": "2"})
check("Other… uses the typed name", calls[-1][:3] == ["mark-line", "--supplier", "Jiffy Shirts Co"]
      and "--instead" not in calls[-1])
saved = {s["name"]: s["url"] for s in purchasing.suppliers({})}
check("a new supplier is remembered for next time, with its website",
      saved.get("Jiffy Shirts Co") == "https://www.jiffyshirts.com" and "SanMar" in saved)

n = len(calls)
r = client.post("/purchasing/other/mark", headers=host, data={"key": "The Williams Collective|8",
                                                             "supplier": "__other__"})
check("Other… with no name is refused before anything runs", len(calls) == n and r.status_code in (302, 303))
check("version is 3.95 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 95))
r = client.post("/purchasing/other/mark", headers=host, data={"supplier": "SanMar"})
check("nothing ticked: refused, and sent to the top of the page where the message shows",
      len(calls) == n and r.status_code in (302, 303) and "#" not in r.headers["Location"])
check("other-supplier lines start ticked; out-of-stock lines do not",
      'value="The Williams Collective|8" checked' in html and 'value="Steeple &amp; Stitch|9" checked' not in html)

# The Other… box appears only when Other… is picked (a real browser).
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("  skip browser check (no playwright)")
else:
    page_html = html.replace('href="/static/', 'href="http://localhost/static/')
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.set_content(page_html)
        box = pg.locator('input[name="supplier_new"]').first
        hidden_before = not box.is_visible()
        pg.locator("select.pu-supplier").last.select_option("__other__")
        shown = pg.locator('input[name="supplier_new"]').last.is_visible()
        req = pg.locator('input[name="supplier_new"]').last.evaluate("e => e.required")
        # nothing ticked -> stays on the page with the message beside the button
        pg.set_content(page_html)
        frm = pg.locator("form:has(select.pu-supplier)").last
        frm.locator('input[name="key"]').uncheck()
        frm.locator("button:has-text('Mark ticked as ordered')").click()
        pg.wait_for_timeout(200)
        err = pg.locator(".pu-tickerr").last.inner_text() if pg.locator(".pu-tickerr").count() else ""
        qw = pg.locator("table input.pu-qty").last.evaluate("e => e.getBoundingClientRect().width")
        b.close()
    check("the New supplier box shows only when Other… is picked, and is then required",
          hidden_before and shown and req)
    check("clicking with nothing ticked says so beside the button", "Tick at least one line" in err)
    check("the Qty bought box is narrow", qw < 110)

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
