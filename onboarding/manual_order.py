"""Manual orders (Portal 3.90): an order that came in by email, text or phone
becomes a Shopify draft order, so it is tracked like any web order.

Why a Shopify draft order and not a Portal record: the draft becomes a real
order -- the Orders board, Purchasing (ssorder buys its blanks), sales tax,
revenue and the customer's history all see it with no second system to keep
in step. Shopify sends the invoice (card, or a manual method such as bank
transfer / Zelle); Larry marks bank payments paid in the admin.

A line is one of:
  product   an existing product variant in that store
  ss        a custom item on an S&S blank: style / colour / size checked
            against S&S's live catalogue (ssorder stock), carried to ssorder
            in hidden attributes _ss_style / _ss_color / _ss_size
  supplier  a custom item bought elsewhere: _supplier / _supplier_url

Custom titles end in " TWC" (the in-house rule the Orders board, ssorder and
the Products page key on). Visible attributes (Colour, Size, Decoration,
Placement) show on the invoice; hidden ones start with "_".

The Portal never completes a draft itself: "Mark as paid" / "Payment due
later" are Shopify's own buttons, and the API for completing a draft changes
between versions.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import purchasing, studio_product, twc_shop

ROOT = Path(__file__).resolve().parents[1]
RECORDS = ROOT / "cache" / "manual_orders.json"
MAX_LINES = 25
KINDS = ("product", "ss", "supplier")
SOURCES = ("email", "text", "phone", "in person", "other")
DECORATIONS = ("DTF", "Embroidery", "Patch", "Laser engraving", "Blank / none", "Other")
NEED_SCOPE = "write_draft_orders"
_lock = threading.Lock()

DRAFT_CREATE = """
mutation D($input: DraftOrderInput!) { draftOrderCreate(input: $input) {
  draftOrder { id name invoiceUrl totalPriceSet { shopMoney { amount } } }
  userErrors { field message } } }"""

INVOICE_SEND = """
mutation I($id: ID!, $email: EmailInput) { draftOrderInvoiceSend(id: $id, email: $email) {
  draftOrder { id status } userErrors { field message } } }"""

SCOPES_Q = "{ currentAppInstallation { accessScopes { handle } } }"

VARIANTS_Q = """
query V($ids: [ID!]!) { nodes(ids: $ids) { ... on ProductVariant {
  id title price product { title status } } } }"""

SEARCH_Q = """
query S($q: String!) { products(first: 15, query: $q, sortKey: TITLE) { nodes {
  title status variants(first: 100) { nodes { id title price } } } } }"""


# ------------------------------------------------------------------ helpers

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _money(text) -> float | None:
    try:
        v = round(float(str(text).replace("$", "").replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None
    return v


def _store_label(store: str) -> str:
    return "The Williams Collective" if store == "twc" else "Steeple & Stitch"


def admin_draft_url(gid: str, store: str) -> str:
    num = gid.rsplit("/", 1)[-1]
    if store == "twc":
        dom = twc_shop.domain()
    else:
        studio_product._load_env()
        import os
        dom = os.environ.get("SHOPIFY_STORE", "")
    return f"https://{dom}/admin/draft_orders/{num}" if dom else ""


def _list(form, name: str) -> list[str]:
    if form is None:
        return []
    get = getattr(form, "getlist", None)
    v = get(name) if get else form.get(name)
    if v is None:
        return []
    return [str(x) for x in (v if isinstance(v, list) else [v])]


def blank() -> dict:
    return {"store": "twc", "email": "", "first": "", "last": "", "phone": "", "company": "",
            "source": "email", "note": "", "due": "", "po": "", "delivery": "checkout", "ship_price": "",
            "lines": [{"kind": "ss", "variant": "", "title": "", "style": "", "color": "", "size": "",
                       "supplier": "", "supplier_url": "", "qty": "1", "price": "", "decoration": "DTF",
                       "placement": ""}]}


def read_form(form) -> dict:
    if form is None:
        return blank()
    g = lambda k, d="": (form.get(k) or d).strip()     # noqa: E731
    f = {"store": "ss" if g("store") == "ss" else "twc", "email": g("email"), "first": g("first"),
         "last": g("last"), "phone": g("phone"), "company": g("company"),
         "source": g("source", "email") if g("source", "email") in SOURCES else "other",
         "note": g("note")[:2000], "due": g("due")[:10], "po": g("po")[:60],
         "delivery": g("delivery", "checkout") if g("delivery") in ("checkout", "pickup", "flat") else "checkout",
         "ship_price": g("ship_price")}
    cols = {k: _list(form, f"l_{k}") for k in ("kind", "variant", "title", "style", "color", "size", "supplier",
                                                "supplier_url", "qty", "price", "decoration", "placement")}
    n = max([len(v) for v in cols.values()] + [0])
    lines = []
    for i in range(min(n, MAX_LINES)):
        row = {k: (cols[k][i].strip() if i < len(cols[k]) else "") for k in cols}
        if row["kind"] not in KINDS:
            row["kind"] = "ss"
        # a row the user left completely empty is dropped, not an error
        if not any(row[k] for k in ("variant", "title", "style", "supplier", "price")):
            continue
        lines.append(row)
    f["lines"] = lines or blank()["lines"]
    f["_empty"] = not lines
    return f


def _gql(query: str, variables: dict | None, store: str) -> dict:
    return studio_product._gql(query, variables or {}, store=store)


def scopes(store: str) -> set:
    r = _gql(SCOPES_Q, {}, store)
    if not r["ok"]:
        return set()
    return {s["handle"] for s in ((r.get("data") or {}).get("currentAppInstallation") or {}).get("accessScopes") or []}


def search_products(store: str, q: str) -> list[dict]:
    """Variants of products matching q (title), for the line picker."""
    q = " ".join(str(q or "").split())[:60]
    if len(q) < 2:
        return []
    r = _gql(SEARCH_Q, {"q": f"title:*{q}* AND status:active"}, store)
    out = []
    for p in ((r.get("data") or {}).get("products") or {}).get("nodes") or [] if r["ok"] else []:
        for v in (p.get("variants") or {}).get("nodes") or []:
            label = p["title"] if v["title"] == "Default Title" else f"{p['title']} — {v['title']}"
            out.append({"id": v["id"], "label": label, "price": v.get("price")})
    return out[:200]


# ------------------------------------------------------------------ preview

def preview(form) -> dict:
    f = read_form(form)
    out = {"ok": False, "form": f, "blocking": [], "warnings": [], "lines": [], "subtotal": 0.0,
           "shipping": None, "fingerprint": "", "store_label": _store_label(f["store"])}
    if form is None:
        return out
    b, w = out["blocking"], out["warnings"]
    store = f["store"]
    if store == "twc" and not twc_shop.configured():
        b.append("The TWC store is not connected (Settings › The Williams Collective store).")
        return out
    have = scopes(store)
    if have and NEED_SCOPE not in have:
        b.append(f"The {_store_label(store)} connection lacks {NEED_SCOPE}. Add it to that app's version in the "
                 "Dev Dashboard, release it, then Reconnect in Settings.")
    if not have:
        w.append(f"Could not read the {_store_label(store)} connection's permissions; Create may be refused.")

    if not f["email"] and not f["phone"]:
        b.append("Give the customer's email (needed to send the invoice) or at least a phone number.")
    if f["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", f["email"]):
        b.append("That email address does not look right.")
    if not f["email"]:
        w.append("No email: you can create the draft, but Shopify cannot email the invoice — "
                 "copy its payment link from the result instead.")
    if not (f["first"] or f["last"] or f["company"]):
        b.append("Give the customer's name or company.")
    if f["_empty"]:
        b.append("Add at least one item.")
    if f["due"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", f["due"]):
        b.append("Due date must be a date.")
    ship = None
    if f["delivery"] == "flat":
        ship = _money(f["ship_price"])
        if ship is None or ship < 0:
            b.append("Enter the shipping charge (0 or more).")
    out["shipping"] = 0.0 if f["delivery"] == "pickup" else ship

    # live prices for store products, one call
    vids = [l["variant"] for l in f["lines"] if l["kind"] == "product" and l["variant"]]
    found = {}
    if vids:
        r = _gql(VARIANTS_Q, {"ids": vids}, store)
        for n in ((r.get("data") or {}).get("nodes") or []) if r["ok"] else []:
            if n:
                found[n["id"]] = n
    styles_seen: dict[str, dict] = {}
    for i, l in enumerate(f["lines"], 1):
        tag = f"Line {i}"
        qty = int(l["qty"]) if l["qty"].isdigit() else 0
        if not 1 <= qty <= 999:
            b.append(f"{tag}: quantity must be 1–999.")
        row = {"n": i, "kind": l["kind"], "qty": qty}
        if l["kind"] == "product":
            v = found.get(l["variant"])
            if not v:
                b.append(f"{tag}: pick a product from the {_store_label(store)} list.")
                continue
            price = _money(v.get("price")) or 0.0
            title = v["product"]["title"] + ("" if v["title"] == "Default Title" else f" — {v['title']}")
            if (v["product"].get("status") or "") != "ACTIVE":
                w.append(f"{tag}: {title} is {v['product'].get('status', '').lower()} in Shopify.")
            row.update(title=title, price=price, variant=l["variant"], detail="store product")
        else:
            price = _money(l["price"])
            if price is None or price <= 0:
                b.append(f"{tag}: enter the price each.")
                price = 0.0
            title = l["title"].strip()
            if not title:
                b.append(f"{tag}: give the item a name the customer will see.")
            elif not title.endswith(" TWC"):
                title += " TWC"
            attrs = []
            if l["kind"] == "ss":
                style = " ".join(l["style"].split())
                if not style:
                    b.append(f"{tag}: give the S&S style (e.g. Gildan 64000).")
                else:
                    if style not in styles_seen:
                        styles_seen[style] = purchasing.style_options(style)
                    opt = styles_seen[style]
                    if not opt.get("ok"):
                        b.append(f"{tag}: S&S does not know style “{style}” ({opt.get('error') or 'no answer'}).")
                    else:
                        if l["color"] not in opt["colors"]:
                            b.append(f"{tag}: “{l['color'] or '(no colour)'}” is not an S&S colour of {style}.")
                        if l["size"] not in opt["sizes"]:
                            b.append(f"{tag}: “{l['size'] or '(no size)'}” is not an S&S size of {style}.")
                attrs = [("Colour", l["color"]), ("Size", l["size"]), ("_ss_style", style),
                         ("_ss_color", l["color"]), ("_ss_size", l["size"])]
                detail = f"S&S {style} · {l['color']} · {l['size']}"
            else:
                if not l["supplier"]:
                    b.append(f"{tag}: name the supplier it is bought from.")
                attrs = [("Colour", l["color"]), ("Size", l["size"]), ("_supplier", l["supplier"]),
                         ("_supplier_url", l["supplier_url"])]
                detail = f"from {l['supplier'] or '?'}"
            attrs += [("Decoration", l["decoration"]), ("Placement", l["placement"]), ("_source", f["source"])]
            row.update(title=title, price=price, detail=detail,
                       attrs=[{"key": k, "value": v} for k, v in attrs if v])
        row["total"] = round(row["price"] * qty, 2)
        out["lines"].append(row)
    out["subtotal"] = round(sum(r["total"] for r in out["lines"]), 2)
    out["ok"] = not b
    fp = {k: f[k] for k in ("store", "email", "first", "last", "phone", "company", "source", "note", "due",
                            "po", "delivery", "ship_price")}
    fp["lines"] = out["lines"]
    out["fingerprint"] = hashlib.sha256(json.dumps(fp, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return out


def draft_input(pre: dict) -> dict:
    f = pre["form"]
    lines = []
    for r in pre["lines"]:
        if r["kind"] == "product":
            lines.append({"variantId": r["variant"], "quantity": r["qty"]})
        else:
            lines.append({"title": r["title"], "quantity": r["qty"], "taxable": True, "requiresShipping": True,
                          "originalUnitPriceWithCurrency": {"amount": f"{r['price']:.2f}", "currencyCode": "USD"},
                          "customAttributes": r["attrs"]})
    note = " · ".join(x for x in (f"Manual order ({f['source']})", f"Due {f['due']}" if f["due"] else "",
                                  f["note"]) if x)
    name = " ".join(x for x in (f["first"], f["last"]) if x)
    inp = {"lineItems": lines, "note": note, "tags": ["manual-order", f"source:{f['source']}"],
           "customAttributes": [a for a in ({"key": "Due date", "value": f["due"]} if f["due"] else None,
                                            {"key": "Customer", "value": name} if name else None,
                                            {"key": "Company", "value": f["company"]} if f["company"] else None)
                                if a]}
    if f["email"]:
        inp["email"] = f["email"]
    if f["phone"]:
        inp["phone"] = f["phone"]
    if f["po"]:
        inp["poNumber"] = f["po"]
    if f["first"] or f["last"] or f["company"]:
        addr = {k: v for k, v in (("firstName", f["first"]), ("lastName", f["last"]),
                                  ("company", f["company"]), ("phone", f["phone"])) if v}
        inp["billingAddress"] = addr
    if f["delivery"] == "pickup":
        inp["shippingLine"] = {"title": "Pickup — 5351 Logan Ave, Dayton",
                               "priceWithCurrency": {"amount": "0.00", "currencyCode": "USD"}}
    elif f["delivery"] == "flat":
        inp["shippingLine"] = {"title": "Shipping",
                               "priceWithCurrency": {"amount": f"{pre['shipping']:.2f}", "currencyCode": "USD"}}
    return inp


# ------------------------------------------------------------------ records

def records() -> list[dict]:
    try:
        return json.loads(RECORDS.read_text()) if RECORDS.exists() else []
    except (ValueError, OSError):
        return []


def _save(rows: list[dict]) -> None:
    RECORDS.parent.mkdir(parents=True, exist_ok=True)
    tmp = RECORDS.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows[-200:], indent=1))
    tmp.replace(RECORDS)


def find(draft_id: str) -> dict | None:
    return next((r for r in records() if r.get("id") == draft_id), None)


# ------------------------------------------------------------------ writes

def create(form, expected: str, by: str) -> dict:
    pre = preview(form)
    if not pre["ok"]:
        return {"ok": False, "error": "The preview has problems — fix them first.", "pre": pre}
    if expected != pre["fingerprint"]:
        return {"ok": False, "error": "Something changed since the preview. Look at the new preview, then create.",
                "pre": pre}
    store = pre["form"]["store"]
    r = _gql(DRAFT_CREATE, {"input": draft_input(pre)}, store)
    node = ((r.get("data") or {}).get("draftOrderCreate") or {}) if r["ok"] else {}
    errs = node.get("userErrors") or []
    d = node.get("draftOrder")
    if not d:
        msg = r.get("error") or "; ".join(e["message"] for e in errs) or "Shopify returned no draft order."
        return {"ok": False, "error": f"Shopify refused the draft order: {msg}", "pre": pre}
    f = pre["form"]
    rec = {"id": d["id"], "name": d["name"], "store": store, "invoice_url": d.get("invoiceUrl") or "",
           "total": _money(((d.get("totalPriceSet") or {}).get("shopMoney") or {}).get("amount")),
           "customer": " ".join(x for x in (f["first"], f["last"]) if x) or f["company"],
           "company": f["company"], "email": f["email"], "source": f["source"], "due": f["due"],
           "items": sum(l["qty"] for l in pre["lines"]), "created": _now(), "by": by,
           "invoice_sent": "", "admin_url": admin_draft_url(d["id"], store)}
    with _lock:
        rows = records()
        rows.append(rec)
        _save(rows)
    return {"ok": True, "record": rec, "pre": pre}


def send_invoice(draft_id: str, message: str, by: str) -> dict:
    rec = find(draft_id)
    if not rec:
        return {"ok": False, "error": "That draft order is not one the Portal made."}
    if not rec.get("email"):
        return {"ok": False, "error": "This draft has no customer email — copy the payment link instead."}
    email = {"to": rec["email"], "subject": f"Invoice {rec['name']} from {_store_label(rec['store'])}"}
    if message.strip():
        email["customMessage"] = message.strip()[:1000]
    r = _gql(INVOICE_SEND, {"id": draft_id, "email": email}, rec["store"])
    node = ((r.get("data") or {}).get("draftOrderInvoiceSend") or {}) if r["ok"] else {}
    errs = node.get("userErrors") or []
    if not r["ok"] or errs or not node.get("draftOrder"):
        msg = r.get("error") or "; ".join(e["message"] for e in errs) or "no answer"
        return {"ok": False, "error": f"Shopify did not send the invoice: {msg}"}
    with _lock:
        rows = records()
        for row in rows:
            if row.get("id") == draft_id:
                row["invoice_sent"] = _now()
                row["invoice_by"] = by
        _save(rows)
    return {"ok": True, "record": find(draft_id)}
