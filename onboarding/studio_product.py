"""Create a Shopify product from an approved Production Studio item (doc 72).

The in-house create path doc 46 has carried as "still open": a design the
customer approved in the Studio becomes an in-house (TWC) Shopify product in one
step, built to the house convention instead of by hand in the admin, where the
defaults are wrong (weight 0, tracked, no shipping, General delivery profile).

    preview(iid, form)  -> {ok, blocking[], warnings[], plan{}, fingerprint}
                           Reads the Studio and Shopify. Writes nothing.
    commit(iid, form, by) -> the same, then creates the product. Refuses unless
                           the preview is clean and its fingerprint matches what
                           Larry looked at.

The Studio never writes to Shopify; this module does, with the Portal's token.
The church comes from pod2twc's config.json -- vendor, org tag, collection, store
name, pickup locations, house location, sales channels -- the registry the Clone
button already uses, so the two create paths cannot disagree. What config.json
does not hold is read live from Shopify: the ministry sub-collections (doc 50 A),
the delivery profile the church's other products are on, colour swatches.

House convention, from doc 14 / doc 50 and the clone-made HOH Logo Crewneck:
  title "<short> - <item> TWC" · vendor = the org's vendor string
  tags = org tag (+ chosen ministry:*) · Color + Size options, Color linked to the
  shopify.color-pattern swatch when the token can read metaobjects
  every variant: tracked OFF, DENY, requires shipping, taxable, weight from S&S,
  loaded cost, no SKU · stocked at the house location + the org's pickup
  locations ONLY (Shopify assigns new products to every location)
  church_store / _name / _url + inhouse_only metafields · the church's delivery
  profile · the config's sales channels · DRAFT by default.

Nothing here raises; every Shopify answer is reported, and a failure after the
product exists is reported as PARTIAL with the product id, never as success.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import requests

from . import clone, dashboard, store
from .shopify_pull import _load_env, configured
from .storefront import _gql

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "product_runs" / "studio"
STUDIO_INTERNAL = os.environ.get("STUDIO_INTERNAL_URL", "http://127.0.0.1:5100").rstrip("/")
STOREFRONT = os.environ.get("STOREFRONT_URL", "https://www.steepleandstitch.com").rstrip("/")
MAX_VARIANTS = 100                 # Basic plan (platform learnings)
SIZE_ORDER = ["YXS", "YS", "YM", "YL", "YXL", "XS", "S", "M", "L", "XL",
              "2XL", "3XL", "4XL", "5XL", "6XL", "OSFA", "One Size"]
# The house retail ladder, as upcharges over the S–XL price (doc 14):
# a $22 tee is 2XL $24 · 3XL $25 · 4XL $26.
LADDER = {"2XL": 2.0, "3XL": 3.0, "4XL": 4.0, "5XL": 5.0, "6XL": 6.0}
ACCESSORY = {"hat", "beanie"}

NEEDED_SCOPES = ["write_products", "write_inventory"]
SOFT_SCOPES = {
    "write_publications": "it will not be published to the sales channels — publish it in the admin",
    "write_shipping": "its delivery profile cannot be set — set it in Settings › Shipping",
    "read_metaobjects": "the Color option cannot be linked to swatches — link it in the admin",
}


# ------------------------------------------------------------------ helpers

def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _money(text) -> float | None:
    try:
        value = float(str(text).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return round(value, 2) if value >= 0 else None


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _size_rank(size: str) -> int:
    return SIZE_ORDER.index(size) if size in SIZE_ORDER else len(SIZE_ORDER)


def _run_path(iid: int) -> Path:
    return RUNS / f"item-{int(iid)}.json"


def load_run(iid: int) -> dict:
    try:
        return json.loads(_run_path(iid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_run(iid: int, data: dict) -> None:
    RUNS.mkdir(parents=True, exist_ok=True)
    tmp = _run_path(iid).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, _run_path(iid))


def admin_url(gid: str) -> str:
    shop = os.environ.get("SHOPIFY_STORE", "")
    num = str(gid or "").rsplit("/", 1)[-1]
    return f"https://{shop}/admin/products/{num}" if shop and num else ""


# ------------------------------------------------------------ the Studio side

def fetch_item(iid: int) -> tuple[dict | None, str]:
    """The approved item, as the Studio hands it over (loopback API)."""
    try:
        with urllib.request.urlopen(f"{STUDIO_INTERNAL}/api/items/{int(iid)}/export", timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as exc:                                  # noqa: BLE001
        return None, f"Could not reach the Studio ({type(exc).__name__}: {exc})."
    if not isinstance(data, dict) or not data.get("ok"):
        return None, "The Studio answered, but not with the item."
    return data, ""


def fetch_mockup(url: str) -> bytes:
    with urllib.request.urlopen(f"{STUDIO_INTERNAL}{url}", timeout=20) as r:
        return r.read()


def tell_studio(iid: int, product: str, title: str, by: str) -> str:
    body = json.dumps({"product": product, "title": title, "by": by}).encode()
    req = urllib.request.Request(f"{STUDIO_INTERNAL}/api/items/{int(iid)}/shopify", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return "" if json.loads(r.read().decode()).get("ok") else "the Studio refused the link"
    except Exception as exc:                                  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


# ----------------------------------------------------------- the Shopify side

CONTEXT_QUERY = """
query Ctx($col: ID!) {
  currentAppInstallation { accessScopes { handle } }
  collection(id: $col) { id title handle sortOrder
    metafield(namespace: "custom", key: "ministry_collections") { value } }
  locations(first: 50, includeLegacy: true) { nodes { id name isActive } }
  metafieldDefinitions(ownerType: PRODUCT, namespace: "custom", first: 50) { nodes { key } }
}"""

VENDOR_QUERY = """
query Vendor($q: String!, $dup: String!) {
  siblings: products(first: 10, query: $q, sortKey: UPDATED_AT, reverse: true) {
    nodes { id title status variants(first: 1) { nodes { deliveryProfile { id name } } } }
  }
  dupes: products(first: 5, query: $dup) { nodes { id title status } }
}"""

CHILDREN_QUERY = """
query Kids($ids: [ID!]!) {
  nodes(ids: $ids) { ... on Collection { id title
      ruleSet { rules { column relation condition } }
      resourcePublicationsV2(first: 5) { nodes { isPublished } } } }
}"""

SWATCH_QUERY = """
query Swatches($after: String) {
  metaobjects(type: "shopify--color-pattern", first: 250, after: $after) {
    nodes { id field(key: "label") { value } }
    pageInfo { hasNextPage endCursor }
  }
}"""


def _tag_rule(col: dict) -> str:
    for r in ((col.get("ruleSet") or {}).get("rules") or []):
        if r.get("column") == "TAG" and r.get("relation") == "EQUALS":
            return r.get("condition") or ""
    return ""


def org_for(record: dict) -> tuple[str, dict, dict, str]:
    """(org key, org entry, whole config, problem) from pod2twc's registry --
    the one the Clone button uses. Matched on vendor string, never on a key:
    pod2twc's "hoh" is Highway of Holiness, not Haven of Hope (doc 14)."""
    cfg = clone._config()
    orgs = cfg.get("orgs") or {}
    if not orgs:
        return "", {}, cfg, f"Could not read pod2twc's org registry at {clone.CONFIG}."
    records = [r for r in store.list_partners() if not r.get("_seed_check")]
    mapping, _ = dashboard.vendor_map(records)
    pid = store.partner_id(record)
    mine = ({k for k, r in mapping.items() if store.partner_id(r) == pid}
            or set(dashboard.partner_vendors(record)))
    hits = [k for k, o in orgs.items() if dashboard.vendor_key(o.get("vendor", "")) in mine]
    if not hits:
        return "", {}, cfg, (f"{record.get('org_name')} is not in pod2twc's org registry "
                             f"({clone.CONFIG.name}). Add it there — vendor, tag, collection, pickup "
                             "locations — the same as for the Clone button (doc 14).")
    if len(hits) > 1:
        return "", {}, cfg, f"{record.get('org_name')} matches more than one org in {clone.CONFIG.name}: {hits}."
    return hits[0], orgs[hits[0]], cfg, ""


def context(record: dict, title: str) -> dict:
    """Everything needed about this church. Reads only."""
    ctx: dict = {"errors": [], "scopes": set(), "collection": None, "vendor": "", "store_name": "",
                 "org": "", "org_tag": "", "profile": None, "publications": [], "house": None,
                 "keep": [], "locations": [], "ministries": [], "swatches": None, "dupes": [],
                 "has_inhouse_def": False, "siblings": 0, "sort": ""}
    if not configured():
        ctx["errors"].append("Shopify credentials are not set (Settings › Shopify).")
        return ctx
    key, org, cfg, problem = org_for(record)
    if problem:
        ctx["errors"].append(problem)
        return ctx
    ctx.update({"org": key, "vendor": org["vendor"], "org_tag": org.get("tag") or "",
                "store_name": org.get("store_name") or org["vendor"],
                "publications": list(cfg.get("publications") or [])})
    house_id = cfg.get("house_location") or ""
    ctx["keep"] = [house_id] + [l for l in org.get("pickup_locations") or [] if l != house_id]
    res = _gql(CONTEXT_QUERY, {"col": org["collection_id"]})
    if not res["ok"]:
        ctx["errors"].append(f"Could not read Shopify: {res['error']}")
        return ctx
    d = res["data"]
    ctx["scopes"] = {s["handle"] for s in ((d.get("currentAppInstallation") or {}).get("accessScopes") or [])}
    ctx["locations"] = list((d.get("locations") or {}).get("nodes") or [])
    names = {l["id"]: l["name"] for l in ctx["locations"]}
    ctx["house"] = {"id": house_id, "name": names.get(house_id, "")} if house_id in names else None
    ctx["keep_names"] = [names.get(l, l) for l in ctx["keep"]]
    ctx["has_inhouse_def"] = any(n["key"] == "inhouse_only" for n in
                                 ((d.get("metafieldDefinitions") or {}).get("nodes") or []))
    col = d.get("collection")
    if not col:
        ctx["errors"].append(f"The collection in {clone.CONFIG.name} for {org['vendor']} "
                             f"({org['collection_id']}) does not exist in Shopify.")
        return ctx
    ctx["collection"] = {"id": col["id"], "title": col["title"], "handle": col["handle"],
                         "url": (cfg.get("store_url_base") or f"{STOREFRONT}/collections/") + org["collection_handle"]}
    ctx["sort"] = col.get("sortOrder") or ""
    try:
        kids = json.loads(((col.get("metafield") or {}).get("value")) or "[]")
    except ValueError:
        kids = []
    if kids:
        kr = _gql(CHILDREN_QUERY, {"ids": kids})
        if kr["ok"]:
            for k in (kr["data"].get("nodes") or []):
                if not k:
                    continue
                tag = _tag_rule(k)
                live = any(n.get("isPublished") for n in
                           ((k.get("resourcePublicationsV2") or {}).get("nodes") or []))
                label = re.sub(rf"^{re.escape(col['title'])}\s*[–-]\s*", "", k["title"])
                if tag.startswith("ministry:") and live:
                    ctx["ministries"].append({"tag": tag, "label": label})

    v = ctx["vendor"].replace('"', '\\"')
    vr = _gql(VENDOR_QUERY, {"q": f'vendor:"{v}"', "dup": f'title:"{title.replace(chr(34), "")}"'})
    if vr["ok"]:
        sib = (vr["data"].get("siblings") or {}).get("nodes") or []
        ctx["siblings"] = len(sib)
        for s in [x for x in sib if x["title"].rstrip().endswith(" TWC")] or sib:
            prof = (((s.get("variants") or {}).get("nodes") or [{}])[0] or {}).get("deliveryProfile")
            if prof and "general" not in (prof.get("name") or "").lower():
                ctx["profile"] = prof
                break
        ctx["dupes"] = [p for p in ((vr["data"].get("dupes") or {}).get("nodes") or [])
                        if p["title"].strip().casefold() == title.strip().casefold()]
    else:
        ctx["errors"].append(f"Could not read this church's products: {vr['error']}")

    if "read_metaobjects" in ctx["scopes"]:
        sw, after = {}, None
        for _ in range(8):
            sr = _gql(SWATCH_QUERY, {"after": after})
            if not sr["ok"]:
                sw = None
                break
            block = sr["data"].get("metaobjects") or {}
            for n in block.get("nodes") or []:
                label = ((n.get("field") or {}).get("value") or "").strip().casefold()
                if label:
                    sw.setdefault(label, n["id"])
            if not (block.get("pageInfo") or {}).get("hasNextPage"):
                break
            after = block["pageInfo"]["endCursor"]
        ctx["swatches"] = sw
    return ctx


# ------------------------------------------------------------------- the plan

def defaults(export: dict, record: dict | None) -> dict:
    item = export["item"]
    short = ((record or {}).get("short_name") or "").strip() or (export["project"]["org_name"])
    color = item.get("color") or ""
    ss = export.get("ss") or {}
    sizes = []
    for c in ss.get("colors") or []:
        if c["name"] == color:
            sizes = [s["size"] for s in c["sizes"]]
    blank = item.get("blank") or ""
    return {
        "title": f"{short} - {item['name']} TWC",
        "description": f"<p>{item['name']}.</p>" + (f"<p>Printed in-house on the {blank}.</p>" if blank else ""),
        "price": "", "compare_at": "", "cost": "",
        "up": {s: f"{v:.2f}" for s, v in LADDER.items()},
        "colors": [color] if color else [],
        "sizes": sizes,
        "ministries": [],
        "status": "DRAFT",
        "fallback_weight": "",
    }


def read_form(form, base: dict) -> dict:
    """Merge a submitted form over the defaults. Unknown keys are ignored."""
    if form is None:
        return base
    out = dict(base)
    for k in ("title", "description", "price", "compare_at", "cost", "fallback_weight"):
        if k in form:
            out[k] = (form.get(k) or "").strip()
    out["up"] = {s: (form.get(f"up_{s}") or "").strip() for s in LADDER} if any(
        f"up_{s}" in form for s in LADDER) else base["up"]
    if hasattr(form, "getlist"):
        if "colors_sent" in form:
            out["colors"] = form.getlist("colors")
        if "sizes_sent" in form:
            out["sizes"] = form.getlist("sizes")
        if "ministries_sent" in form:
            out["ministries"] = form.getlist("ministries")
    out["status"] = "ACTIVE" if form.get("status") == "ACTIVE" else "DRAFT"
    return out


def build_plan(export: dict, record: dict | None, ctx: dict, f: dict, run: dict) -> dict:
    """{blocking[], warnings[], plan{}} -- pure: no I/O, so it is testable."""
    blocking: list[str] = list(ctx.get("errors") or [])
    warnings: list[str] = []
    item, proj = export["item"], export["project"]

    # --- may this item become a product at all
    if not item.get("ready"):
        blocking.append(f"{item['code']} is “{item['status'].replace('_', ' ')}”. Only an item the "
                        "customer approved can become a product.")
    if item.get("shopify_product") or (run.get("result") or {}).get("product"):
        gid = item.get("shopify_product") or run["result"]["product"]
        blocking.append(f"{item['code']} is already in Shopify ({gid.rsplit('/', 1)[-1]}). "
                        "Open it from the link above instead of creating a second one.")
    if record is None:
        blocking.append("This Studio project is not linked to a Portal partner. In the Studio: "
                        "Intake & uploads › Project details › Operations Portal partner.")
    if proj.get("brand") == "twc":
        warnings.append("This is a TWC (The Williams Collective) project. It will still be created in "
                        "the Steeple & Stitch store under the church's vendor.")

    # --- church in Shopify
    missing = [s for s in NEEDED_SCOPES if s not in ctx.get("scopes", set())]
    if ctx.get("scopes") and missing:
        blocking.append(f"The Shopify token lacks {', '.join(missing)}. Reconnect from Settings.")
    for scope, what in SOFT_SCOPES.items():
        if ctx.get("scopes") and scope not in ctx["scopes"]:
            warnings.append(f"The token lacks {scope}, so {what}.")
    if ctx.get("collection") and not ctx.get("house"):
        blocking.append("The house location in pod2twc's config.json is not a location in Shopify.")
    if ctx.get("collection") and not ctx.get("profile") and "write_shipping" in ctx.get("scopes", set()):
        first = " and this is its first product" if not ctx.get("siblings") else ""
        warnings.append(f"{ctx['vendor']} has no delivery profile on its other products{first}, so this "
                        "one stays on General shipping rates (doc 20).")
    if ctx.get("ministries") and not f["ministries"]:
        warnings.append("No ministry chosen, so it will show only in the church's Everything view.")
    if ctx.get("sort") == "MANUAL":
        warnings.append("The church's collection is sorted by hand: the new product lands at the end. "
                        "Move it in the admin if it belongs elsewhere.")

    # --- what it is
    title = f["title"].strip()
    if not title:
        blocking.append("A title is required.")
    elif not title.endswith(" TWC"):
        warnings.append("The title does not end in “ TWC”. The Orders board, ssorder and the Products "
                        "page key on that suffix to know a product is made in-house.")
    for d in ctx.get("dupes") or []:
        blocking.append(f"Shopify already has a product called “{d['title']}” ({d['status'].lower()}).")

    price, cost, compare = _money(f["price"]), _money(f["cost"]), _money(f["compare_at"])
    if price is None or price <= 0:
        blocking.append("Enter the retail price for S–XL.")
    if cost is None or cost <= 0:
        blocking.append("Enter the loaded in-house cost (blank + print + labour). It cannot be copied from a "
                        "paired product: in-house blanks cost about half the POD basis (doc 46).")
    if price and cost and cost >= price:
        blocking.append(f"Cost {_fmt(cost)} is at or above the price {_fmt(price)}.")
    if compare is not None and price and compare <= price:
        warnings.append("Compare-at is not above the price, so no sale badge will show.")
    ups = {s: _money(v) if v != "" else 0.0 for s, v in f["up"].items()}
    for s, v in ups.items():
        if v is None:
            blocking.append(f"The {s} upcharge is not a number.")

    # --- the grid, from S&S
    ss = export.get("ss") or {}
    by_color = {c["name"]: c for c in ss.get("colors") or []}
    if not ss:
        blocking.append(f"{item['code']} is not linked to an S&S style, so there are no sizes or weights. "
                        "Link it in the Studio (the S&S blank picker on the item).")
    colors = [c for c in f["colors"] if c in by_color]
    if ss and not colors:
        blocking.append("Choose at least one colour.")
    sizes_wanted = set(f["sizes"])
    fallback = _money(f["fallback_weight"])
    variants, no_weight = [], []
    base_piece = None
    for cname in colors:
        for s in by_color[cname]["sizes"]:
            if s["size"] not in sizes_wanted:
                continue
            if base_piece is None or (s.get("piece") or 0) < base_piece:
                base_piece = s.get("piece") or 0
    for cname in colors:
        for s in sorted(by_color[cname]["sizes"], key=lambda x: _size_rank(x["size"])):
            if s["size"] not in sizes_wanted:
                continue
            up = ups.get(s["size"]) or 0.0
            # cost follows the blank: S&S charges more for the big sizes
            extra = max(0.0, round((s.get("piece") or 0) - (base_piece or 0), 2))
            w = s.get("weight")
            try:
                w = float(w) if w not in (None, "") else None
            except (TypeError, ValueError):
                w = None
            if not w or w <= 0:
                no_weight.append(f"{cname} / {s['size']}")
                w = fallback
            variants.append({"color": cname, "size": s["size"],
                             "price": round(price + up, 2) if price else None,
                             "compare_at": round(compare + up, 2) if compare else None,
                             "cost": round(cost + extra, 2) if cost else None,
                             "weight": round(w, 3) if w else None,
                             "blank_piece": s.get("piece")})
    if ss and colors and not variants:
        blocking.append("Choose at least one size.")
    if len(variants) > MAX_VARIANTS:
        blocking.append(f"{len(variants)} variants — Shopify Basic allows {MAX_VARIANTS}. Drop a colour or sizes.")
    if no_weight:
        if fallback:
            warnings.append(f"S&S gave no weight for {', '.join(no_weight[:6])}"
                            f"{'…' if len(no_weight) > 6 else ''}; using {fallback} lb.")
        else:
            blocking.append(f"S&S gave no weight for {', '.join(no_weight[:6])}"
                            f"{'…' if len(no_weight) > 6 else ''}. Enter a fallback weight — weight 0 "
                            "undercharges shipping on every order (doc 50).")

    # --- swatches
    sw = ctx.get("swatches")
    swatch = {}
    if sw is not None:
        for cname in colors:
            gid = sw.get(cname.strip().casefold())
            if gid:
                swatch[cname] = gid
            else:
                blocking.append(f"No Shopify colour swatch is called “{cname}”. Create it in "
                                "Content › Metaobjects › Color with the hex from S&S — a guessed hex "
                                "renders confidently and wrong (doc 50 C2).")

    # --- images
    mockups = export.get("mockups") or []
    if not mockups:
        warnings.append("No saved mockups, so the product will have no images. Save the mockup in the Studio editor first.")
    if len(colors) > 1:
        warnings.append(f"The mockups show {item.get('color')} only; the other colours will use the same images.")

    tags = ([ctx["org_tag"]] if ctx.get("org_tag") else []) + list(f["ministries"])
    coll = ctx.get("collection") or {}
    metafields = []
    if coll:
        metafields = [
            {"namespace": "custom", "key": "church_store", "type": "collection_reference", "value": coll["id"]},
            {"namespace": "custom", "key": "church_store_name", "type": "single_line_text_field", "value": ctx["store_name"]},
            {"namespace": "custom", "key": "church_store_url", "type": "url", "value": coll["url"]},
        ]
    if ctx.get("has_inhouse_def"):
        metafields.append({"namespace": "custom", "key": "inhouse_only", "type": "boolean", "value": "true"})
    elif ctx.get("collection"):
        warnings.append("The custom.inhouse_only definition does not exist yet, so the product is not "
                        "marked in-house-only (doc 50 prerequisite 0).")

    plan = {
        "title": title, "vendor": ctx.get("vendor") or "",
        "collection": coll, "type": "Accessories" if item["category"] in ACCESSORY else "Apparel",
        "tags": tags, "status": f["status"], "description": f["description"],
        "colors": colors, "sizes": sorted({v["size"] for v in variants}, key=_size_rank),
        "variants": variants, "swatch": swatch, "metafields": metafields,
        "house": ctx.get("house"), "keep": ctx.get("keep") or [], "keep_names": ctx.get("keep_names") or [],
        "profile": ctx.get("profile"), "publications": ctx.get("publications") or [],
        "images": [{"view": m["view"], "url": m["url"], "preview": m.get("preview", "")} for m in mockups],
        "ss_style": ss.get("style") or item.get("ss_style") or "",
    }
    return {"blocking": blocking, "warnings": warnings, "plan": plan}


def fingerprint(plan: dict) -> str:
    keep = {k: plan[k] for k in ("title", "vendor", "tags", "status", "variants", "images", "description")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


def preview(iid: int, form=None) -> dict:
    _load_env()
    export, err = fetch_item(iid)
    if export is None:
        return {"ok": False, "blocking": [err], "warnings": [], "plan": None, "export": None,
                "form": None, "fingerprint": "", "run": load_run(iid)}
    pid = export["project"].get("portal_pid") or ""
    record = store.load(pid) if pid else None
    f = read_form(form, defaults(export, record))
    ctx = context(record, f["title"]) if record is not None else {
        "errors": [], "scopes": set(), "locations": [], "ministries": []}
    run = load_run(iid)
    out = build_plan(export, record, ctx, f, run)
    out.update({"ok": not out["blocking"], "export": export, "record": record, "form": f,
                "ministries": ctx.get("ministries") or [], "fingerprint": fingerprint(out["plan"]),
                "run": run})
    return out


# ------------------------------------------------------------------ commit

def _upload(name: str, data: bytes) -> tuple[str, str]:
    """(resourceUrl, error) through a staged upload."""
    r = _gql("""mutation S($input: [StagedUploadInput!]!) { stagedUploadsCreate(input: $input) {
                  stagedTargets { url resourceUrl parameters { name value } } userErrors { message } } }""",
             {"input": [{"resource": "IMAGE", "filename": name, "mimeType": "image/png",
                         "httpMethod": "POST", "fileSize": str(len(data))}]})
    if not r["ok"]:
        return "", r["error"]
    node = r["data"].get("stagedUploadsCreate") or {}
    if node.get("userErrors"):
        return "", node["userErrors"][0]["message"]
    t = (node.get("stagedTargets") or [None])[0]
    if not t:
        return "", "Shopify gave no upload target."
    try:
        resp = requests.post(t["url"], data={p["name"]: p["value"] for p in t["parameters"]},
                             files={"file": (name, data, "image/png")}, timeout=60)
    except Exception as exc:                                  # noqa: BLE001
        return "", f"upload failed: {exc}"
    if resp.status_code >= 300:
        return "", f"upload refused ({resp.status_code})"
    return t["resourceUrl"], ""


PRODUCT_SET = """
mutation Create($input: ProductSetInput!) {
  productSet(synchronous: true, input: $input) {
    product { id handle title
      variants(first: 100) { nodes { id title
        inventoryItem { id inventoryLevels(first: 30) { nodes { location { id } } } } } } }
    userErrors { field message }
  }
}"""


def product_input(plan: dict, files: list[dict]) -> dict:
    linked = bool(plan["swatch"]) and all(c in plan["swatch"] for c in plan["colors"])
    color_opt: dict = {"name": "Color", "position": 1}
    if linked:
        color_opt["linkedMetafield"] = {"namespace": "shopify", "key": "color-pattern",
                                        "values": [plan["swatch"][c] for c in plan["colors"]]}
    else:
        color_opt["values"] = [{"name": c} for c in plan["colors"]]
    variants = []
    for i, v in enumerate(plan["variants"]):
        color_val = ({"optionName": "Color", "linkedMetafieldValue": plan["swatch"][v["color"]]}
                     if linked else {"optionName": "Color", "name": v["color"]})
        row = {"optionValues": [color_val, {"optionName": "Size", "name": v["size"]}],
               "position": i + 1, "price": f"{v['price']:.2f}", "taxable": True,
               "inventoryPolicy": "DENY",
               "inventoryItem": {"tracked": False, "requiresShipping": True, "cost": f"{v['cost']:.2f}",
                                 "measurement": {"weight": {"value": v["weight"], "unit": "POUNDS"}}}}
        if v.get("compare_at"):
            row["compareAtPrice"] = f"{v['compare_at']:.2f}"
        variants.append(row)
    return {
        "title": plan["title"], "descriptionHtml": plan["description"], "vendor": plan["vendor"],
        "productType": plan["type"], "tags": plan["tags"], "status": plan["status"],
        "productOptions": [color_opt, {"name": "Size", "position": 2,
                                       "values": [{"name": s} for s in plan["sizes"]]}],
        "variants": variants, "metafields": plan["metafields"], "files": files,
    }


def commit(iid: int, form, expected: str, by: str) -> dict:
    """Create the product. Returns the run record (also saved to disk)."""
    pre = preview(iid, form)
    run = pre.get("run") or {}
    if not pre["ok"]:
        return {"ok": False, "error": "The preview has problems — fix them first.", "pre": pre}
    if expected != pre["fingerprint"]:
        return {"ok": False, "error": "Something changed since the preview. Look at the new preview, then create.",
                "pre": pre}
    plan = pre["plan"]
    steps: list[dict] = []
    result = {"started": _now(), "by": by, "steps": steps, "product": "", "ok": False}
    run["result"] = result
    run["plan"] = plan
    _save_run(iid, run)

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": ok, "detail": detail})
        _save_run(iid, run)
        return ok

    # 1. images
    files = []
    for m in plan["images"]:
        try:
            blob = fetch_mockup(m["url"])
        except Exception as exc:                              # noqa: BLE001
            step(f"Image: {m['view']}", False, f"could not read it from the Studio ({exc})")
            continue
        url, err = _upload(f"{re.sub(r'[^a-z0-9]+', '-', plan['title'].lower()).strip('-')}-{m['view']}.png", blob)
        if step(f"Image: {m['view']}", not err, err or "uploaded"):
            files.append({"originalSource": url, "contentType": "IMAGE",
                          "alt": f"{plan['title'].removesuffix(' TWC')}, {m['view'].replace('_', ' ')}"})

    # 2. the product itself
    r = _gql(PRODUCT_SET, {"input": product_input(plan, files)})
    node = (r.get("data") or {}).get("productSet") if r["ok"] else None
    errs = (node or {}).get("userErrors") or []
    product = (node or {}).get("product")
    if not r["ok"] or errs or not product:
        msg = r.get("error") or "; ".join(e["message"] for e in errs) or "Shopify returned no product."
        step("Create product", False, msg)
        result.update({"ok": False, "finished": _now(), "error": msg})
        _save_run(iid, run)
        return {"ok": False, "error": msg, "run": run, "pre": pre}
    gid = product["id"]
    result["product"] = gid
    result["handle"] = product.get("handle", "")
    step("Create product", True, f"{product['title']} · {len(plan['variants'])} variants")

    # 3. stocked where in-house work ships from, plus the church's pickup point
    #    (pod2twc config) -- and nowhere else. Shopify assigns a new product to
    #    every location; an HOH hat left stocked at Germantown and Revive is how
    #    that looks when nobody detaches them. Attach before detach: an item
    #    must stay stocked somewhere (pod2twc 6).
    keep = plan["keep"]
    bad = 0
    for v in (product.get("variants") or {}).get("nodes") or []:
        inv = v["inventoryItem"]
        at = {n["location"]["id"] for n in ((inv.get("inventoryLevels") or {}).get("nodes") or [])}
        for updates in ([{"locationId": l, "activate": True} for l in keep if l not in at],
                        [{"locationId": l, "activate": False} for l in sorted(at) if l not in keep]):
            if not updates:
                continue
            t = _gql("""mutation T($id: ID!, $u: [InventoryBulkToggleActivationInput!]!) {
                         inventoryBulkToggleActivation(inventoryItemId: $id, inventoryItemUpdates: $u) {
                           userErrors { message } } }""", {"id": inv["id"], "u": updates})
            e = (t.get("data") or {}).get("inventoryBulkToggleActivation", {}).get("userErrors") if t["ok"] else None
            if not t["ok"] or e:
                bad += 1
                break
    step(f"Stocked at {' + '.join(plan['keep_names'])} only", bad == 0,
         "every other location detached" if not bad else f"{bad} variant(s) could not be moved — fix in the admin")

    variant_ids = [v["id"] for v in (product.get("variants") or {}).get("nodes") or []]
    # 4. delivery profile
    if plan.get("profile"):
        dp = _gql("""mutation D($id: ID!, $p: DeliveryProfileInput!) { deliveryProfileUpdate(id: $id, profile: $p) {
                      userErrors { message } } }""",
                  {"id": plan["profile"]["id"], "p": {"variantsToAssociate": variant_ids}})
        e = (dp.get("data") or {}).get("deliveryProfileUpdate", {}).get("userErrors") if dp["ok"] else None
        step(f"Delivery profile: {plan['profile']['name']}", dp["ok"] and not e,
             (dp.get("error") or (e[0]["message"] if e else "assigned")))
    # 5. sales channels
    if plan.get("publications"):
        pb = _gql("""mutation P($id: ID!, $in: [PublicationInput!]!) { publishablePublish(id: $id, input: $in) {
                      userErrors { message } } }""",
                  {"id": gid, "in": [{"publicationId": p} for p in plan["publications"]]})
        e = (pb.get("data") or {}).get("publishablePublish", {}).get("userErrors") if pb["ok"] else None
        step("Sales channels", pb["ok"] and not e,
             pb.get("error") or (e[0]["message"] if e else f"{len(plan['publications'])} channel(s)"))
    # 6. the Studio item links to it
    err = tell_studio(iid, gid, plan["title"], by)
    step("Linked in the Studio", not err, err or "the item shows “In Shopify”")

    result["ok"] = all(s["ok"] for s in steps if s["step"] == "Create product")
    result["partial"] = not all(s["ok"] for s in steps)
    result["finished"] = _now()
    _save_run(iid, run)
    return {"ok": True, "run": run, "pre": pre}
