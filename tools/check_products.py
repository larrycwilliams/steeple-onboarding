#!/usr/bin/env python3
"""Offline check of the partner Products page (doc 71).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_products.py

Copies the app's CODE (never .env, partners/, cache/) into a temp folder, writes
made-up partners, products and orders there, and drives the page through Flask's
test client. Nothing real is read or written, and Shopify is never called.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

tmp = Path(tempfile.mkdtemp(prefix="products-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP or n.startswith(".env")
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))

import app as portal  # noqa: E402
from onboarding import products, shopify_sales, store  # noqa: E402

assert Path(store.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"
ok = fail = 0


def check(name: str, cond) -> None:
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


GCS, ATH, REV = "Germantown Christian School", "GCS Athletics", "Revive Church"
gcs = store.save({"org_name": GCS, "shopify_vendor": GCS, "plan": "Growth"})
ath = store.save({"org_name": ATH, "org_legal_name": GCS, "plan": "Growth"})   # inherits GCS's name
rev = store.save({"org_name": REV, "plan": "Growth"})                          # vendor guessed from name
store.save({"org_name": "Seed", "_seed_check": True, "shopify_vendor": REV})
pid = {n: store.partner_id(r) for n, r in ((GCS, gcs), (ATH, ath), (REV, rev))}


def prod(n, vendor, status="ACTIVE", cost=9.0, lo=24.0, hi=None, **kw):
    return {"id": f"gid://shopify/Product/{n}", "title": kw.pop("title", f"Item {n} TWC"), "vendor": vendor, "status": status,
            "unit_cost": cost, "unit_costs": [cost] if cost is not None else [], "handle": f"item-{n}",
            "type": "T-Shirts", "variants": 6, "price_min": lo, "price_max": hi if hi is not None else lo,
            "image": f"https://cdn.example/{n}.jpg", "updated_at": "2026-11-01T12:00:00Z", **kw}


catalog = [prod(1, GCS), prod(2, GCS, cost=None), prod(3, GCS, "DRAFT"), prod(4, GCS, "ARCHIVED"),
           prod(5, GCS, cost=30.0, lo=28.0, hi=32.0), prod(6, REV), prod(7, "Somebody Else"),
           prod(9, GCS, title="Item 9 Printify Mug"), prod(10, GCS, title="TWC Logo Tee (POD)")]
old = {k: v for k, v in prod(8, REV).items() if k in ("id", "title", "vendor", "status", "unit_cost", "unit_costs")}
catalog.append(old)                                     # a product from a cache written before 3.76


def line(n, qty, price, vendor=GCS):
    return {"title": f"Item {n} TWC", "qty": qty, "qty_ordered": qty, "vendor": vendor,
            "product_id": f"gid://shopify/Product/{n}", "unit_price": price, "unit_cost": 9.0}


orders = [{"name": "#1001", "id": "1", "created_at": "2026-10-02T15:00:00Z", "test": False, "cancelled": False,
           "lines": [line(1, 3, 20.0), line(2, 1, 24.0)]},
          {"name": "#1002", "id": "2", "created_at": "2026-11-05T15:00:00Z", "test": False, "cancelled": False,
           "lines": [line(1, 2, 24.0)]},
          {"name": "#1003", "id": "3", "created_at": "2026-11-06T15:00:00Z", "test": True, "cancelled": False,
           "lines": [line(1, 50, 24.0)]},
          {"name": "#1004", "id": "4", "created_at": "2026-11-07T15:00:00Z", "test": False, "cancelled": True,
           "lines": [line(1, 50, 24.0)]},
          {"name": "#1005", "id": "5", "created_at": "2026-11-08T15:00:00Z", "test": False, "cancelled": False,
           "lines": [line(1, 0, 24.0)]}]                 # fully refunded line
cache = work / "cache"
cache.mkdir(exist_ok=True)
(cache / "shopify_sales.json").write_text(json.dumps({
    "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "orders": orders,
    "catalog": catalog, "collections": [], "locations": []}))

g = products.for_partner(pid[GCS])
titles = [p["title"] for p in g["products"]]
check("products are matched by vendor: GCS gets its five, nobody else's", sorted(titles) ==
      ["Item 1 TWC", "Item 2 TWC", "Item 3 TWC", "Item 4 TWC", "Item 5 TWC"])
check("the explicit shopify_vendor beats GCS Athletics' inherited name", products.for_partner(pid[ATH])["total"] == 0)
check("a vendor guessed from the org name still matches", [p["title"] for p in products.for_partner(pid[REV])["products"]]
      == ["Item 6 TWC", "Item 8 TWC"])
check("live first, best sellers first", titles[0] == "Item 1 TWC" and titles[-1] == "Item 4 TWC")
check("counts by status", g["counts"] == {"ACTIVE": 3, "DRAFT": 1, "ARCHIVED": 1})
i1 = next(p for p in g["products"] if p["title"] == "Item 1 TWC")
check("units sold leave out test, cancelled and refunded lines", i1["units"] == 5)
check("sales use the after-discount price", i1["revenue"] == "$108.00")
check("last sold date comes from the latest real order", i1["last_sold"] == "Nov 5, 2026")
check("margin is lowest price minus cost", i1["margin"] == "$15.00" and not i1["margin_bad"])
i5 = next(p for p in g["products"] if p["title"] == "Item 5 TWC")
check("a price range shows low–high, and cost above price is flagged", i5["price"] == "$28.00–$32.00"
      and i5["margin"] == "-$2.00" and i5["margin_bad"])
i2 = next(p for p in g["products"] if p["title"] == "Item 2 TWC")
check("live with no cost: cost and margin are dashes, never $0", i2["cost"] == "—" and i2["margin"] == "—"
      and g["no_cost"] == 1)
check("summary units and sales across the partner", g["units"] == 6 and g["revenue"] == "$132.00")
check("links: Shopify admin by number, storefront only when live",
      i1["admin_url"].endswith("/admin/products/1") or not g["shop"])
check("drafts have no storefront link", all(not p["store_url"] for p in g["products"] if p["status"] != "ACTIVE")
      and i1["store_url"].endswith("/products/item-1"))
check("filter: live only", [p["status"] for p in products.for_partner(pid[GCS], "active")["products"]] == ["ACTIVE"] * 3)
r8 = next(p for p in products.for_partner(pid[REV])["products"] if p["title"] == "Item 8 TWC")
check("a product from an older cache shows dashes, not errors", r8["price"] == "—" and r8["image"] == "")
check("only titles ending TWC are listed; POD products are counted, not shown",
      g["pod_hidden"] == 2 and all(t.endswith(" TWC") for t in titles))
check("the seed check record is not a partner here", products.for_partner("seed") is None
      or products.for_partner("seed")["total"] == 0)

before = sorted(p.name for p in store.PARTNERS.glob("*.json"))
snap = {p.name: p.read_text() for p in store.PARTNERS.glob("*.json")}
client = portal.app.test_client()
page = client.get(f"/partner/{pid[GCS]}/products")
html = page.get_data(as_text=True)
check("/partner/<pid>/products renders", page.status_code == 200 and "Item 1 TWC" in html and "Item 7" not in html and "Printify Mug" not in html and "2 POD products" in html)
check("page shows images, filters and Shopify links", "cdn.example/1.jpg" in html and "?show=draft" in html
      and "Edit in Shopify" in html or "View in store" in html)
check("unknown partner is a 404", client.get("/partner/nope/products").status_code == 404)
check("a bad filter falls back to all", client.get(f"/partner/{pid[GCS]}/products?show=zzz").status_code == 200)
check("the partner records are read, never written",
      sorted(p.name for p in store.PARTNERS.glob("*.json")) == before
      and all(p.read_text() == snap[p.name] for p in store.PARTNERS.glob("*.json")))
check("Programs, the record and Files link to Products",
      f"/partner/{pid[GCS]}/products" in client.get("/programs").get_data(as_text=True)
      and f"/partner/{pid[GCS]}/products" in client.get(f"/partner/{pid[GCS]}").get_data(as_text=True)
      and f"/partner/{pid[GCS]}/products" in client.get(f"/partner/{pid[GCS]}/files").get_data(as_text=True))

(cache / "shopify_sales.json").unlink()
bare = client.get(f"/partner/{pid[GCS]}/products")
check("no Shopify cache: the page says so, nothing crashes", bare.status_code == 200
      and "No Shopify data on file" in bare.get_data(as_text=True))

check("catalog pull asks for price, image, handle and variant count",
      all(f in shopify_sales.CATALOG_QUERY for f in ("priceRangeV2", "featuredMedia", "handle", "variantsCount")))
clean = shopify_sales._clean_product({"id": "gid://shopify/Product/9", "title": "T", "vendor": "V", "status": "ACTIVE",
    "handle": "t", "productType": "Tee", "variantsCount": {"count": 4},
    "priceRangeV2": {"minVariantPrice": {"amount": "22.0"}, "maxVariantPrice": {"amount": "26.0"}},
    "featuredMedia": {"preview": {"image": {"url": "https://cdn/x.jpg"}}},
    "variants": {"edges": [{"node": {"inventoryItem": {"unitCost": {"amount": "8.5"}}}}]}})
check("a pulled product keeps the new fields", clean["price_min"] == 22.0 and clean["price_max"] == 26.0
      and clean["image"] == "https://cdn/x.jpg" and clean["variants"] == 4 and clean["unit_cost"] == 8.5)
check("a product with no image or price still cleans", shopify_sales._clean_product(
      {"id": "x", "title": "T", "featuredMedia": None, "priceRangeV2": None})["image"] == "")
check("version is 3.77 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 77))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
