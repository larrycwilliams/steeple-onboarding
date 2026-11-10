#!/usr/bin/env python3
"""Offline check of Create Shopify product from a Studio item (doc 72).

    hub:  cd ~/Dev/steeple-onboarding
          ~/.venvs/steeple-onboarding-312/bin/python tools/check_studio_product.py

Copies the app's CODE (never .env, partners/, cache/, product_runs/) into a temp
folder and stands in for the Studio, Shopify and pod2twc's config. Nothing real
is read, written or called -- every Shopify call is answered by a fake that
records it, so the test can assert exactly what would have been sent.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
SKIP = {".env", "partners", "cache", "output", "assets", "leads", "product_runs", ".git",
        "__pycache__", "Claude outputs", "cost_changes"}

tmp = Path(tempfile.mkdtemp(prefix="studio-product-check-"))
work = tmp / "app"
shutil.copytree(APP_ROOT, work, ignore=lambda d, names: [n for n in names if n in SKIP
                                                        or n.endswith(".app")])
sys.path.insert(0, str(work))

import app as portal  # noqa: E402
from onboarding import clone, store, studio_product as sp  # noqa: E402

assert Path(store.__file__).resolve().is_relative_to(work.resolve()), "imported the real app"
ok = fail = 0


def check(name: str, cond) -> None:
    global ok, fail
    print(("  ok   " if cond else "  FAIL ") + name)
    ok, fail = ok + bool(cond), fail + (not cond)


# ---------------------------------------------------------------- the fakes
HOUSE, GCS_PICKUP, REVIVE, NINJA = ("gid://shopify/Location/1", "gid://shopify/Location/2",
                                    "gid://shopify/Location/3", "gid://shopify/Location/4")
cfg = {"house_location": HOUSE, "publications": ["gid://shopify/Publication/9"],
       "store_url_base": "https://www.steepleandstitch.com/collections/",
       "orgs": {"hoh": {"vendor": "Highway of Holiness", "tag": "highway-of-holiness",
                        "collection_id": "gid://shopify/Collection/77", "collection_handle": "highway-of-holiness",
                        "store_name": "Highway of Holiness", "pickup_locations": []},
                "haven-of-hope": {"vendor": "Haven of Hope", "tag": "haven-of-hope",
                                  "collection_id": "gid://shopify/Collection/88", "collection_handle": "haven-of-hope",
                                  "store_name": "Haven of Hope", "pickup_locations": []},
                "gcs": {"vendor": "Germantown Christian School", "tag": "GCS",
                        "collection_id": "gid://shopify/Collection/66", "collection_handle": "gcs-cardinals",
                        "store_name": "Germantown Christian School", "pickup_locations": [GCS_PICKUP]}}}
(tmp / "config.json").write_text(json.dumps(cfg))
clone.CONFIG = tmp / "config.json"
sp.configured = lambda: True
sp._load_env = lambda: None

HOH = store.save({"org_name": "Haven of Hope", "shopify_vendor": "Haven of Hope", "short_name": "HOH",
                  "org_type": "church"})
GCS = store.save({"org_name": "Germantown Christian School", "shopify_vendor": "Germantown Christian School",
                  "short_name": "GCS"})
pid = {"hoh": store.partner_id(HOH), "gcs": store.partner_id(GCS)}


def sizes(*pairs):
    return [{"size": s, "piece": p, "price": p + 1, "weight": w} for s, p, w in pairs]


TEE = sizes(("S", 4.10, 0.40), ("M", 4.10, 0.44), ("L", 4.10, 0.48), ("XL", 4.10, 0.52),
            ("2XL", 6.10, 0.58), ("3XL", 7.40, 0.64))


def export(status="approved", portal_pid=None, shopify="", mockups=True, ss=True, colors=None):
    return {"ok": True, "item": {"id": 7, "code": "TS-01", "name": "Logo Crewneck", "category": "tshirt",
                                 "method": "dtf", "status": status, "ready": status in sp_ready,
                                 "approved_by": "Pastor Ray", "blank": "Comfort Colors 1717",
                                 "color": "White", "ss_style": "Comfort Colors 1717", "shopify_product": shopify},
            "project": {"id": 1, "org_name": "Haven of Hope", "project_name": "Fall", "brand": "ss",
                        "portal_pid": pid["hoh"] if portal_pid is None else portal_pid},
            "mockups": ([{"view": "front", "url": "/api/items/7/mockup/front.png", "preview": "/m/front.png"},
                         {"view": "back", "url": "/api/items/7/mockup/back.png", "preview": "/m/back.png"}]
                        if mockups else []),
            "ss": ({"style": "Comfort Colors 1717", "title": "Heavyweight Tee",
                    "colors": colors or [{"name": "White", "hex": "#fff", "sizes": TEE},
                                         {"name": "Pepper", "hex": "#555", "sizes": TEE}]} if ss else None)}


sp_ready = ("approved", "production", "complete")
EXPORT = {"data": export()}
sp.fetch_item = lambda iid: (EXPORT["data"], "")
sp.fetch_mockup = lambda url: b"\x89PNG fake " + url.encode()
TOLD = []
sp.tell_studio = lambda iid, product, title, by: TOLD.append((iid, product, title, by)) or ""

CALLS: list[tuple[str, dict]] = []
SHOP = {"scopes": ["write_products", "write_inventory", "write_publications", "write_shipping",
                   "read_metaobjects"],
        "dupes": [], "profile": {"id": "gid://shopify/DeliveryProfile/5", "name": "Haven of Hope – Freight"},
        "swatches": {"White": "gid://shopify/Metaobject/1", "Pepper": "gid://shopify/Metaobject/2"},
        "inhouse_def": True, "fail_create": "", "kids": True}


def fake_gql(query, variables=None):
    variables = variables or {}
    CALLS.append((query.split("(")[0].split("{")[0].strip(), variables))
    q = query
    if "query Ctx" in q:
        return {"ok": True, "data": {
            "currentAppInstallation": {"accessScopes": [{"handle": h} for h in SHOP["scopes"]]},
            "collection": {"id": variables["col"], "title": "HAVEN OF HOPE", "handle": "haven-of-hope",
                           "sortOrder": "MANUAL",
                           "metafield": {"value": json.dumps(["gid://shopify/Collection/101",
                                                              "gid://shopify/Collection/102"])} if SHOP["kids"] else None},
            "locations": {"nodes": [{"id": HOUSE, "name": "Shop - Dayton", "isActive": True},
                                    {"id": GCS_PICKUP, "name": "Germantown Christian School", "isActive": True},
                                    {"id": REVIVE, "name": "Revive Church", "isActive": True},
                                    {"id": NINJA, "name": "Ninja POD", "isActive": True}]},
            "metafieldDefinitions": {"nodes": [{"key": "church_store"}] + (
                [{"key": "inhouse_only"}] if SHOP["inhouse_def"] else [])}}}
    if "query Kids" in q:
        return {"ok": True, "data": {"nodes": [
            {"id": "gid://shopify/Collection/101", "title": "HAVEN OF HOPE – Women's Ministry",
             "ruleSet": {"rules": [{"column": "TAG", "relation": "EQUALS", "condition": "ministry:womens"}]},
             "resourcePublicationsV2": {"nodes": [{"isPublished": True}]}},
            {"id": "gid://shopify/Collection/102", "title": "HAVEN OF HOPE – Youth",
             "ruleSet": {"rules": [{"column": "TAG", "relation": "EQUALS", "condition": "ministry:youth"}]},
             "resourcePublicationsV2": {"nodes": [{"isPublished": False}]}}]}}
    if "query Vendor" in q:
        return {"ok": True, "data": {
            "siblings": {"nodes": [{"id": "gid://shopify/Product/1", "title": "HOH - Hoodie TWC", "status": "ACTIVE",
                                    "variants": {"nodes": [{"deliveryProfile": SHOP["profile"]}]}}]},
            "dupes": {"nodes": SHOP["dupes"]}}}
    if "query Swatches" in q:
        return {"ok": True, "data": {"metaobjects": {"nodes": [
            {"id": g, "field": {"value": label}} for label, g in SHOP["swatches"].items()],
            "pageInfo": {"hasNextPage": False}}}}
    if "stagedUploadsCreate" in q:
        n = variables["input"][0]["filename"]
        return {"ok": True, "data": {"stagedUploadsCreate": {"userErrors": [], "stagedTargets": [
            {"url": "https://upload.example/", "resourceUrl": f"https://upload.example/{n}",
             "parameters": [{"name": "key", "value": n}]}]}}}
    if "productSet" in q:
        if SHOP["fail_create"]:
            return {"ok": True, "data": {"productSet": {"product": None,
                                                        "userErrors": [{"field": ["x"], "message": SHOP["fail_create"]}]}}}
        inp = variables["input"]
        return {"ok": True, "data": {"productSet": {"userErrors": [], "product": {
            "id": "gid://shopify/Product/555", "handle": "hoh-logo-crewneck-twc", "title": inp["title"],
            "variants": {"nodes": [{"id": f"gid://shopify/ProductVariant/{i}", "title": "",
                                    "inventoryItem": {"id": f"gid://shopify/InventoryItem/{i}", "inventoryLevels": {
                                        "nodes": [{"location": {"id": HOUSE}}, {"location": {"id": GCS_PICKUP}},
                                                  {"location": {"id": REVIVE}}]}}}
                                   for i, _ in enumerate(inp["variants"])]}}}}}
    if "inventoryBulkToggleActivation" in q:
        return {"ok": True, "data": {"inventoryBulkToggleActivation": {"userErrors": []}}}
    if "deliveryProfileUpdate" in q:
        return {"ok": True, "data": {"deliveryProfileUpdate": {"userErrors": []}}}
    if "publishablePublish" in q:
        return {"ok": True, "data": {"publishablePublish": {"userErrors": []}}}
    return {"ok": False, "error": f"unexpected call: {q[:60]}"}


sp._gql = fake_gql


class _Resp:
    status_code = 204


UPLOADS = []
sp.requests.post = lambda url, data=None, files=None, timeout=None: UPLOADS.append((url, data, files)) or _Resp()


class Form(dict):
    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([] if v is None else [v])


def form(**kw):
    base = {"title": "HOH - Logo Crewneck TWC", "price": "22", "compare_at": "", "cost": "11",
            "fallback_weight": "", "description": "<p>Logo Crewneck.</p>",
            "up_2XL": "2", "up_3XL": "3", "up_4XL": "4", "up_5XL": "5", "up_6XL": "6",
            "colors_sent": "1", "sizes_sent": "1", "ministries_sent": "1",
            "colors": ["White"], "sizes": ["S", "M", "L", "XL", "2XL", "3XL"],
            "ministries": ["ministry:womens"], "status": "DRAFT"}
    base.update(kw)
    return Form(base)


# ---------------------------------------------------------------- preview
d = sp.preview(7, form())
p = d["plan"]
check("clean preview: nothing blocks", d["ok"] and not d["blocking"])
check("vendor, collection and org tag come from pod2twc's registry",
      p["vendor"] == "Haven of Hope" and p["collection"]["id"] == "gid://shopify/Collection/88"
      and p["tags"] == ["haven-of-hope", "ministry:womens"])
check("church metafields: store, its name and its URL; marked in-house only",
      {m["key"]: m["value"] for m in p["metafields"]} == {
          "church_store": "gid://shopify/Collection/88", "church_store_name": "Haven of Hope",
          "church_store_url": "https://www.steepleandstitch.com/collections/haven-of-hope", "inhouse_only": "true"})
check("six variants, price ladder applied", [v["price"] for v in p["variants"]] == [22, 22, 22, 22, 24, 25])
check("cost follows the blank: base cost plus S&S's extra for big sizes",
      [v["cost"] for v in p["variants"]] == [11, 11, 11, 11, 13, 14.3])
check("weights come from S&S, per size", [v["weight"] for v in p["variants"]][:2] == [0.40, 0.44])
check("stocked at the house location only (Haven has no pickup)", p["keep"] == [HOUSE])
check("delivery profile copied from the church's other products", p["profile"]["name"].startswith("Haven"))
check("only published ministries are offered", [m["tag"] for m in d["ministries"]] == ["ministry:womens"])
check("manual sort order is a warning, not a block", any("sorted by hand" in w for w in d["warnings"]))
check("default status is draft", sp.preview(7, None)["form"]["status"] == "DRAFT")
check("defaults: title from the short name, the item's colour, all its sizes",
      sp.preview(7, None)["form"]["title"] == "HOH - Logo Crewneck TWC"
      and sp.preview(7, None)["form"]["colors"] == ["White"] and "3XL" in sp.preview(7, None)["form"]["sizes"])
check("no writes during preview", not any(n.startswith("mutation") for n, _ in CALLS))


def blocks(text, **kw):
    r = sp.preview(7, form(**kw))
    return any(text in b for b in r["blocking"])


check("blocks: missing cost", blocks("loaded in-house cost", cost=""))
check("blocks: missing price", blocks("retail price", price=""))
check("blocks: cost at or above price", blocks("at or above the price", cost="22"))
check("blocks: no colour chosen", blocks("at least one colour", colors=[]))
EXPORT["data"] = export(status="sent")
check("blocks: an item the customer has not approved", blocks("Only an item the customer approved"))
EXPORT["data"] = export(portal_pid="")
check("blocks: project not linked to a partner", blocks("not linked to a Portal partner"))
EXPORT["data"] = export(shopify="gid://shopify/Product/9")
check("blocks: item already in Shopify", blocks("already in Shopify"))
EXPORT["data"] = export(ss=False)
check("blocks: no S&S style, so no sizes or weights", blocks("not linked to an S&S style"))
EXPORT["data"] = export(colors=[{"name": "White", "hex": "#fff",
                                 "sizes": sizes(("S", 4.1, None), ("M", 4.1, 0.44))}])
check("blocks: S&S has no weight and no fallback given", blocks("gave no weight", sizes=["S", "M"]))
r = sp.preview(7, form(sizes=["S", "M"], fallback_weight="0.4"))
check("a fallback weight fills the gap, with a warning", r["ok"] and any("using 0.4 lb" in w for w in r["warnings"])
      and r["plan"]["variants"][0]["weight"] == 0.4)
EXPORT["data"] = export()
SHOP["dupes"] = [{"id": "x", "title": "HOH - Logo Crewneck TWC", "status": "ACTIVE"}]
check("blocks: a product with the same title already exists", blocks("already has a product called"))
SHOP["dupes"] = []
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1"}
check("blocks: a colour with no swatch metaobject, named", blocks("No Shopify colour swatch is called “Pepper”",
                                                                  colors=["White", "Pepper"]))
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1", "Pepper": "gid://shopify/Metaobject/2"}
SHOP["scopes"] = ["write_products", "write_inventory"]
r = sp.preview(7, form())
check("missing soft scopes warn and skip, never block",
      r["ok"] and sum("The token lacks" in w for w in r["warnings"]) == 3 and r["plan"]["swatch"] == {})
SHOP["scopes"] = ["read_products"]
check("blocks: token cannot write products", blocks("lacks write_products, write_inventory"))
SHOP["scopes"] = ["write_products", "write_inventory", "write_publications", "write_shipping", "read_metaobjects"]
EXPORT["data"]["project"]["portal_pid"] = pid["gcs"]
r = sp.preview(7, form(title="GCS - Logo Crewneck TWC"))
check("a church with local pickup stays stocked at its pickup point too", r["plan"]["keep"] == [HOUSE, GCS_PICKUP])
EXPORT["data"] = export()
store.save({"org_name": "Brand New Church", "short_name": "BNC"})
EXPORT["data"]["project"]["portal_pid"] = store.partner_id({"org_name": "Brand New Church"})
check("blocks: a church not in pod2twc's registry", blocks("not in pod2twc's org registry"))
EXPORT["data"] = export()
r = sp.preview(7, form(colors=["White", "Pepper"], sizes=["S", "M", "L", "XL", "2XL", "3XL"]))
check("two colours make twelve variants and warn that mockups show one", len(r["plan"]["variants"]) == 12
      and any("show White only" in w for w in r["warnings"]))
r = sp.preview(7, form(title="HOH - Logo Crewneck"))
check("a title without “ TWC” warns", any("does not end in" in w for w in r["warnings"]))

# ---------------------------------------------------------------- commit
CALLS.clear()
d = sp.preview(7, form())
bad = sp.commit(7, form(), "stale", "Larry")
check("commit refuses a stale fingerprint, writes nothing", not bad["ok"] and "changed since the preview" in bad["error"]
      and not any(n.startswith("mutation") for n, _ in CALLS))
bad = sp.commit(7, form(cost=""), sp.preview(7, form(cost=""))["fingerprint"], "Larry")
check("commit refuses a preview with problems", not bad["ok"] and "problems" in bad["error"])

CALLS.clear()
res = sp.commit(7, form(), d["fingerprint"], "Larry · Studio")
run = res.get("run") or {}
r = run.get("result") or {}
names = [n for n, _ in CALLS]
check("commit creates the product and reports every step", res["ok"] and r.get("product") == "gid://shopify/Product/555"
      and all(s["ok"] for s in r["steps"]) and not r.get("partial"))
ps = next(v for n, v in CALLS if "productSet" in n or n == "mutation Create")["input"]
check("one productSet: title, vendor, type, tags, draft", ps["title"] == "HOH - Logo Crewneck TWC"
      and ps["vendor"] == "Haven of Hope" and ps["productType"] == "Apparel" and ps["status"] == "DRAFT"
      and ps["tags"] == ["haven-of-hope", "ministry:womens"])
v0 = ps["variants"][0]
check("every variant: untracked, DENY, ships, taxable, cost and weight, no SKU",
      all(v["inventoryPolicy"] == "DENY" and v["inventoryItem"]["tracked"] is False
          and v["inventoryItem"]["requiresShipping"] is True and v["taxable"] is True
          and "sku" not in v["inventoryItem"] and v["inventoryItem"]["measurement"]["weight"]["unit"] == "POUNDS"
          for v in ps["variants"]) and v0["inventoryItem"]["cost"] == "11.00" and v0["price"] == "22.00")
check("Color option linked to the swatch metaobjects",
      ps["productOptions"][0]["linkedMetafield"]["values"] == ["gid://shopify/Metaobject/1"]
      and v0["optionValues"][0] == {"optionName": "Color", "linkedMetafieldValue": "gid://shopify/Metaobject/1"})
check("both mockups uploaded and attached, front first, with alt text",
      len(UPLOADS) == 2 and [f["alt"] for f in ps["files"]] == ["HOH - Logo Crewneck, front", "HOH - Logo Crewneck, back"])
toggles = [v for n, v in CALLS if n == "mutation T"]
check("every variant detached from every other location (Germantown, Revive)",
      len(toggles) == 6 and all(u == [{"locationId": GCS_PICKUP, "activate": False},
                                      {"locationId": REVIVE, "activate": False}] for t in toggles for u in [t["u"]]))
dp = next(v for n, v in CALLS if n == "mutation D")
check("delivery profile associated with all six variants", dp["id"].endswith("/5") and len(dp["p"]["variantsToAssociate"]) == 6)
pb = next(v for n, v in CALLS if n == "mutation P")
check("published to the registry's channels", pb["in"] == [{"publicationId": "gid://shopify/Publication/9"}])
check("the Studio item is told, with who did it", TOLD and TOLD[-1][1] == "gid://shopify/Product/555"
      and TOLD[-1][3] == "Larry · Studio")
check("the run is saved", sp.load_run(7)["result"]["product"].endswith("/555"))
check("a second create is refused", any("already in Shopify" in b for b in sp.preview(7, form())["blocking"]))

sp._run_path(7).unlink()
SHOP["fail_create"] = "Title has already been taken"
CALLS.clear()
res = sp.commit(7, form(), sp.preview(7, form())["fingerprint"], "Larry")
check("a Shopify refusal is reported as a failure, nothing claimed",
      not res["ok"] and "already been taken" in res["error"] and not sp.load_run(7)["result"]["product"]
      and not any(n in ("mutation T", "mutation D", "mutation P") for n, _ in CALLS))
SHOP["fail_create"] = ""
sp._run_path(7).unlink()

# ---------------------------------------------------------------- the page
client = portal.app.test_client()
page = client.get("/studio-item/7/shopify")
html = page.get_data(as_text=True)
check("/studio-item/<id>/shopify renders the preview", page.status_code == 200 and "Create in Shopify" in html
      and "HOH - Logo Crewneck TWC" in html and "Shop - Dayton" in html)
check("Create is disabled until the preview is clean (no price or cost yet)", 'disabled' in html.split("Create in Shopify")[0][-400:])
page = client.post("/studio-item/7/shopify", data=dict(form(), action="preview"))
check("Update preview recomputes with the form", page.status_code == 200 and "$24.00" in page.get_data(as_text=True))
fp = sp.preview(7, form())["fingerprint"]
page = client.post("/studio-item/7/shopify", data=dict(form(), action="create", fingerprint=fp))
html = page.get_data(as_text=True)
check("Create from the page makes the product and shows the result", "Created in Shopify" in html
      and "/admin/products/555" in html or "Open in Shopify" in html)
check("the result says it is a draft and gives the ssorder mapping", "draft" in html and "ssorder map set 555" in html)
check("version is 3.78 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 78))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
