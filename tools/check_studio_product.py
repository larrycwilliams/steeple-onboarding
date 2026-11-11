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


def export(status="approved", portal_pid=None, shopify="", mockups=True, ss=True, colors=None, method="dtf"):
    return {"ok": True, "item": {"id": 7, "code": "TS-01", "name": "Logo Crewneck", "category": "tshirt",
                                 "method": method, "status": status, "ready": status in sp_ready,
                                 "approved_by": "Pastor Ray", "blank": "Comfort Colors 1717",
                                 "color": "White", "ss_style": "Comfort Colors 1717", "shopify_product": shopify},
            "project": {"id": 1, "org_name": "Haven of Hope", "project_name": "Fall", "brand": "ss",
                        "portal_pid": pid["hoh"] if portal_pid is None else portal_pid},
            "mockups": ([{"view": "front", "url": "/api/items/7/mockup/front.jpg", "preview": "/i/7/shop-image/front.jpg"},
                         {"view": "back", "url": "/api/items/7/mockup/back.jpg", "preview": "/i/7/shop-image/back.jpg"}]
                        if mockups else []),
            "ss": ({"style": "Comfort Colors 1717", "title": "Garment-Dyed Heavyweight T-Shirt",
                    "brand": "Comfort Colors", "style_name": "1717",
                    "bullets": ["6.1 oz./yd², 100% ring-spun cotton", "Garment-dyed for a lived-in look"],
                    "specs": {"names": ["Body Length", "Body Width"], "rows": [
                        {"size": z, "values": {"Body Width": str(20 + i), "Body Length": f"{28 + i} 1/2"}}
                        for i, z in enumerate(["S", "M", "L", "XL", "2XL", "3XL"])]},
                    "colors": colors or [{"name": "White", "hex": "#fff", "sizes": TEE},
                                         {"name": "Pepper", "hex": "#555", "sizes": TEE}]} if ss else None)}


sp_ready = ("approved", "production", "complete")
EXPORT = {"data": export()}
sp.fetch_item = lambda iid: (EXPORT["data"], "")
sp.fetch_mockup = lambda url: b"\x89PNG fake " + url.encode()
TOLD = []
sp.tell_studio = lambda iid, product, title, by: TOLD.append((iid, product, title, by)) or ""

CALLS: list[tuple[str, dict]] = []
STORES_HIT: list[str] = []
SHOP = {"scopes": ["write_products", "write_inventory", "write_publications", "write_shipping",
                   "read_metaobjects"],
        "dupes": [], "profile": {"id": "gid://shopify/DeliveryProfile/5", "name": "Haven of Hope – Freight"},
        "swatches": {"White": "gid://shopify/Metaobject/1", "Pepper": "gid://shopify/Metaobject/2"},
        "inhouse_def": True, "fail_create": "", "kids": True}


TWC = {"locations": [{"id": "gid://shopify/Location/T1", "name": "TWC Shop", "isActive": True}],
       "collections": [], "dupes": []}


def fake_gql(query, variables=None, store="ss"):
    variables = variables or {}
    CALLS.append((query.split("(")[0].split("{")[0].strip(), variables))
    STORES_HIT.append(store)
    q = query
    if "query T(" in q:
        return {"ok": True, "data": {
            "currentAppInstallation": {"accessScopes": [{"handle": h} for h in
                                                        ("write_products", "write_inventory", "write_publications")]},
            "shop": {"name": "The Williams Collective", "primaryDomain": {"url": "https://thewilliamscollective.com"}},
            "locations": {"nodes": TWC["locations"]},
            "publications": {"nodes": [{"id": "gid://shopify/Publication/T9", "name": "Online Store"},
                                       {"id": "gid://shopify/Publication/T8", "name": "Shop"}]},
            "collections": {"nodes": TWC["collections"]}, "dupes": {"nodes": TWC["dupes"]}}}
    if "collectionUpdate" in q:
        return {"ok": True, "data": {"collectionUpdate": {"userErrors": [], "collection": {"id": variables["input"]["id"]}}}}
    if "collectionCreate" in q:
        return {"ok": True, "data": {"collectionCreate": {"userErrors": [], "collection": {
            "id": "gid://shopify/Collection/T77", "handle": variables["input"]["handle"]}}}}
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
            "fallback_weight": "", "options_sent": "1", "details": "1",
            "special": "Comfort Colors runs small — please order one size up.\nPre-orders close March 1.",
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
desc = p["description"]
check("description: special instructions in red, one line each, then blank name and S&S bullets",
      desc.startswith('<h3><span style="color: rgb(255, 42, 0);"><strong>Comfort Colors runs small')
      and "one size up.<br>Pre-orders close March 1.</strong>" in desc
      and "<strong>Comfort Colors 1717 Garment-Dyed Heavyweight T-Shirt</strong>" in desc
      and "<li>6.1 oz./yd², 100% ring-spun cotton</li>" in desc)
check("size chart: S&S specs, width before length, inches marked, only sizes sold",
      'id="sizechart"' in desc and desc.index("Body Width") < desc.index("Body Length")
      and '28 1/2"' in desc and ">3XL<" in desc
      and ">3XL<" not in sp.preview(7, form(sizes=["S", "M"]))["plan"]["description"])
check("garment care is left to the theme, never in the description", "GARMENT CARE" not in desc.upper().replace("CARE INSTRUCTIONS", ""))
check("special instructions are blank by default, for every blank", sp.preview(7, None)["form"]["special"] == ""
      and "<h3>" not in sp.preview(7, None)["plan"]["description"])
check("blank instructions add nothing; details can be switched off",
      "rgb(255, 42, 0)" not in sp.preview(7, form(special="  \n "))["plan"]["description"]
      and "sizechart" not in sp.preview(7, form(details=""))["plan"]["description"])
check("instructions are escaped, not run as HTML", "&lt;script&gt;" in sp.preview(7, form(special="<script>x</script>"))["plan"]["description"])
check("printed items carry no care tag", "no-print-care" not in p["tags"])
EXPORT["data"] = export(method="embroidery")
check("an embroidered item is tagged no-print-care", "no-print-care" in sp.preview(7, form())["plan"]["tags"])
EXPORT["data"] = export()


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
check("two colours make twelve variants and warn that Pepper has no colour render", len(r["plan"]["variants"]) == 12
      and any("No colour render for Pepper" in w and "🎨 Colours" in w for w in r["warnings"]))
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
check("description goes to Shopify as built", ps["descriptionHtml"] == desc)
check("both store images uploaded as JPEG and attached, front first, with alt text",
      len(UPLOADS) == 2 and UPLOADS[0][2]["file"][2] == "image/jpeg" and ps["files"][0]["originalSource"].endswith("-front.jpg") and [f["alt"] for f in ps["files"]] == ["HOH - Logo Crewneck, White, front", "HOH - Logo Crewneck, White, back"])
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
check("the result says it is a draft and gives the ssorder mapping", "draft" in html and "python3 ssorder.py map set 555" in html)
# ------------------------------------------- a business, in the TWC store (doc 73)
from onboarding import twc_shop  # noqa: E402
sp._run_path(7).unlink(missing_ok=True)
TOLD.clear()


def nfg_export(**kw):
    e = export(portal_pid="", **kw)
    e["project"].update({"org_name": "NFG - National Fuel", "brand": "twc", "project_name": "Employee Merch"})
    return e


EXPORT["data"] = nfg_export()
twc_shop.configured = lambda: False
r = sp.preview(7, None)
check("TWC project, store not connected: says where to connect it", not r["ok"]
      and any("not connected yet" in b for b in r["blocking"]) and r["business"])
twc_shop.configured = lambda: True
twc_shop.domain = lambda: "twc-test.myshopify.com"
twc_shop.house_location_name = lambda: ""
STORES_HIT.clear()
r = sp.preview(7, form(title="NFG - Staff tee TWC"))
p = r["plan"]
check("a business needs no Portal partner link", r["ok"] and not any("Portal partner" in b for b in r["blocking"]))
check("defaults: the short name is the part before ' - '", sp.preview(7, None)["form"]["title"].startswith("NFG - Logo Crewneck"))
check("every read goes to the TWC store, none to Steeple & Stitch", STORES_HIT and set(STORES_HIT) == {"twc"})
check("vendor is the business; tag is its slug; no ministry, no church fields",
      p["vendor"] == "NFG - National Fuel" and p["tags"] == ["nfg-national-fuel", "private-store"] and p["store"] == "twc"
      and [m["key"] for m in p["metafields"]] == ["hidden"])
check("the single TWC location is where it is stocked", p["keep"] == ["gid://shopify/Location/T1"])
check("published to the Online Store only (not Shop)", p["publications"] == ["gid://shopify/Publication/T9"])
check("first product: a link-only collection is planned, with an unguessable handle",
      p["new_collection"]["handle"].startswith("nfg-national-fuel-") and len(p["new_collection"]["handle"]) == 24
      and any("link-only collection" in w for w in r["warnings"]))
check("the handle is the same on every preview", sp.preview(7, form(title="NFG - Staff tee TWC"))["plan"]["new_collection"]
      == p["new_collection"])
check("no delivery-profile or shipping warnings for TWC", not any("write_shipping" in w or "delivery profile" in w
                                                                  for w in r["warnings"]))
TWC["locations"].append({"id": "gid://shopify/Location/T2", "name": "Dayton Office", "isActive": True})
check("two TWC locations and none named: asks for Ships from", any("Ships from" in b for b in
                                                                   sp.preview(7, form(title="NFG - Staff tee TWC"))["blocking"]))
twc_shop.house_location_name = lambda: "TWC Shop"
check("Ships from picks the named location", sp.preview(7, form(title="NFG - Staff tee TWC"))["plan"]["keep"]
      == ["gid://shopify/Location/T1"])

CALLS.clear(); STORES_HIT.clear(); UPLOADS.clear()
pre = sp.preview(7, form(title="NFG - Staff tee TWC"))
res = sp.commit(7, form(title="NFG - Staff tee TWC"), pre["fingerprint"], "Larry")
r = res["run"]["result"]
names = [n for n, _ in CALLS]
check("commit writes only to the TWC store", res["ok"] and set(STORES_HIT) == {"twc"})
check("the collection is created first, VENDOR EQUALS the business, hidden from search",
      names.index("mutation C") < names.index("mutation Create")
      and next(v for n, v in CALLS if n == "mutation C")["input"]["ruleSet"]["rules"][0] ==
      {"column": "VENDOR", "relation": "EQUALS", "condition": "NFG - National Fuel"}
      and next(v for n, v in CALLS if n == "mutation C")["input"]["metafields"][0]["key"] == "hidden")
check("the collection is published to the Online Store", any(n == "mutation P" and v["id"].endswith("T77") for n, v in CALLS))
ps = next(v for n, v in CALLS if n == "mutation Create")["input"]
check("the product: business vendor, hidden from search, no church metafields",
      ps["vendor"] == "NFG - National Fuel" and ps["metafields"] == [
          {"namespace": "seo", "key": "hidden", "type": "number_integer", "value": "1"}])
check("no delivery-profile call for a TWC product", "mutation D" not in names)
check("the result keeps the store and the private link",
      r["store"] == "twc" and r["collection"]["url"].startswith("https://thewilliamscollective.com/collections/nfg-national-fuel-"))
check("the admin link points at the TWC store", sp.admin_url(r["product"], "twc").startswith("https://twc-test.myshopify.com/admin/"))
page = portal.app.test_client().get("/studio-item/7/shopify").get_data(as_text=True)
check("the page shows the collection link to share", "Share this link with the business" in page
      and "thewilliamscollective.com/collections/nfg-national-fuel-" in page)
sp._run_path(7).unlink()
TWC["collections"] = [{"id": "gid://shopify/Collection/T77", "title": "NFG - National Fuel", "handle": "nfg-national-fuel-abc123",
                       "ruleSet": {"rules": [{"column": "VENDOR", "relation": "EQUALS", "condition": "NFG - National Fuel"}]}}]
p2 = sp.preview(7, form(title="NFG - Polo TWC"))["plan"]
check("second product: the existing collection is reused, none created", p2["new_collection"] is None
      and p2["collection"]["id"].endswith("T77"))

# ------------------------------------------------- Settings: the TWC store card
client = portal.app.test_client()
html = client.get("/settings").get_data(as_text=True)
check("Settings has The Williams Collective store card with its scopes and callback URLs",
      'id="twc-store"' in html and "/shopify/twc/callback" in html and "read_orders,read_products" in html and "read_all_orders" not in html)
resp = client.post("/settings/twc-shopify", data={"store": "https://twc-test.myshopify.com/", "client_id": "a" * 32,
                                                  "client_secret": "shpss_" + "b" * 32, "house_location": "TWC Shop"})
from onboarding import secrets as es  # noqa: E402
v = es.read_env(es.TWC_ENV_PATH)
check("saving writes .env.twc only, never .env", v.get("SHOPIFY_STORE") == "twc-test.myshopify.com"
      and v.get("TWC_HOUSE_LOCATION") == "TWC Shop" and not es.read_env().get("SHOPIFY_CLIENT_ID", "").startswith("aaaa"))
check("a non-.myshopify.com domain is refused", "not a .myshopify.com domain" in client.post(
      "/settings/twc-shopify", data={"store": "thewilliamscollective.com"}, follow_redirects=True).get_data(as_text=True))
c = client.get("/shopify/twc/connect")
check("Connect sends the TWC store its own scopes and callback", c.status_code == 302
      and c.headers["Location"].startswith("https://twc-test.myshopify.com/admin/oauth/authorize")
      and "shopify%2Ftwc%2Fcallback" in c.headers["Location"] and "read_metaobjects" in c.headers["Location"])
bad = client.get("/shopify/twc/callback?state=nope&shop=twc-test.myshopify.com", follow_redirects=True)
check("a callback that this app did not start saves nothing", "did not match" in bad.get_data(as_text=True)
      and not es.read_env(es.TWC_ENV_PATH).get("SHOPIFY_ADMIN_TOKEN"))

# 3.83: TWC store without swatches -> plain Color, warning with the S&S hexes
EXPORT["data"] = nfg_export()
sp._run_path(7).unlink(missing_ok=True)
TWC["collections"] = []
_orig = fake_gql


def fake_gql_sw(query, variables=None, store="ss"):
    r = _orig(query, variables, store)
    if "query T(" in query:
        r["data"]["currentAppInstallation"]["accessScopes"].append({"handle": "read_metaobjects"})
    if "query Swatches" in query and store == "twc":
        return {"ok": True, "data": {"metaobjects": {"nodes": [{"id": "gid://shopify/Metaobject/W", "field": {"value": "White"}}],
                                                     "pageInfo": {"hasNextPage": False}}}}
    return r


sp._gql = fake_gql_sw
r = sp.preview(7, form(title="NFG - Staff tee TWC", colors=["White", "Pepper"]))
check("TWC: a colour with no swatch warns (with the S&S hex), never blocks", r["ok"]
      and any("no colour swatch for Pepper" in w and "#555" in w for w in r["warnings"]))
check("TWC: with a swatch missing, Color is created plain (not half-linked)", r["plan"]["swatch"] == {}
      and "linkedMetafield" not in sp.product_input(r["plan"], [])["productOptions"][0])
EXPORT["data"] = export()
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1"}
check("S&S store: a missing swatch still blocks (doc 50 C2)", blocks("No Shopify colour swatch is called “Pepper”",
                                                                     colors=["White", "Pepper"]))
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1", "Pepper": "gid://shopify/Metaobject/2"}
sp._gql = fake_gql
client = portal.app.test_client()
with client.session_transaction() as sess:
    sess["_flashes"] = [("error", "Shopify's signature on that response did not verify. Nothing was saved.")]
html = client.get("/settings?twc=1").get_data(as_text=True)
check("TWC connect messages show on the TWC card, not at the top of the page",
      html.count("did not verify") == 1 and html.index("did not verify") > html.index('id="twc-store"'))

# ------------------------------------------- 3.84: colour renders + swatches
check("taxonomy: Natural is Beige (6), not Brown (7)", sp.color_taxonomy("Natural", "Natural", "#EFE6D2").endswith("/6"))
check("taxonomy: family words map (Navy 15, Heather Grey 8, Maroon 13)",
      sp.color_taxonomy("Navy", "Navy", "#1F2A44").endswith("/15") and sp.color_taxonomy("", "Sport Grey", "#9B9B9B").endswith("/8")
      and sp.color_taxonomy("Maroon", "Maroon", "#5B1A22").endswith("/13"))
check("taxonomy: an unknown name falls back to the hex hue (orange 10, dark navy 15)",
      sp.color_taxonomy("", "Sunset", "#FF8800").endswith("/10") and sp.color_taxonomy("", "Midnight", "#14213D").endswith("/15"))
check("hex is normalised (#fff -> #FFFFFF; junk -> empty)", sp.norm_hex("#fff") == "#FFFFFF" and sp.norm_hex("nope") == "")

CW = {"Pepper": [{"view": "front", "url": "/api/items/7/cw/11.jpg", "preview": "/i/7/cw/11.jpg", "hex": "#555"},
                 {"view": "back", "url": "/api/items/7/cw/12.jpg", "preview": "/i/7/cw/12.jpg", "hex": "#555"}]}
EXPORT["data"] = export()
EXPORT["data"]["colourways"] = CW
sp._run_path(7).unlink(missing_ok=True)
f2 = form(title="HOH - Logo Crewneck TWC", colors=["White", "Pepper"])
pre = sp.preview(7, f2)
imgs = pre["plan"]["images"]
check("each colour gets its own images, colour by colour, front first",
      [(m["color"], m["view"]) for m in imgs] == [("White", "front"), ("White", "back"), ("Pepper", "front"), ("Pepper", "back")]
      and imgs[2]["url"] == "/api/items/7/cw/11.jpg")
check("with every colour rendered there is no 'no colour render' warning",
      not any("No colour render" in w for w in pre["warnings"]))
CALLS.clear(); UPLOADS.clear()
res = sp.commit(7, f2, pre["fingerprint"], "Larry")
ps = next(v for n, v in CALLS if n == "mutation Create")["input"]
check("four images uploaded, named by colour", res["ok"] and len(UPLOADS) == 4
      and [f["originalSource"].rsplit("/", 1)[-1] for f in ps["files"]][2] == "hoh-logo-crewneck-twc-pepper-front.jpg")
vf = {v["optionValues"][0].get("linkedMetafieldValue"): v["file"]["originalSource"] for v in ps["variants"]}
check("every variant shows its own colour's front image",
      vf["gid://shopify/Metaobject/1"].endswith("-white-front.jpg") and vf["gid://shopify/Metaobject/2"].endswith("-pepper-front.jpg")
      and all(v["file"]["originalSource"] in {f["originalSource"] for f in ps["files"]} for v in ps["variants"]))

# productSet refuses the links -> made plain, flagged
sp._run_path(7).unlink(missing_ok=True)
_base = fake_gql
SEEN = []


def refuse_links(query, variables=None, store="ss"):
    if "productSet" in query:
        inp = variables["input"]
        SEEN.append(inp)
        if "linkedMetafield" in inp["productOptions"][0] or any("file" in v for v in inp["variants"]):
            return {"ok": True, "data": {"productSet": {"product": None, "userErrors": [
                {"field": ["x"], "message": "Product must have a category to link a metafield"}]}}}
    return _base(query, variables, store)


sp._gql = refuse_links
pre = sp.preview(7, f2)
res = sp.commit(7, f2, pre["fingerprint"], "Larry")
r = res["run"]["result"]
check("swatch links refused: the product is still made, plain, and the step says why",
      res["ok"] and r["product"] and len(SEEN) == 2 and "values" in SEEN[1]["productOptions"][0]
      and not any("file" in v for v in SEEN[1]["variants"]) and SEEN[1]["files"] == SEEN[0]["files"]
      and any(s["step"].startswith("Swatches and variant photos skipped") and "category" in s["detail"] for s in r["steps"])
      and r["partial"])
sp._gql = fake_gql

# TWC with write_metaobjects: missing swatches are made from the S&S hex
EXPORT["data"] = nfg_export(colors=[{"name": "White", "hex": "#fff", "family": "White", "sizes": TEE},
                                    {"name": "Sport Grey", "hex": "#9b9b9b", "family": "Grey", "sizes": TEE}])
EXPORT["data"]["colourways"] = {"Sport Grey": CW["Pepper"]}
TWC["collections"] = []
sp._run_path(7).unlink(missing_ok=True)
MADE, ENABLED = [], []
TWC_SCOPES = ["read_metaobjects", "write_metaobjects"]
FAIL_FIRST = {"on": False}


def twc_sw(query, variables=None, store="ss"):
    r = _base(query, variables, store)
    if "query T(" in query:
        r["data"]["currentAppInstallation"]["accessScopes"] += [{"handle": h} for h in TWC_SCOPES]
    if "query Swatches" in query and store == "twc":
        return {"ok": True, "data": {"metaobjects": {"nodes": [{"id": "gid://shopify/Metaobject/W", "field": {"value": "White"}}],
                                                     "pageInfo": {"hasNextPage": False}}}}
    if "metaobjectCreate" in query:
        if FAIL_FIRST["on"] and not ENABLED:
            return {"ok": True, "data": {"metaobjectCreate": {"metaobject": None, "userErrors": [
                {"field": ["type"], "message": "No metaobject definition exists for type", "code": "UNDEFINED_OBJECT_TYPE"}]}}}
        MADE.append(variables["m"])
        return {"ok": True, "data": {"metaobjectCreate": {"userErrors": [], "metaobject": {"id": f"gid://shopify/Metaobject/N{len(MADE)}"}}}}
    if "standardMetaobjectDefinitionEnable" in query:
        ENABLED.append(1)
        return {"ok": True, "data": {"standardMetaobjectDefinitionEnable": {"userErrors": [], "metaobjectDefinition": {"id": "x"}}}}
    return r


sp._gql = twc_sw
twc_shop.house_location_name = lambda: "TWC Shop"
TWC["locations"] = TWC["locations"][:1]
f3 = form(title="NFG - Staff tee TWC", colors=["White", "Sport Grey"])
pre = sp.preview(7, f3)
check("TWC + write_metaobjects: the missing swatch is planned from the S&S hex, not blocked",
      pre["ok"] and pre["plan"]["swatch_new"] == {"Sport Grey": {"hex": "#9B9B9B", "taxonomy": "gid://shopify/TaxonomyValue/8"}}
      and pre["plan"]["swatch"] == {"White": "gid://shopify/Metaobject/W"}
      and any("New colour swatches" in w and "#9B9B9B" in w for w in pre["warnings"]))
FAIL_FIRST["on"] = True
CALLS.clear()
res = sp.commit(7, f3, pre["fingerprint"], "Larry")
ps = next(v for n, v in CALLS if n == "mutation Create")["input"]
m = MADE[0]
fields = {x["key"]: x["value"] for x in m["fields"]}
check("a store with no swatch definition gets Shopify's standard one turned on, then the swatch made",
      res["ok"] and ENABLED and len(MADE) == 1 and m["type"] == "shopify--color-pattern")
check("the swatch: label, S&S hex, Gray taxonomy, Solid pattern, published",
      fields == {"label": "Sport Grey", "color": "#9B9B9B", "color_taxonomy_reference": '["gid://shopify/TaxonomyValue/8"]',
                 "pattern_taxonomy_reference": "gid://shopify/TaxonomyValue/2874"}
      and m["capabilities"]["publishable"]["status"] == "ACTIVE")
check("Color is linked to the old and the new swatch",
      ps["productOptions"][0]["linkedMetafield"]["values"] == ["gid://shopify/Metaobject/W", "gid://shopify/Metaobject/N1"]
      and any(s["step"].startswith("Colour swatches") and s["ok"] for s in res["run"]["result"]["steps"]))
TWC_SCOPES[:] = ["read_metaobjects"]
sp._run_path(7).unlink(missing_ok=True)
pre = sp.preview(7, f3)
check("without write_metaobjects: plain dropdown, and the warning says what to add",
      pre["ok"] and pre["plan"]["swatch_new"] == {} and pre["plan"]["swatch"] == {}
      and any("write_metaobjects" in w and "Reconnect" in w for w in pre["warnings"]))
check("the TWC connection asks for write_metaobjects and write_metaobject_definitions",
      "write_metaobjects" in twc_shop.SCOPES and "write_metaobject_definitions" in twc_shop.SCOPES)
sp._gql = fake_gql
EXPORT["data"] = export()

# ------------------------------------------- 3.85: category, re-create after delete
sp._run_path(7).unlink(missing_ok=True)
EXPORT["data"] = export()
pre = sp.preview(7, form())
CALLS.clear()
res = sp.commit(7, form(), pre["fingerprint"], "Larry")
ps = next(v for n, v in CALLS if n == "mutation Create")["input"]
check("a tee goes in as Clothing Tops › T-Shirts (so its Color can link to swatches)",
      ps.get("category") == "gid://shopify/TaxonomyCategory/aa-1-13-8")
check("plain fallback drops the category as well as the links",
      "category" not in sp.product_input(pre["plan"], [], {}, plain=True))
EXPORT["data"] = export(shopify="gid://shopify/Product/555")
check("an item still in Shopify blocks a second create", blocks("already in Shopify"))
_b2 = fake_gql


def deleted(query, variables=None, store="ss"):
    if query.startswith("query G"):
        return {"ok": True, "data": {"product": None}}
    return _b2(query, variables, store)


sp._gql = deleted
r = sp.preview(7, form())
check("deleted in the admin: it can be created again, with a warning about ssorder",
      r["ok"] and any("has been deleted there" in w and "ssorder" in w for w in r["warnings"]))
CALLS.clear()
res = sp.commit(7, form(), r["fingerprint"], "Larry")
check("…and Create makes it again", res["ok"] and any(n == "mutation Create" for n, _ in CALLS))
sp._gql = fake_gql
EXPORT["data"] = export()
page = portal.app.test_client().get("/studio-item/7/shopify").get_data(as_text=True)
check("Open in Shopify is a real button; the ssorder line is runnable",
      'class="btn btn-gold"' in page and "python3 ssorder.py map set" in page)

# ------------------------------------------- 3.86: a business's private corner
EXPORT["data"] = nfg_export()
sp._run_path(7).unlink(missing_ok=True)
TWC["collections"] = []
pre = sp.preview(7, form(title="NFG - Staff tee TWC"))
CALLS.clear()
res = sp.commit(7, form(title="NFG - Staff tee TWC"), pre["fingerprint"], "Larry")
ps = next(v for n, v in CALLS if n == "mutation Create")["input"]
cc = next(v for n, v in CALLS if n == "mutation C")["input"]
check("a business product uses product.private and is tagged private-store",
      ps.get("templateSuffix") == "private" and "private-store" in ps["tags"])
check("its new collection uses collection.private", cc.get("templateSuffix") == "private")
sp._run_path(7).unlink(missing_ok=True)
TWC["collections"] = [{"id": "gid://shopify/Collection/T77", "title": "NFG - National Fuel", "handle": "nfg-national-fuel-cedf24",
                       "ruleSet": {"rules": [{"column": "VENDOR", "relation": "EQUALS", "condition": "NFG - National Fuel"}]}}]
pre = sp.preview(7, form(title="NFG - Polo TWC"))
CALLS.clear()
res = sp.commit(7, form(title="NFG - Polo TWC"), pre["fingerprint"], "Larry")
cu = [v for n, v in CALLS if n == "mutation U"]
check("an existing business collection is switched to collection.private too",
      res["ok"] and cu and cu[0]["input"] == {"id": "gid://shopify/Collection/T77", "templateSuffix": "private"}
      and not res["run"]["result"]["partial"])
EXPORT["data"] = export()
sp._run_path(7).unlink(missing_ok=True)
pre = sp.preview(7, form())
check("a church product is untouched: no private tag or template",
      "private-store" not in pre["plan"]["tags"] and not pre["plan"]["template_suffix"]
      and "templateSuffix" not in sp.product_input(pre["plan"], []))

# ------------------------------------------- 3.87: add colours to a live product
HN = sizes(("S", 5.20, 0.40), ("M", 5.20, 0.44), ("L", 5.20, 0.48), ("XL", 5.20, 0.52), ("2XL", 7.20, 0.58))
LIVE = {"linked": True, "deleted": False}
ADDS, OPTS = [], []


def live_gql(query, variables=None, store="ss"):
    if query.lstrip().startswith("query P("):
        CALLS.append(("query P", variables))
        if LIVE["deleted"]:
            return {"ok": True, "data": {"product": None}}
        price = {"S": "22.00", "M": "22.00", "L": "22.00", "XL": "22.00", "2XL": "24.00", "3XL": "26.00"}
        return {"ok": True, "data": {"product": {
            "id": "gid://shopify/Product/555", "title": "HOH - Logo Crewneck TWC", "handle": "h", "status": "ACTIVE",
            "options": [{"id": "gid://shopify/ProductOption/1", "name": "Color",
                         "linkedMetafield": {"namespace": "shopify", "key": "color-pattern"} if LIVE["linked"] else None,
                         "optionValues": [{"id": "v1", "name": "White"}]},
                        {"id": "gid://shopify/ProductOption/2", "name": "Size", "linkedMetafield": None,
                         "optionValues": [{"id": f"s{i}", "name": z} for i, z in enumerate(price)]}],
            "variants": {"nodes": [{"id": f"gid://shopify/ProductVariant/{i}", "price": p, "compareAtPrice": None,
                                    "selectedOptions": [{"name": "Color", "value": "White"}, {"name": "Size", "value": z}],
                                    "inventoryItem": {"unitCost": {"amount": "11.00" if z in ("S", "M", "L", "XL") else "13.00"}}}
                                   for i, (z, p) in enumerate(price.items())]}}}}
    if query.lstrip().startswith("query G("):
        return {"ok": True, "data": {"product": None if LIVE["deleted"] else {"id": variables["id"]}}}
    if "productVariantsBulkCreate" in query:
        CALLS.append(("mutation A", variables))
        ADDS.append(variables)
        return {"ok": True, "data": {"productVariantsBulkCreate": {"userErrors": [], "productVariants": [
            {"id": f"gid://shopify/ProductVariant/9{i}", "title": "",
             "inventoryItem": {"id": f"gid://shopify/InventoryItem/9{i}", "inventoryLevels": {
                 "nodes": [{"location": {"id": HOUSE}}, {"location": {"id": REVIVE}}]}}}
            for i, _ in enumerate(variables["v"])]}}}
    if "productOptionUpdate" in query:
        OPTS.append(variables)
        return {"ok": True, "data": {"productOptionUpdate": {"userErrors": []}}}
    return fake_gql(query, variables, store)


sp._gql = live_gql
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1", "Pepper": "gid://shopify/Metaobject/2",
                    "Heather Navy": "gid://shopify/Metaobject/3"}
EXPORT["data"] = export(shopify="gid://shopify/Product/555",
                        colors=[{"name": "White", "hex": "#fff", "sizes": TEE},
                                {"name": "Pepper", "hex": "#555", "sizes": TEE},
                                {"name": "Heather Navy", "hex": "#223", "sizes": HN}])
EXPORT["data"]["colourways"] = {"Heather Navy": [
    {"view": "front", "url": "/api/items/7/cw/21.jpg", "preview": "/i/7/cw/21.jpg"},
    {"view": "back", "url": "/api/items/7/cw/22.jpg", "preview": "/i/7/cw/22.jpg"}]}
sp._save_run(7, {"result": {"product": "gid://shopify/Product/555", "store": "ss", "ok": True, "steps": []}})
a0 = sp.add_preview(7, None)
check("add colours: lists only the S&S colours the product lacks", a0["choices"] == ["Pepper", "Heather Navy"]
      and a0["existing"] == ["White"] and not a0["ok"])
a1 = sp.add_preview(7, Form(add_colors=["Heather Navy"]))
hv = {v["size"]: v for v in a1["add"]["variants"]}
check("new colour copies the live price ladder, hand-edited sizes included",
      hv["S"]["price"] == 22 and hv["2XL"]["price"] == 24 and "3XL" not in hv)
check("cost follows the blank from the ORIGINAL base (heather costs more)",
      hv["S"]["cost"] == round(11 + (5.20 - 4.10), 2) and hv["2XL"]["cost"] == round(11 + (7.20 - 4.10), 2))
check("a size S&S lacks in the new colour is named, not invented",
      any("no 3XL in Heather Navy" in w for w in a1["warnings"]))
check("only the new colour's images go up", [m["color"] for m in a1["add"]["images"]] == ["Heather Navy"] * 2
      and a1["ok"])
ADDS.clear(); UPLOADS.clear(); CALLS.clear()
r = sp.add_commit(7, Form(add_colors=["Heather Navy"]), a1["fingerprint"], "Larry")
v = ADDS[0]
row = v["v"][0]
check("adds variants in place on the same product (no productSet, no delete)",
      r["ok"] and v["id"] == "gid://shopify/Product/555" and not any("productSet" in n for n, _ in CALLS)
      and len(v["v"]) == 5 and len(UPLOADS) == 2)
check("new variants link the swatch and carry their colour's photo",
      row["optionValues"][0] == {"optionName": "Color", "linkedMetafieldValue": "gid://shopify/Metaobject/3"}
      and row["mediaSrc"][0].endswith("-heather-navy-front.jpg")
      and {m["originalSource"] for m in v["m"]} >= set(row["mediaSrc"]))
check("new variants: untracked, DENY, cost and weight set",
      row["inventoryItem"]["tracked"] is False and row["inventoryPolicy"] == "DENY"
      and row["inventoryItem"]["cost"] == "12.10" and row["inventoryItem"]["measurement"]["weight"]["value"] == 0.4)
upd = sp.load_run(7)["updates"][-1]
check("new variants stocked at house + pickup only, and on the church's profile",
      any(s["step"].startswith("Stocked at") and s["ok"] for s in upd["steps"])
      and any(n == "mutation D" and len(x["p"]["variantsToAssociate"]) == 5 for n, x in CALLS))
check("the update is recorded on the run; the original result is untouched",
      upd["ok"] and upd["colors"] == ["Heather Navy"] and sp.load_run(7)["result"]["product"] == "gid://shopify/Product/555")
check("a changed tick after preview refuses (fingerprint)",
      not sp.add_commit(7, Form(add_colors=["Pepper"]), a1["fingerprint"], "Larry")["ok"])
LIVE["linked"] = False
a2 = sp.add_preview(7, Form(add_colors=["Pepper"]))
ADDS.clear()
sp.add_commit(7, Form(add_colors=["Pepper"]), a2["fingerprint"], "Larry")
check("a plain (unlinked) Color gets plain names", ADDS[0]["v"][0]["optionValues"][0] == {"optionName": "Color", "name": "Pepper"})
LIVE["linked"] = True
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1"}
a3 = sp.add_preview(7, Form(add_colors=["Pepper"]))
check("S&S store, linked Color, no swatch for the new colour: blocked, nothing sent",
      not a3["ok"] and any("No Shopify colour swatch" in b or "none for Pepper" in b for b in a3["blocking"]))
SHOP["swatches"] = {"White": "gid://shopify/Metaobject/1", "Pepper": "gid://shopify/Metaobject/2",
                    "Heather Navy": "gid://shopify/Metaobject/3"}
LIVE["deleted"] = True
check("deleted in Shopify: no Add, says to create it again",
      any("deleted in Shopify" in b for b in sp.add_preview(7, None)["blocking"]))
html = portal.app.test_client().get("/studio-item/7/shopify").get_data(as_text=True)
check("…and the Create form comes back by itself (no moving run files by hand)",
      'value="create"' in html or 'name="action" value="create"' in html)
LIVE["deleted"] = False
html = portal.app.test_client().get("/studio-item/7/shopify").get_data(as_text=True)
check("the page shows Add colours for a live product, with its history",
      "Add colours" in html and "Heather Navy" in html and "Changes since" in html)
html = portal.app.test_client().post("/studio-item/7/shopify", data={"action": "add_preview", "add_colors": "Pepper"}
                                     ).get_data(as_text=True)
check("Preview shows the new variants and an Add button", "Pepper / S" in html and 'value="add"' in html)
# TWC: a new colour with no swatch gets one made, then is linked
TWC_SCOPES[:] = ["read_metaobjects", "write_metaobjects"]
FAIL_FIRST["on"] = False
MADE.clear()


def twc_live(query, variables=None, store="ss"):
    if query.lstrip().startswith(("query P(", "query G(")) or "productVariantsBulkCreate" in query \
            or "productOptionUpdate" in query:
        return live_gql(query, variables, store)
    return twc_sw(query, variables, store)


sp._gql = twc_live
EXPORT["data"] = nfg_export(colors=[{"name": "White", "hex": "#fff", "family": "White", "sizes": TEE},
                                    {"name": "Heather Royal", "hex": "#3B5BA9", "family": "Blue", "sizes": TEE}])
EXPORT["data"]["item"]["shopify_product"] = "gid://shopify/Product/555"
EXPORT["data"]["colourways"] = {"Heather Royal": CW["Pepper"]}
sp._save_run(7, {"result": {"product": "gid://shopify/Product/555", "store": "twc", "ok": True, "steps": []}})
a4 = sp.add_preview(7, Form(add_colors=["Heather Royal"]))
check("TWC: the new colour's swatch is planned from the S&S hex (Blue)",
      a4["ok"] and a4["add"]["swatch_new"] == {"Heather Royal": {"hex": "#3B5BA9", "taxonomy": "gid://shopify/TaxonomyValue/2"}}
      and a4["add"]["store"] == "twc")
ADDS.clear()
r = sp.add_commit(7, Form(add_colors=["Heather Royal"]), a4["fingerprint"], "Larry")
check("TWC: swatch created, then the new variants link to it",
      r["ok"] and MADE and ADDS[0]["v"][0]["optionValues"][0]["linkedMetafieldValue"].startswith("gid://shopify/Metaobject/N")
      and STORES_HIT[-1] == "twc")
sp._gql = fake_gql
sp._run_path(7).unlink(missing_ok=True)
EXPORT["data"] = export()
check("version is 3.87 or later", tuple(map(int, portal.APP_VERSION.split("."))) >= (3, 87))

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{ok} passed, {fail} failed")
sys.exit(1 if fail else 0)
