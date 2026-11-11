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
import html
import json
import os
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import requests

from . import clone, dashboard, store, twc_shop
from .shopify_pull import _load_env, configured
from .storefront import _gql as _ss_gql


def _gql(query: str, variables: dict | None = None, store: str = "ss") -> dict:
    """One GraphQL call against the store this product belongs to: Steeple &
    Stitch for partner projects, The Williams Collective for business
    projects (doc 73). Never raises."""
    if store == "twc":
        return twc_shop.gql(query, variables)
    return _ss_gql(query, variables)

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
PRINTED = {"dtf", "screen_print"}          # anything else is embroidery / patch work
# A suggestion only, shown as the placeholder of the Special instructions box.
SIZE_UP_NOTE = "Comfort Colors runs small — please order one size up."
# Size chart columns, in the order Larry's listings use; anything else S&S
# reports (neck size, inseam…) follows.
SPEC_ORDER = ["Chest", "Body Width", "Chest Width", "Body Length", "Full Body Length", "Length",
              "Sleeve Length", "Sleeve Length (from center back)"]


def description_html(export: dict, f: dict, sizes: list[str]) -> str:
    """The listing description, built like the Lighthouse crewnecks: the
    optional special instructions (red, bold, at the top -- one line each),
    the blank's name and S&S bullets, and the size chart for the sizes being
    sold. Garment care is NOT included:
    the theme adds it under the description (doc 47 -- it would show twice)."""
    ss = export.get("ss") or {}
    def esc(text):                       # text nodes only, so quotes stay as typed (28")
        return html.escape(str(text), quote=False)
    parts = []
    lines = [ln.strip() for ln in (f.get("special") or "").splitlines() if ln.strip()]
    if lines:
        parts.append('<h3><span style="color: rgb(255, 42, 0);"><strong>'
                     + "<br>".join(esc(ln) for ln in lines) + "</strong></span></h3>")
    if f.get("details") and ss:
        name = " ".join(x for x in (ss.get("brand"), ss.get("style_name"), ss.get("title")) if x) or ss.get("style", "")
        parts.append(f"<p><strong>{esc(name)}</strong></p>")
        if ss.get("bullets"):
            parts.append("<ul>\n" + "\n".join(f"<li>{esc(b)}</li>" for b in ss["bullets"]) + "\n</ul>")
        chart = ss.get("specs") or {}
        rows = [r for r in chart.get("rows") or [] if r["size"] in sizes]
        names = sorted(chart.get("names") or [],
                       key=lambda n: SPEC_ORDER.index(n) if n in SPEC_ORDER else len(SPEC_ORDER))
        if rows and names:
            cell = 'style="padding: 8px; text-align: left;"'

            def val(v):
                v = v.strip()
                return v if not v or v[-1] in '"”' or not v[-1].isdigit() else v + '"'
            head = "".join(f"<th {cell}>{esc(n.capitalize() if n.isupper() else n)}</th>" for n in names)
            body = "".join("<tr>" + f"<td {cell}>{esc(r['size'])}</td>" +
                           "".join(f"<td {cell}>{esc(val(r['values'].get(n, '')))}</td>" for n in names) + "</tr>\n"
                           for r in rows)
            parts.append(f'<table id="sizechart" style="width: 100%; border-collapse: collapse;" border="1">\n'
                         f"<tbody>\n<tr><th {cell}>Size</th>{head}</tr>\n{body}</tbody>\n</table>")
    return "\n".join(parts)

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


def admin_url(gid: str, store: str = "ss") -> str:
    shop = twc_shop.domain() if store == "twc" else os.environ.get("SHOPIFY_STORE", "")
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
    """The 3200 px store image the Studio renders from the saved mockup."""
    with urllib.request.urlopen(f"{STUDIO_INTERNAL}{url}", timeout=60) as r:
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


# ------------------------------------------------- a business, in TWC's store

TWC_CONTEXT_QUERY = """
query T($q: String!, $dup: String!) {
  currentAppInstallation { accessScopes { handle } }
  shop { name primaryDomain { url } }
  locations(first: 50) { nodes { id name isActive } }
  publications(first: 20) { nodes { id name } }
  collections(first: 10, query: $q) { nodes { id title handle
      ruleSet { rules { column relation condition } } } }
  dupes: products(first: 5, query: $dup) { nodes { id title status } }
}"""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def private_handle(vendor: str, domain: str) -> str:
    """The link-only collection's handle: name + 6 characters nobody would
    guess (doc 54). Derived, not random, so the preview and the create agree
    and a second preview names the same collection."""
    tail = hashlib.sha256(f"{domain}|{vendor}|twc-business".encode()).hexdigest()[:6]
    return f"{_slug(vendor)}-{tail}"


def context_twc(export: dict, title: str) -> dict:
    """A business customer sold from The Williams Collective's store (doc 73):
    no partner record, no payout, no church fields. Reads only."""
    proj = export["project"]
    vendor = proj["org_name"].strip()
    ctx: dict = {"store": "twc", "errors": [], "scopes": set(), "collection": None, "new_collection": None,
                 "vendor": vendor, "store_name": vendor, "org": "", "org_tag": _slug(vendor), "profile": None,
                 "publications": [], "house": None, "keep": [], "keep_names": [], "locations": [],
                 "ministries": [], "swatches": None, "dupes": [], "has_inhouse_def": False,
                 "siblings": 0, "sort": "", "storefront": ""}
    if not twc_shop.configured():
        ctx["errors"].append("The Williams Collective store is not connected yet. Settings › "
                             "The Williams Collective store: save the app's details, then Connect.")
        return ctx
    v = vendor.replace('"', '\\"')
    r = _gql(TWC_CONTEXT_QUERY, {"q": f'title:"{v}"', "dup": f'title:"{title.replace(chr(34), "")}"'}, store="twc")
    if not r["ok"]:
        ctx["errors"].append(f"Could not read the TWC store: {r['error']}")
        return ctx
    d = r["data"]
    ctx["scopes"] = {s["handle"] for s in ((d.get("currentAppInstallation") or {}).get("accessScopes") or [])}
    base = (((d.get("shop") or {}).get("primaryDomain") or {}).get("url") or "").rstrip("/")
    ctx["storefront"] = base
    locs = [l for l in ((d.get("locations") or {}).get("nodes") or []) if l.get("isActive", True)]
    ctx["locations"] = locs
    want = twc_shop.house_location_name().strip().casefold()
    house = (next((l for l in locs if l["name"].strip().casefold() == want), None) if want
             else (locs[0] if len(locs) == 1 else None))
    if house:
        ctx.update({"house": {"id": house["id"], "name": house["name"]}, "keep": [house["id"]],
                    "keep_names": [house["name"]]})
    elif want:
        ctx["errors"].append(f"The TWC store has no location called “{twc_shop.house_location_name()}”. "
                             "Fix Ships from in Settings › The Williams Collective store.")
    else:
        ctx["errors"].append(f"The TWC store has {len(locs)} locations. Name the one it ships from in "
                             "Settings › The Williams Collective store › Ships from.")
    ctx["publications"] = [p["id"] for p in ((d.get("publications") or {}).get("nodes") or [])
                           if (p.get("name") or "").lower() == "online store"]
    for c in ((d.get("collections") or {}).get("nodes") or []):
        rules = ((c.get("ruleSet") or {}).get("rules") or [])
        if any(x["column"] == "VENDOR" and x["relation"] == "EQUALS"
               and dashboard.vendor_key(x["condition"]) == dashboard.vendor_key(vendor) for x in rules):
            ctx["collection"] = {"id": c["id"], "title": c["title"], "handle": c["handle"],
                                 "url": f"{base}/collections/{c['handle']}"}
            break
    if not ctx["collection"]:
        handle = private_handle(vendor, twc_shop.domain())
        ctx["new_collection"] = {"title": vendor, "handle": handle, "url": f"{base}/collections/{handle}"}
    ctx["dupes"] = [p for p in ((d.get("dupes") or {}).get("nodes") or [])
                    if p["title"].strip().casefold() == title.strip().casefold()]
    if "read_metaobjects" in ctx["scopes"]:
        sw, after = {}, None
        for _ in range(8):
            sr = _gql(SWATCH_QUERY, {"after": after}, store="twc")
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


# ------------------------------------------------------------- swatches

# Shopify's standard colour values (checked against the API, 3.84): note 6 is
# Beige and 7 is Brown.
TAXONOMY = {"black": 1, "blue": 2, "white": 3, "gold": 4, "silver": 5, "beige": 6, "brown": 7, "gray": 8,
            "green": 9, "orange": 10, "pink": 11, "purple": 12, "red": 13, "yellow": 14, "navy": 15}
SOLID = "gid://shopify/TaxonomyValue/2874"
# words in an S&S colour family or name -> taxonomy colour; first match wins
_WORDS = [("navy", "navy"), ("black", "black"), ("jet", "black"), ("white", "white"),
          ("natural", "beige"), ("ivory", "beige"), ("cream", "beige"), ("beige", "beige"), ("khaki", "beige"),
          ("sand", "beige"), ("tan", "beige"), ("oatmeal", "beige"), ("bone", "beige"),
          ("brown", "brown"), ("chocolate", "brown"), ("espresso", "brown"), ("coffee", "brown"),
          ("maroon", "red"), ("burgundy", "red"), ("cardinal", "red"), ("crimson", "red"), ("red", "red"),
          ("pink", "pink"), ("fuchsia", "pink"), ("magenta", "pink"), ("coral", "pink"),
          ("purple", "purple"), ("violet", "purple"), ("lavender", "purple"), ("orchid", "purple"),
          ("orange", "orange"), ("yellow", "yellow"), ("gold", "yellow"), ("mustard", "yellow"),
          ("olive", "green"), ("forest", "green"), ("kelly", "green"), ("sage", "green"), ("mint", "green"),
          ("military", "green"), ("green", "green"), ("teal", "blue"), ("turquoise", "blue"), ("aqua", "blue"),
          ("royal", "blue"), ("sapphire", "blue"), ("sky", "blue"), ("blue", "blue"),
          ("grey", "gray"), ("gray", "gray"), ("charcoal", "gray"), ("ash", "gray"), ("heather", "gray"),
          ("silver", "gray"), ("smoke", "gray"), ("pepper", "gray"), ("graphite", "gray")]


# Shopify only accepts a Color option linked to swatches on a product whose
# category has a colour attribute: the NFG tee (no category) was refused with
# "At least one value for the option linked to the 'shopify.color-pattern'
# metafield is invalid". Every linked product on the S&S store has one (3.85).
CATEGORY = {
    "tshirt": "gid://shopify/TaxonomyCategory/aa-1-13-8",       # Clothing Tops > T-Shirts
    "longsleeve": "gid://shopify/TaxonomyCategory/aa-1-13-8",
    "crewneck": "gid://shopify/TaxonomyCategory/aa-1-13-14",    # Clothing Tops > Sweatshirts
    "hoodie": "gid://shopify/TaxonomyCategory/aa-1-1-7-2",      # Activewear ... > Hoodies
    "polo": "gid://shopify/TaxonomyCategory/aa-1-13-6",         # Clothing Tops > Polos
    "hat": "gid://shopify/TaxonomyCategory/aa-2-17-1",          # Hats > Baseball Caps
    "beanie": "gid://shopify/TaxonomyCategory/aa-2-17-2",       # Hats > Beanies
}
CATEGORY_DEFAULT = "gid://shopify/TaxonomyCategory/aa-1-13"     # Clothing Tops
PRIVATE_TAG = "private-store"        # TWC theme hides these everywhere but their own collection
PRIVATE_TEMPLATE = "private"         # product.private / collection.private in the TWC theme


def norm_hex(value) -> str:
    h = (value or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    return f"#{h.upper()}" if re.fullmatch(r"[0-9A-Fa-f]{6}", h) else ""


def _hue_word(hx: str) -> str:
    import colorsys
    r, g, b = (int(hx[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, sat, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360
    if sat < 0.15 or v < 0.12:
        return "black" if v < 0.2 else "white" if v > 0.9 and sat < 0.08 else "beige" if sat >= 0.08 and 20 <= h <= 60 and v > 0.7 else "gray"
    if 20 <= h <= 50 and sat < 0.35 and v > 0.7:
        return "beige"
    if h < 15 or h >= 345:
        return "red"
    if h < 45:
        return "brown" if v < 0.6 else "orange"
    if h < 70:
        return "yellow"
    if h < 170:
        return "green"
    if h < 250:
        return "navy" if v < 0.45 else "blue"
    if h < 290:
        return "purple"
    return "pink"


def color_taxonomy(family: str, name: str, hx: str) -> str:
    """The taxonomy colour a swatch filters under: S&S colour family first,
    then the colour's own name, then the hue of its hex."""
    for text in (family or "", name or ""):
        t = text.casefold()
        for word, tax in _WORDS:
            if re.search(rf"\b{word}", t):
                return f"gid://shopify/TaxonomyValue/{TAXONOMY[tax]}"
    return f"gid://shopify/TaxonomyValue/{TAXONOMY[_hue_word(hx)]}" if hx else ""


# ------------------------------------------------------------------- the plan

def defaults(export: dict, record: dict | None) -> dict:
    item = export["item"]
    org = export["project"]["org_name"]
    # "NFG - National Fuel" -> "NFG"; a partner's own short name wins.
    short = ((record or {}).get("short_name") or "").strip() or org.split(" - ")[0].strip() or org
    color = item.get("color") or ""
    ss = export.get("ss") or {}
    sizes = []
    for c in ss.get("colors") or []:
        if c["name"] == color:
            sizes = [s["size"] for s in c["sizes"]]
    blank = item.get("blank") or ""
    return {
        "title": f"{short} - {item['name']} TWC",
        "special": "",
        "details": True,
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
    for k in ("title", "price", "compare_at", "cost", "fallback_weight"):
        if k in form:
            out[k] = (form.get(k) or "").strip()
    if "special" in form:
        out["special"] = (form.get("special") or "").strip()[:600]
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
    if "options_sent" in form:
        out["details"] = bool(form.get("details"))
    return out


def build_plan(export: dict, record: dict | None, ctx: dict, f: dict, run: dict) -> dict:
    """{blocking[], warnings[], plan{}} -- pure: no I/O, so it is testable."""
    blocking: list[str] = list(ctx.get("errors") or [])
    warnings: list[str] = []
    item, proj = export["item"], export["project"]
    business = ctx.get("store") == "twc"

    # --- may this item become a product at all
    if not item.get("ready"):
        blocking.append(f"{item['code']} is “{item['status'].replace('_', ' ')}”. Only an item the "
                        "customer approved can become a product.")
    gone = ctx.get("gone") or ""
    if item.get("shopify_product") or (run.get("result") or {}).get("product"):
        gid = item.get("shopify_product") or run["result"]["product"]
    else:
        gid = ""
    if gid and gid == gone:
        warnings.append(f"{item['code']} was in Shopify ({gid.rsplit('/', 1)[-1]}) but that product has been "
                        "deleted there, so it can be created again. ssorder needs the new product number "
                        "afterwards (the line at the bottom of the result).")
    elif gid:
        blocking.append(f"{item['code']} is already in Shopify ({gid.rsplit('/', 1)[-1]}). "
                        "Open it from the link above instead of creating a second one.")
    if record is None and not business:
        blocking.append("This Studio project is not linked to a Portal partner. In the Studio: "
                        "Intake & uploads › Project details › Operations Portal partner.")

    # --- church in Shopify
    missing = [s for s in NEEDED_SCOPES if s not in ctx.get("scopes", set())]
    if ctx.get("scopes") and missing:
        blocking.append(f"The Shopify token lacks {', '.join(missing)}. Reconnect from Settings.")
    for scope, what in SOFT_SCOPES.items():
        if business and scope == "write_shipping":
            continue                    # TWC sells on its store's own shipping rates
        if ctx.get("scopes") and scope not in ctx["scopes"]:
            warnings.append(f"The token lacks {scope}, so {what}.")
    if business and ctx.get("new_collection"):
        nc = ctx["new_collection"]
        warnings.append(f"{ctx['vendor']} has no collection in the TWC store yet. Creating this product also "
                        f"creates a link-only collection: {nc['url']} — not in the menus or search; anyone "
                        "with the link can buy (doc 54).")
    if ctx.get("collection") and not ctx.get("house") and not business:
        blocking.append("The house location in pod2twc's config.json is not a location in Shopify.")
    if ctx.get("collection") and not ctx.get("profile") and "write_shipping" in ctx.get("scopes", set()):
        first = " and this is its first product" if not ctx.get("siblings") else ""
        warnings.append(f"{ctx['vendor']} has no delivery profile on its other products{first}, so this "
                        "one stays on General shipping rates (doc 20).")
    if ctx.get("ministries") and not f["ministries"]:
        warnings.append("No ministry chosen, so it will show only in the church's Everything view.")
    if ctx.get("sort") == "MANUAL":
        warnings.append("The collection is sorted by hand: the new product lands at the end. "
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
    swatch, swatch_new = {}, {}
    if sw is not None:
        missing = []
        for cname in colors:
            gid = sw.get(cname.strip().casefold())
            if gid:
                swatch[cname] = gid
            else:
                missing.append(cname)
        if missing and business:
            # The TWC store starts with no swatches. With write_metaobjects the
            # missing ones are made from the S&S hex (never a guess, doc 50 C2);
            # without it the Color option is created plain and the hexes are
            # given so swatches can be added later (doc 73).
            can_make = "write_metaobjects" in ctx.get("scopes", set())
            new, cant = {}, []
            for cname in missing:
                info = by_color.get(cname) or {}
                hx = norm_hex(info.get("hex"))
                tax = color_taxonomy(info.get("family") or "", cname, hx) if hx else ""
                if hx and tax:
                    new[cname] = {"hex": hx, "taxonomy": tax}
                else:
                    cant.append(cname)
            if can_make and not cant:
                swatch_new = new
                warnings.append("New colour swatches will be made in the TWC store from the S&S hex codes: " +
                                ", ".join(f"{c} {v['hex']}" for c, v in new.items()) + ".")
            else:
                hexes = ", ".join(f"{c} {(by_color.get(c) or {}).get('hex') or '(no hex from S&S)'}" for c in missing)
                why = ("S&S gave no usable hex for " + ", ".join(cant)) if can_make else \
                      ("the TWC connection cannot make swatches yet (it needs write_metaobjects — add it to the "
                       "app version, then Reconnect in Settings)")
                warnings.append(f"The TWC store has no colour swatch for {', '.join(missing)} and {why}, so Color "
                                f"will be a plain dropdown with no swatches. S&S colours — {hexes}.")
                swatch = {}
        else:
            for cname in missing:
                blocking.append(f"No Shopify colour swatch is called “{cname}”. Create it in "
                                "Content › Metaobjects › Color with the hex from S&S — a guessed hex "
                                "renders confidently and wrong (doc 50 C2).")

    # --- images
    # Each colour gets its own images: the item's colour uses the saved
    # mockups, every other colour its 🎨 Colours render from the Studio. Each
    # variant then shows its colour's front image (doc 72, 3.84).
    mockups = export.get("mockups") or []
    rendered = {k.strip().casefold(): v for k, v in (export.get("colourways") or {}).items()}
    own = (item.get("color") or "").strip().casefold()
    images, no_render = [], []
    for cname in colors:
        key = cname.strip().casefold()
        imgs = mockups if key == own else rendered.get(key) or []
        if not imgs:
            no_render.append(cname)
        images += [{"view": m["view"], "url": m["url"], "preview": m.get("preview", ""), "color": cname}
                   for m in imgs]
    if not images:
        images = [{"view": m["view"], "url": m["url"], "preview": m.get("preview", ""), "color": ""}
                  for m in mockups]
    if not mockups and not images:
        warnings.append("No saved mockups, so the product will have no images. Save the mockup in the Studio editor first.")
    elif no_render:
        warnings.append(f"No colour render for {', '.join(no_render)}, so "
                        f"{'it shows' if len(no_render) == 1 else 'they show'} the first image. To fix: Studio "
                        "editor › 🎨 Colours › tick "
                        f"{'it' if len(no_render) == 1 else 'them'} › Render, then refresh this preview.")

    # Only ministries this church actually offers (published sub-collections);
    # a business has none.
    offered = {m["tag"] for m in ctx.get("ministries") or []}
    tags = ([ctx["org_tag"]] if ctx.get("org_tag") else []) + [m for m in f["ministries"] if m in offered]
    if (item.get("method") or "dtf") not in PRINTED:
        # The theme's print-care block (doc 47) is for printed items. An
        # embroidered or patch piece says so in its tags, whatever its title.
        tags.append("no-print-care")
    sizes_sold = sorted({v["size"] for v in variants}, key=_size_rank)
    description = description_html(export, f, sizes_sold)
    if f.get("details") and ss and not (ss.get("specs") or {}).get("rows"):
        warnings.append("S&S has no size chart for this style, so the description has none. Add one in the admin if it needs it.")
    coll = ctx.get("collection") or {}
    metafields = []
    if coll:
        metafields = [
            {"namespace": "custom", "key": "church_store", "type": "collection_reference", "value": coll["id"]},
            {"namespace": "custom", "key": "church_store_name", "type": "single_line_text_field", "value": ctx["store_name"]},
            {"namespace": "custom", "key": "church_store_url", "type": "url", "value": coll["url"]},
        ]
    if business:
        # A business's own corner of the TWC store (3.86): the theme's
        # *.private templates drop the menu, search, breadcrumbs and
        # suggestions, and the rest of the store leaves out anything tagged
        # private-store -- so its employees see only their own items.
        tags.append(PRIVATE_TAG)
    if business:
        # A business listing is link-only: out of storefront search and the
        # sitemap (doc 54). No church fields -- there is no partner.
        metafields = [{"namespace": "seo", "key": "hidden", "type": "number_integer", "value": "1"}]
    elif ctx.get("has_inhouse_def"):
        metafields.append({"namespace": "custom", "key": "inhouse_only", "type": "boolean", "value": "true"})
    elif ctx.get("collection"):
        warnings.append("The custom.inhouse_only definition does not exist yet, so the product is not "
                        "marked in-house-only (doc 50 prerequisite 0).")

    plan = {
        "title": title, "vendor": ctx.get("vendor") or "",
        "collection": coll, "type": "Accessories" if item["category"] in ACCESSORY else "Apparel",
        "category": CATEGORY.get(item["category"], CATEGORY_DEFAULT),
        "template_suffix": PRIVATE_TEMPLATE if business else "",
        "tags": tags, "status": f["status"], "description": description,
        "colors": colors, "sizes": sizes_sold,
        "variants": variants, "swatch": swatch, "swatch_new": swatch_new, "metafields": metafields,
        "house": ctx.get("house"), "keep": ctx.get("keep") or [], "keep_names": ctx.get("keep_names") or [],
        "profile": ctx.get("profile"), "publications": ctx.get("publications") or [],
        "images": images,
        "ss_style": ss.get("style") or item.get("ss_style") or "",
        "store": "twc" if business else "ss", "new_collection": ctx.get("new_collection"),
        "storefront": ctx.get("storefront") or "",
    }
    return {"blocking": blocking, "warnings": warnings, "plan": plan}


def fingerprint(plan: dict) -> str:
    keep = {k: plan.get(k) for k in ("title", "vendor", "tags", "status", "variants", "images", "description",
                                     "template_suffix",
                                     "store", "new_collection", "swatch_new")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _setup(iid: int, form=None):
    """Everything both Create and Add colours start from. (None, error) when
    the Studio item cannot be read."""
    _load_env()
    export, err = fetch_item(iid)
    if export is None:
        return None, err
    business = export["project"].get("brand") == "twc"
    pid = export["project"].get("portal_pid") or ""
    record = store.load(pid) if pid and not business else None
    f = read_form(form, defaults(export, record))
    if business:
        ctx = context_twc(export, f["title"])
    elif record is not None:
        ctx = context(record, f["title"])
    else:
        ctx = {"errors": [], "scopes": set(), "locations": [], "ministries": []}
    return {"export": export, "business": business, "record": record, "f": f, "ctx": ctx,
            "run": load_run(iid)}, ""


def preview(iid: int, form=None) -> dict:
    su, err = _setup(iid, form)
    if su is None:
        return {"ok": False, "blocking": [err], "warnings": [], "plan": None, "export": None,
                "form": None, "fingerprint": "", "run": load_run(iid)}
    export, business, record, f, ctx, run = (su[k] for k in ("export", "business", "record", "f", "ctx", "run"))
    old = export["item"].get("shopify_product") or (run.get("result") or {}).get("product") or ""
    if old and not ctx.get("errors"):
        # deleted in the admin since? then it may be made again (3.85)
        g = _gql("query G($id: ID!) { product(id: $id) { id } }", {"id": old},
                 store=(run.get("result") or {}).get("store") or ("twc" if business else "ss"))
        if g["ok"] and (g.get("data") or {}).get("product") is None:
            ctx["gone"] = old
            if (run.get("result") or {}).get("product") == old:
                run = {k: v for k, v in run.items() if k not in ("result", "plan")}
    out = build_plan(export, record, ctx, f, run)
    out.update({"ok": not out["blocking"], "export": export, "record": record, "form": f, "business": business,
                "ministries": ctx.get("ministries") or [], "fingerprint": fingerprint(out["plan"]),
                "run": run})
    return out


# ------------------------------------------------------------------ commit

def _upload(name: str, data: bytes, store: str = "ss") -> tuple[str, str]:
    """(resourceUrl, error) through a staged upload."""
    r = _gql("""mutation S($input: [StagedUploadInput!]!) { stagedUploadsCreate(input: $input) {
                  stagedTargets { url resourceUrl parameters { name value } } userErrors { message } } }""",
             {"input": [{"resource": "IMAGE", "filename": name, "mimeType": "image/jpeg",
                         "httpMethod": "POST", "fileSize": str(len(data))}]}, store=store)
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
                             files={"file": (name, data, "image/jpeg")}, timeout=60)
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


def product_input(plan: dict, files: list[dict], front: dict | None = None, plain: bool = False) -> dict:
    """front: colour -> the staged URL of its first image (the variant photo).
    plain: no swatch links and no variant photos -- the fallback when
    Shopify refuses either, so the product is still made."""
    front = {} if plain else (front or {})
    linked = not plain and bool(plan["swatch"]) and all(c in plan["swatch"] for c in plan["colors"])
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
        if front.get(v["color"]):
            row["file"] = {"originalSource": front[v["color"]], "contentType": "IMAGE"}
        variants.append(row)
    return {
        "title": plan["title"], "descriptionHtml": plan["description"], "vendor": plan["vendor"],
        "productType": plan["type"], "tags": plan["tags"], "status": plan["status"],
        "productOptions": [color_opt, {"name": "Size", "position": 2,
                                       "values": [{"name": s} for s in plan["sizes"]]}],
        "variants": variants, "metafields": plan["metafields"], "files": files,
        **({"category": plan["category"]} if plan.get("category") and not plain else {}),
        **({"templateSuffix": plan["template_suffix"]} if plan.get("template_suffix") else {}),
    }


SWATCH_CREATE = """
mutation M($m: MetaobjectCreateInput!) { metaobjectCreate(metaobject: $m) {
  metaobject { id } userErrors { field message code } } }"""


def _swatch_input(name: str, s: dict, publish: bool) -> dict:
    m = {"type": "shopify--color-pattern", "fields": [
        {"key": "label", "value": name},
        {"key": "color", "value": s["hex"]},
        {"key": "color_taxonomy_reference", "value": json.dumps([s["taxonomy"]])},
        {"key": "pattern_taxonomy_reference", "value": SOLID}]}
    if publish:
        m["capabilities"] = {"publishable": {"status": "ACTIVE"}}
    return m


def _create_swatch(name: str, s: dict, shop: str) -> tuple[str, str]:
    err = ""
    for publish in (True, False):           # some stores' definition is not publishable
        r = _gql(SWATCH_CREATE, {"m": _swatch_input(name, s, publish)}, store=shop)
        node = ((r.get("data") or {}).get("metaobjectCreate") or {}) if r["ok"] else {}
        if node.get("metaobject"):
            return node["metaobject"]["id"], ""
        err = r.get("error") or "; ".join(x["message"] for x in node.get("userErrors") or []) or "no swatch returned"
        if "capabilit" not in err.lower() and "publishable" not in err.lower():
            break
    return "", err


def make_swatches(new: dict, shop: str) -> tuple[dict, list[str]]:
    """Create shopify--color-pattern entries. ({name: gid}, [failures]).
    If the store has never had swatches, Shopify's standard Color definition
    is turned on once and the create tried again (write_metaobject_definitions)."""
    made, failed, enabled = {}, [], False
    for name, s in new.items():
        gid, err = _create_swatch(name, s, shop)
        if not gid and not enabled:
            _gql("""mutation E { standardMetaobjectDefinitionEnable(type: "shopify--color-pattern") {
                     metaobjectDefinition { id } userErrors { message } } }""", store=shop)
            enabled = True
            gid, err = _create_swatch(name, s, shop)
        if gid:
            made[name] = gid
        else:
            failed.append(f"{name}: {err}")
    return made, failed


def _upload_images(plan: dict, step, shop: str) -> tuple[list[dict], dict]:
    """Upload plan["images"]; (files for productSet, colour -> first URL)."""
    files, front = [], {}
    stem = re.sub(r'[^a-z0-9]+', '-', plan['title'].lower()).strip('-')
    for m in plan["images"]:
        color = m.get("color") or ""
        label = f"{color} {m['view']}".strip() if color else m["view"]
        try:
            blob = fetch_mockup(m["url"])
        except Exception as exc:                              # noqa: BLE001
            step(f"Image: {label}", False, f"could not read it from the Studio ({exc})")
            continue
        cslug = re.sub(r'[^a-z0-9]+', '-', color.lower()).strip('-')
        url, err = _upload(f"{stem}{'-' + cslug if cslug else ''}-{m['view']}.jpg", blob, store=shop)
        if step(f"Image: {label}", not err, err or "uploaded"):
            alt = ", ".join(x for x in (plan['title'].removesuffix(' TWC'), color, m['view'].replace('_', ' ')) if x)
            files.append({"originalSource": url, "contentType": "IMAGE", "alt": alt})
            if color and color not in front:
                front[color] = url
    return files, front


def _keep_only(variants: list[dict], keep: list[str], shop: str) -> int:
    """Stock each variant at the keep locations and nowhere else; returns how
    many could not be moved. Attach before detach: an item must stay stocked
    somewhere (pod2twc 6)."""
    bad = 0
    for v in variants:
        inv = v["inventoryItem"]
        at = {n["location"]["id"] for n in ((inv.get("inventoryLevels") or {}).get("nodes") or [])}
        for updates in ([{"locationId": l, "activate": True} for l in keep if l not in at],
                        [{"locationId": l, "activate": False} for l in sorted(at) if l not in keep]):
            if not updates:
                continue
            t = _gql("""mutation T($id: ID!, $u: [InventoryBulkToggleActivationInput!]!) {
                         inventoryBulkToggleActivation(inventoryItemId: $id, inventoryItemUpdates: $u) {
                           userErrors { message } } }""", {"id": inv["id"], "u": updates}, store=shop)
            e = (t.get("data") or {}).get("inventoryBulkToggleActivation", {}).get("userErrors") if t["ok"] else None
            if not t["ok"] or e:
                bad += 1
                break
    return bad


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

    shop = plan.get("store") or "ss"
    result["store"] = shop

    # 0. a business's first product: its link-only collection (doc 54 pattern)
    nc = plan.get("new_collection")
    if nc:
        cr = _gql("""mutation C($input: CollectionInput!) { collectionCreate(input: $input) {
                      collection { id handle } userErrors { field message } } }""",
                  {"input": {"title": nc["title"], "handle": nc["handle"],
                             "ruleSet": {"appliedDisjunctively": False, "rules": [
                                 {"column": "VENDOR", "relation": "EQUALS", "condition": plan["vendor"]}]},
                             "templateSuffix": PRIVATE_TEMPLATE,
                             "metafields": [{"namespace": "seo", "key": "hidden", "type": "number_integer",
                                             "value": "1"}]}}, store=shop)
        node = ((cr.get("data") or {}).get("collectionCreate") or {}) if cr["ok"] else {}
        col = node.get("collection")
        e = node.get("userErrors") or []
        if not col:
            msg = cr.get("error") or "; ".join(x["message"] for x in e) or "no collection returned"
            step("Link-only collection", False, msg)
            result.update({"ok": False, "finished": _now(), "error": f"Could not create the collection: {msg}"})
            _save_run(iid, run)
            return {"ok": False, "error": result["error"], "run": run, "pre": pre}
        result["collection"] = {"id": col["id"], "url": nc["url"].rsplit("/", 1)[0] + "/" + col["handle"]}
        if plan.get("publications"):
            _gql("""mutation P($id: ID!, $in: [PublicationInput!]!) { publishablePublish(id: $id, input: $in) {
                     userErrors { message } } }""",
                 {"id": col["id"], "in": [{"publicationId": p} for p in plan["publications"]]}, store=shop)
        step("Link-only collection", True, result["collection"]["url"])
    elif plan.get("store") == "twc" and plan.get("collection"):
        result["collection"] = {"id": plan["collection"]["id"], "url": plan["collection"]["url"]}
        # collections made before 3.86 get the private template too (idempotent)
        cu = _gql("""mutation U($input: CollectionInput!) { collectionUpdate(input: $input) {
                      collection { id } userErrors { message } } }""",
                  {"input": {"id": plan["collection"]["id"], "templateSuffix": PRIVATE_TEMPLATE}}, store=shop)
        e = ((cu.get("data") or {}).get("collectionUpdate") or {}).get("userErrors") if cu["ok"] else None
        step("Collection uses the private template", cu["ok"] and not e,
             cu.get("error") or (e[0]["message"] if e else "collection.private"))

    # 1. images, colour by colour
    files, front = _upload_images(plan, step, shop)

    # 1b. colour swatches the TWC store lacks, from the S&S hex
    if plan.get("swatch_new"):
        made, failed = make_swatches(plan["swatch_new"], shop)
        plan["swatch"] = {**(plan.get("swatch") or {}), **made}
        step(f"Colour swatches: {', '.join(plan['swatch_new'])}", not failed,
             "created" if not failed else "; ".join(failed) + " — Color will be a plain dropdown")

    # 2. the product itself; if Shopify refuses the swatch links or the
    #    variant photos, make it plain rather than not at all
    linked = bool(plan["swatch"]) and all(c in plan["swatch"] for c in plan["colors"])
    r = _gql(PRODUCT_SET, {"input": product_input(plan, files, front)}, store=shop)
    node = (r.get("data") or {}).get("productSet") if r["ok"] else None
    errs = (node or {}).get("userErrors") or []
    product = (node or {}).get("product")
    if (not r["ok"] or errs or not product) and (linked or front):
        first = r.get("error") or "; ".join(e["message"] for e in errs) or "no product returned"
        r = _gql(PRODUCT_SET, {"input": product_input(plan, files, front, plain=True)}, store=shop)
        node = (r.get("data") or {}).get("productSet") if r["ok"] else None
        errs = (node or {}).get("userErrors") or []
        product = (node or {}).get("product")
        if r["ok"] and not errs and product:
            step("Swatches and variant photos skipped", False,
                 f"Shopify refused them ({first}), so Color is a plain dropdown and the variants have no "
                 "photo of their own. Set them in the admin.")
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
    bad = _keep_only((product.get("variants") or {}).get("nodes") or [], plan["keep"], shop)
    step(f"Stocked at {' + '.join(plan['keep_names'])} only", bad == 0,
         "every other location detached" if not bad else f"{bad} variant(s) could not be moved — fix in the admin")

    variant_ids = [v["id"] for v in (product.get("variants") or {}).get("nodes") or []]
    # 4. delivery profile
    if plan.get("profile"):
        dp = _gql("""mutation D($id: ID!, $p: DeliveryProfileInput!) { deliveryProfileUpdate(id: $id, profile: $p) {
                      userErrors { message } } }""",
                  {"id": plan["profile"]["id"], "p": {"variantsToAssociate": variant_ids}}, store=shop)
        e = (dp.get("data") or {}).get("deliveryProfileUpdate", {}).get("userErrors") if dp["ok"] else None
        step(f"Delivery profile: {plan['profile']['name']}", dp["ok"] and not e,
             (dp.get("error") or (e[0]["message"] if e else "assigned")))
    # 5. sales channels
    if plan.get("publications"):
        pb = _gql("""mutation P($id: ID!, $in: [PublicationInput!]!) { publishablePublish(id: $id, input: $in) {
                      userErrors { message } } }""",
                  {"id": gid, "in": [{"publicationId": p} for p in plan["publications"]]}, store=shop)
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


# ------------------------------------------------------------ add colours
# 3.87: add colours to a product the Portal already made, in place. Only new
# variants are created (productVariantsBulkCreate); existing variants, their
# prices, orders, the product number, ssorder's mapping and the share link
# are left exactly as they are. New sizes follow the product's own price
# ladder (read back from Shopify, so hand edits there win) and the cost
# follows the blank, measured from the same base as the original colours.

PRODUCT_NOW = """
query P($id: ID!) { product(id: $id) { id title handle status
  options { id name linkedMetafield { namespace key } optionValues { id name linkedMetafieldValue hasVariants } }
  variants(first: 250) { nodes { id price compareAtPrice selectedOptions { name value }
    inventoryItem { unitCost { amount } } } } } }"""

ADD_VARIANTS = """
mutation A($id: ID!, $v: [ProductVariantsBulkInput!]!, $m: [CreateMediaInput!]) {
  productVariantsBulkCreate(productId: $id, variants: $v, media: $m) {
    productVariants { id title
      inventoryItem { id inventoryLevels(first: 30) { nodes { location { id } } } } }
    userErrors { field message code } } }"""

ADD_VALUES = """
mutation O($id: ID!, $o: OptionUpdateInput!, $add: [OptionValueCreateInput!]) {
  productOptionUpdate(productId: $id, option: $o, optionValuesToAdd: $add, variantStrategy: LEAVE_AS_IS) {
    userErrors { field message code } } }"""

DROP_VALUES = """
mutation X($id: ID!, $o: OptionUpdateInput!, $del: [ID!]) {
  productOptionUpdate(productId: $id, option: $o, optionValuesToDelete: $del) {
    userErrors { field message code } } }"""


def _opt(product: dict, word: str) -> dict | None:
    for o in product.get("options") or []:
        if word in o["name"].casefold():
            return o
    return None


def ladder_from(product: dict) -> dict:
    """Read the live product's price ladder: {size: (price, compare, cost)}
    from its first colour (every colour is priced alike), plus the base."""
    copt, sopt = _opt(product, "colo"), _opt(product, "size")
    first = copt["optionValues"][0]["name"] if copt and copt["optionValues"] else None
    by_size = {}
    for v in (product.get("variants") or {}).get("nodes") or []:
        sel = {o["name"]: o["value"] for o in v["selectedOptions"]}
        if copt and first and sel.get(copt["name"]) != first:
            continue
        size = sel.get(sopt["name"]) if sopt else ""
        cost = ((v.get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
        by_size[size] = (_money(v.get("price")), _money(v.get("compareAtPrice")), _money(cost))
    return by_size


def add_preview(iid: int, form=None) -> dict:
    """What adding the ticked colours would do. Pure apart from reads."""
    out = {"ok": False, "blocking": [], "warnings": [], "add": None, "choices": [], "existing": [],
           "fingerprint": "", "product": None}
    su, err = _setup(iid, None)
    if su is None:
        out["blocking"].append(err)
        return out
    export, business, record, f, ctx, run = (su[k] for k in ("export", "business", "record", "f", "ctx", "run"))
    gid = export["item"].get("shopify_product") or (run.get("result") or {}).get("product") or ""
    shop = (run.get("result") or {}).get("store") or ("twc" if business else "ss")
    if not gid:
        out["blocking"].append("Not in Shopify yet — use Create above.")
        return out
    if ctx.get("errors"):
        out["blocking"] += ctx["errors"]
        return out
    r = _gql(PRODUCT_NOW, {"id": gid}, store=shop)
    product = (r.get("data") or {}).get("product") if r["ok"] else None
    if not r["ok"]:
        out["blocking"].append(f"Could not read the product from Shopify: {r.get('error')}")
        return out
    if product is None:
        out["blocking"].append("That product has been deleted in Shopify — create it again above.")
        return out
    out["product"] = {"id": gid, "title": product["title"], "status": product["status"], "store": shop}
    copt, sopt = _opt(product, "colo"), _opt(product, "size")
    if not copt or not sopt:
        out["blocking"].append("This product has no Color and Size options, so colours cannot be added here.")
        return out
    # a value with no variants is a leftover from a failed add: not "on" the product
    existing = [v["name"] for v in copt["optionValues"] if v.get("hasVariants", True)]
    sizes = [v["name"] for v in sopt["optionValues"]]
    have = {c.casefold() for c in existing}
    ss = export.get("ss") or {}
    out["existing"] = existing
    out["choices"] = [c["name"] for c in ss.get("colors") or [] if c["name"].casefold() not in have]
    chosen = [c for c in (form.getlist("add_colors") if form is not None else []) if c in out["choices"]]
    out["chosen"] = chosen
    if not chosen:
        return out

    ladder = ladder_from(product)
    base = [p for p in (ladder.get(s) for s in ("S", "M", "L", "XL")) if p and p[0]] or \
           [p for p in ladder.values() if p and p[0]]
    if not base:
        out["blocking"].append("Could not read the product's prices from Shopify.")
        return out
    price0 = min(p[0] for p in base)
    compare0 = min((p[1] for p in base if p[1]), default=None)
    cost0 = min((p[2] for p in base if p[2]), default=None)
    if not cost0:
        out["blocking"].append("The product's variants have no cost in Shopify, so the new colours' cost "
                               "cannot follow it. Enter the cost on the existing variants first.")
        return out
    ups = {s: round((ladder[s][0] or price0) - price0, 2) for s in sizes if s in ladder}
    # Re-plan with every colour so the blank-price base matches the original,
    # then keep only the new colours' part of it.
    f2 = dict(f, colors=existing + chosen, sizes=sizes, price=f"{price0:.2f}",
              compare_at=f"{compare0:.2f}" if compare0 else "", cost=f"{cost0:.2f}",
              up={s: f"{v:.2f}" for s, v in ups.items()}, title=product["title"])
    item2 = {k: v for k, v in export["item"].items() if k != "shopify_product"}
    ctx2 = dict(ctx, dupes=[], gone="", new_collection=None)
    pl = build_plan(dict(export, item=item2), record, ctx2, f2, {})
    new = set(chosen)
    plan = pl["plan"]
    variants = [v for v in plan["variants"] if v["color"] in new]
    for v in variants:              # a size priced by hand in Shopify keeps that price
        lp = ladder.get(v["size"])
        if lp and lp[0]:
            v["price"] = lp[0]
            v["compare_at"] = lp[1]
    out["blocking"] += [b for b in pl["blocking"] if any(c in b for c in chosen) or "token" in b
                        or "S&S style" in b or "weight" in b]
    out["warnings"] += [w for w in pl["warnings"] if any(c in w for c in chosen)]
    missing_sizes = {c: [s for s in sizes if not any(v["color"] == c and v["size"] == s for v in variants)]
                     for c in chosen}
    for c, ms in missing_sizes.items():
        if ms:
            out["warnings"].append(f"S&S has no {', '.join(ms)} in {c}, so {c} comes without "
                                   f"{'it' if len(ms) == 1 else 'them'}.")
    linked = bool(copt.get("linkedMetafield"))
    swatch = {c: g for c, g in (plan["swatch"] or {}).items() if c in new}
    swatch_new = {c: v for c, v in (plan["swatch_new"] or {}).items() if c in new}
    if linked:
        lacking = [c for c in chosen if c not in swatch and c not in swatch_new]
        if lacking:
            out["blocking"].append(f"The Color option uses swatches and there is none for {', '.join(lacking)} "
                                   "(see the note above), so it cannot be added to the swatch list.")
    total = len((product.get("variants") or {}).get("nodes") or []) + len(variants)
    if total > MAX_VARIANTS:
        out["blocking"].append(f"{total} variants — Shopify Basic allows {MAX_VARIANTS}.")
    add = {"product": gid, "store": shop, "title": product["title"], "linked": linked,
           "color_option": {"id": copt["id"], "name": copt["name"]}, "size_option": sopt["name"],
           "colors": chosen, "existing": existing, "variants": variants,
           "images": [m for m in plan["images"] if m.get("color") in new],
           "swatch": swatch, "swatch_new": swatch_new if linked else {},
           "keep": plan["keep"], "keep_names": plan["keep_names"], "profile": plan["profile"]}
    fp = {k: add[k] for k in ("product", "colors", "variants", "images", "swatch_new")}
    out.update({"add": add, "ok": not out["blocking"] and bool(variants),
                "fingerprint": hashlib.sha256(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()[:16]})
    if not variants and not out["blocking"]:
        out["blocking"].append("No sizes to add for those colours.")
    return out


def add_input(add: dict, front: dict, linked: bool, value_ids: dict | None = None) -> list[dict]:
    """value_ids: colour -> its ProductOptionValue id. A linked (swatch)
    value must be named by id: the 2025-07 API refuses linkedMetafieldValue
    alone with "id or name must be specified" (NFG tee, 2026-11-11)."""
    value_ids = value_ids or {}
    rows = []
    for v in add["variants"]:
        if value_ids.get(v["color"]):
            cv = {"optionId": add["color_option"]["id"], "id": value_ids[v["color"]]}
        elif linked:
            cv = {"optionName": add["color_option"]["name"], "linkedMetafieldValue": add["swatch"][v["color"]]}
        else:
            cv = {"optionName": add["color_option"]["name"], "name": v["color"]}
        row = {"optionValues": [cv, {"optionName": add["size_option"], "name": v["size"]}],
               "price": f"{v['price']:.2f}", "taxable": True, "inventoryPolicy": "DENY",
               "inventoryItem": {"tracked": False, "requiresShipping": True, "cost": f"{v['cost']:.2f}",
                                 "measurement": {"weight": {"value": v["weight"], "unit": "POUNDS"}}}}
        if v.get("compare_at"):
            row["compareAtPrice"] = f"{v['compare_at']:.2f}"
        if front.get(v["color"]):
            row["mediaSrc"] = [front[v["color"]]]
        rows.append(row)
    return rows


def add_commit(iid: int, form, expected: str, by: str) -> dict:
    """Add the ticked colours to the existing product."""
    pre = add_preview(iid, form)
    if not pre["ok"]:
        return {"ok": False, "error": "The preview has problems — fix them first.", "pre": pre}
    if expected != pre["fingerprint"]:
        return {"ok": False, "error": "Something changed since the preview. Look at the new preview, then add.",
                "pre": pre}
    add = pre["add"]
    shop = add["store"]
    run = load_run(iid)
    steps: list[dict] = []
    upd = {"started": _now(), "by": by, "colors": add["colors"], "steps": steps, "ok": False}
    run.setdefault("updates", []).append(upd)
    _save_run(iid, run)

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": ok, "detail": detail})
        _save_run(iid, run)
        return ok

    files, front = _upload_images(add, step, shop)
    if add.get("swatch_new"):
        made, failed = make_swatches(add["swatch_new"], shop)
        add["swatch"] = {**add["swatch"], **made}
        step(f"Colour swatches: {', '.join(add['swatch_new'])}", not failed,
             "created" if not failed else "; ".join(failed))
    linked = add["linked"] and all(c in add["swatch"] for c in add["colors"])
    if add["linked"] and not linked:
        msg = "No swatch for every new colour, and this product's Color uses swatches. Nothing was added."
        step("Add colours", False, msg)
        upd.update({"finished": _now(), "error": msg})
        _save_run(iid, run)
        return {"ok": False, "error": msg, "pre": pre}
    media = [{"originalSource": x["originalSource"], "alt": x["alt"], "mediaContentType": "IMAGE"} for x in files]

    copt_id = add["color_option"]["id"]

    def values_now() -> dict:
        """colour -> option value id, for the colours being added (matched by
        swatch for a linked Color, by name otherwise)."""
        r = _gql(PRODUCT_NOW, {"id": add["product"]}, store=shop)
        prod = (r.get("data") or {}).get("product") if r["ok"] else None
        opt = next((o for o in (prod or {}).get("options") or [] if o["id"] == copt_id), None) or {}
        ids = {}
        for c in add["colors"]:
            for ov in opt.get("optionValues") or []:
                if (linked and ov.get("linkedMetafieldValue") == add["swatch"].get(c)) or \
                        (not linked and ov["name"].casefold() == c.casefold()):
                    ids[c] = ov["id"]
        return ids

    # 1. the new colours on the option (an earlier failed try may have left them)
    ids = values_now()
    added_here = [c for c in add["colors"] if c not in ids]
    if added_here:
        o = _gql(ADD_VALUES, {"id": add["product"], "o": {"id": copt_id},
                              "add": [({"linkedMetafieldValue": add["swatch"][c]} if linked else {"name": c})
                                      for c in added_here]}, store=shop)
        e = ((o.get("data") or {}).get("productOptionUpdate") or {}).get("userErrors") if o["ok"] else None
        if not o["ok"] or e:
            msg = o.get("error") or "; ".join(x["message"] for x in e)
            step("Add colours", False, f"Shopify would not add {', '.join(added_here)} to Color: {msg}")
            upd.update({"finished": _now(), "error": msg})
            _save_run(iid, run)
            return {"ok": False, "error": msg, "pre": pre}
        ids = values_now()

    def attempt(with_photos: bool):
        r = _gql(ADD_VARIANTS, {"id": add["product"],
                                "v": add_input(add, front if with_photos else {}, linked, ids),
                                "m": media or None}, store=shop)
        node = ((r.get("data") or {}).get("productVariantsBulkCreate") or {}) if r["ok"] else {}
        e = node.get("userErrors") or []
        msg = r.get("error") or "; ".join(x["message"] for x in e)
        return node.get("productVariants") or [], msg

    created, msg = attempt(True)
    if not created and front:
        created, msg2 = attempt(False)
        if created:
            step("Variant photos skipped", False,
                 f"Shopify refused them ({msg}); the photos are on the product — assign them in the admin.")
        msg = msg2 or msg
    if not created:
        # never leave a colour button with nothing behind it on the store
        stray = [ids[c] for c in add["colors"] if c in ids]
        if stray:
            _gql(DROP_VALUES, {"id": add["product"], "o": {"id": copt_id}, "del": stray}, store=shop)
        step("Add colours", False, (msg or "Shopify created no variants.") + " — the new colour was taken off again.")
        upd.update({"finished": _now(), "error": msg or "Shopify created no variants."})
        _save_run(iid, run)
        return {"ok": False, "error": upd["error"], "pre": pre}
    step("Add colours", True, f"{', '.join(add['colors'])} · {len(created)} variants")

    bad = _keep_only(created, add["keep"], shop)
    step(f"Stocked at {' + '.join(add['keep_names'])} only", bad == 0,
         "every other location detached" if not bad else f"{bad} variant(s) could not be moved — fix in the admin")
    if add.get("profile"):
        dp = _gql("""mutation D($id: ID!, $p: DeliveryProfileInput!) { deliveryProfileUpdate(id: $id, profile: $p) {
                      userErrors { message } } }""",
                  {"id": add["profile"]["id"], "p": {"variantsToAssociate": [v["id"] for v in created]}}, store=shop)
        e = (dp.get("data") or {}).get("deliveryProfileUpdate", {}).get("userErrors") if dp["ok"] else None
        step(f"Delivery profile: {add['profile']['name']}", dp["ok"] and not e,
             dp.get("error") or (e[0]["message"] if e else "assigned"))
    upd["ok"] = True
    upd["partial"] = not all(s["ok"] for s in steps)
    upd["finished"] = _now()
    _save_run(iid, run)
    return {"ok": True, "update": upd, "pre": pre}
