"""Create a partner's Shopify storefront: smart collection, publications, redirect.

This is stage 10 of `claude/ops/17-lead-to-live-store.md`, and it is the most
expensive stage in the path to get wrong. A collection created without a
`VENDOR EQUALS` rule falls back to guessing the owner from the organisation
name; "Germantown Christian School" and "GCS Athletics" guess to nearly the
same string, and $2,973.76 of one partner's sales landed on the other.

`collectionCreate` accepts the ruleSet inline, so a collection created here is
never ruleless -- there is no window for the guess to happen. That is the whole
point of automating this rather than doing it by hand.

Three calls, in this order:

    1. collectionCreate   title, handle, VENDOR EQUALS <vendor>, template
    2. publishablePublish onto the sales channels
    3. urlRedirectCreate  /go/<slug> -> /collections/<handle>

The redirect is last because it is the only one that points at something: a
redirect created before the collection exists sends people to a 404, and QR
codes printed from it are permanent.

SCOPES. This module WRITES. The app's original token was read-only
(`read_orders,read_products,read_inventory,read_customers`), so every call here
returns 403 until the app is reconnected from Settings with the write scopes
added. `preflight()` reports that as a blocking problem rather than letting the
first write fail with something that reads like a bad store domain.
"""

from __future__ import annotations

import os
import re

import requests

from onboarding import store
from onboarding.shopify_pull import _endpoint, _load_env, configured

TIMEOUT = 30

# Sales channels a partner collection is published to. Read live rather than
# hardcoded -- publication GIDs differ per store and per installed channel.
WANTED_PUBLICATIONS = ["Online Store", "Shop", "Point of Sale", "Facebook & Instagram", "Google & YouTube"]


# --------------------------------------------------------------- transport

def _gql(query: str, variables: dict | None = None) -> dict:
    """Return {ok, data, error}. Never raises -- callers report, not crash."""
    if not configured():
        return {"ok": False, "error": "Shopify credentials are not set. Settings > Shopify."}
    try:
        response = requests.post(
            _endpoint(),
            headers={
                "X-Shopify-Access-Token": os.environ["SHOPIFY_ADMIN_TOKEN"],
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": variables or {}},
            timeout=TIMEOUT,
        )
    except Exception as exc:
        return {"ok": False, "error": f"Could not reach Shopify: {exc}"}

    if response.status_code == 403:
        return {"ok": False, "scope_error": True, "error":
                "Shopify refused the write (403). The token is read-only — "
                "reconnect the app from Settings to grant the write scopes."}
    if response.status_code == 401:
        return {"ok": False, "error": "Shopify rejected the token (401). Reconnect from Settings."}
    try:
        payload = response.json()
    except ValueError:
        return {"ok": False, "error": f"Shopify returned {response.status_code} and no JSON."}

    if payload.get("errors"):
        first = payload["errors"][0]
        message = first.get("message", str(first))
        # An access-scope failure arrives as a GraphQL error, not an HTTP code.
        scope = "access denied" in message.lower() or "scope" in message.lower()
        return {"ok": False, "scope_error": scope, "error": message}

    return {"ok": True, "data": payload.get("data") or {}}


def _user_errors(node: dict | None) -> str:
    if not node:
        return "Shopify returned nothing for that call."
    errors = node.get("userErrors") or []
    if not errors:
        return ""
    return "; ".join(f"{'/'.join(e.get('field') or [])}: {e['message']}".lstrip(": ")
                     for e in errors)


# --------------------------------------------------------------- preflight

HANDLE_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Two queries, not one. Combining them meant a missing `read_publications`
# scope failed the whole request and took the collection lookup down with it --
# so an optional feature (publishing) blocked a required one (creating). One
# query per concern: a partial permission now degrades to "created hidden"
# instead of blinding the preflight.
COLLECTION_QUERY = """
query($handle: String!) {
  collectionByHandle(handle: $handle) {
    id title handle
    ruleSet { appliedDisjunctively rules { column relation condition } }
  }
}
"""

PUBLICATIONS_QUERY = """
query { publications(first: 25) { nodes { id name } } }
"""

VENDOR_COUNT_QUERY = """
query($q: String!) { products(first: 1, query: $q) { nodes { id } } }
"""

# The logo the partner attached to the discovery form is already a MediaImage
# in Shopify Files, so the collection image is a URL reference -- no re-upload,
# no staged upload, no write_files scope. Reading it needs `read_files`.
MEDIA_IMAGE_QUERY = """
query($id: ID!) {
  node(id: $id) { ... on MediaImage { id status image { url width height } } }
}
"""


def logo_url(record: dict) -> tuple[str, str]:
    """(url, note) for the partner's form-uploaded logo. Never raises.

    Returns ("", reason) when there is nothing usable -- the collection is
    still worth creating without an image.
    """
    ref = (record.get("logo_ref") or "").strip()
    if not ref:
        return "", "no logo on the record"
    result = _gql(MEDIA_IMAGE_QUERY, {"id": ref})
    if not result["ok"]:
        return "", result["error"]
    node = result["data"].get("node") or {}
    if node.get("status") not in (None, "READY"):
        return "", f"logo is still processing ({node.get('status')})"
    url = ((node.get("image") or {}).get("url") or "").strip()
    return (url, "") if url else ("", "logo reference resolved to no image")


def preflight(record: dict) -> dict:
    """Everything that must be true before writing, gathered in one pass.

    Returns {ok, blocking[], warnings[], plan{}, existing, publications[]}.
    Nothing here writes.
    """
    blocking: list[str] = []
    warnings: list[str] = []

    _load_env()
    if not configured():
        blocking.append("Shopify is not connected. Settings > Shopify.")

    vendor = (record.get("vendor") or "").strip()
    handle = (record.get("collection_handle") or "").strip().lower()
    slug = (record.get("redirect_slug") or "").strip().lower()
    title = (record.get("store_name") or record.get("org_name") or "").strip()

    if not vendor:
        blocking.append(
            "Shopify vendor string is empty. This is what the collection rule "
            "matches on — without it the collection would catch nothing.")
    if not handle:
        blocking.append("Collection handle is empty.")
    elif not HANDLE_RE.match(handle):
        blocking.append(
            f"Collection handle “{handle}” is not a valid slug — lowercase "
            "letters, numbers and single hyphens only.")
    if not title:
        blocking.append("No store name or organization name to title the collection with.")
    if not slug:
        warnings.append("No redirect slug — the /go/ redirect will be skipped.")
    elif not HANDLE_RE.match(slug):
        blocking.append(f"Redirect slug “{slug}” is not a valid slug.")

    existing = None
    publications: list[dict] = []
    if not blocking:
        # Required: does this handle already exist, and with what rule?
        result = _gql(COLLECTION_QUERY, {"handle": handle})
        if not result["ok"]:
            blocking.append(result["error"])
        else:
            existing = result["data"].get("collectionByHandle")

            # Optional: which sales channels can we publish to? A failure here
            # is a warning -- the collection still gets created, just hidden.
            pubs = _gql(PUBLICATIONS_QUERY)
            if not pubs["ok"]:
                warnings.append(
                    f"Sales channels could not be read ({pubs['error']}) — the "
                    "collection will be created hidden. Publish it by hand, or "
                    "reconnect Shopify to grant the publication scopes.")
            else:
                publications = [
                    p for p in (pubs["data"].get("publications") or {}).get("nodes", [])
                    if p["name"] in WANTED_PUBLICATIONS
                ]
                if not publications:
                    warnings.append(
                        "No matching sales channels found — the collection will be "
                        "created unpublished.")

            if existing:
                rules = (existing.get("ruleSet") or {}).get("rules") or []
                vendor_rule = next(
                    (r for r in rules
                     if r["column"] == "VENDOR" and r["relation"] == "EQUALS"), None)
                if vendor_rule and vendor_rule["condition"] == vendor:
                    blocking.append(
                        f"Collection “{handle}” already exists with the correct "
                        f"vendor rule. Nothing to do.")
                elif vendor_rule:
                    blocking.append(
                        f"Collection “{handle}” already exists with a DIFFERENT "
                        f"vendor rule ({vendor_rule['condition']!r}). Fix it in "
                        "Shopify rather than creating a second collection.")
                else:
                    blocking.append(
                        f"Collection “{handle}” already exists and has no vendor "
                        "rule. It needs the rule adding, not a new collection — "
                        "that is a collectionUpdate, not a create.")

            # A vendor string that matches no product is not fatal (a new
            # partner has none yet), but if the partner is meant to be live it
            # is almost always a typo in the vendor field.
            # Also optional: a failure here just means we cannot offer the hint.
            probe = _gql(VENDOR_COUNT_QUERY, {"q": f'vendor:"{vendor}"'})
            if probe["ok"] and not (probe["data"].get("products") or {}).get("nodes"):
                warnings.append(
                    f"No products currently carry vendor “{vendor}”. Fine for a new "
                    "partner; a typo if this partner already has products.")

    return {
        "ok": not blocking,
        "blocking": blocking,
        "warnings": warnings,
        "existing": existing,
        "publications": publications,
        "plan": {
            "title": title,
            "handle": handle,
            "vendor": vendor,
            "slug": slug,
            "redirect_from": f"/go/{slug}" if slug else "",
            "redirect_to": f"/collections/{handle}" if handle else "",
            "logo": "the partner's own logo" if (record.get("logo_ref") or "").strip()
                    else "none on record",
            "template_suffix": (record.get("collection_template") or "").strip(),
            "channels": [p["name"] for p in publications],
        },
    }


# ------------------------------------------------------------------ writes

COLLECTION_CREATE = """
mutation($input: CollectionInput!) {
  collectionCreate(input: $input) {
    collection { id title handle
      ruleSet { rules { column relation condition } } }
    userErrors { field message }
  }
}
"""

PUBLISH = """
mutation($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    publishable { availablePublicationsCount { count } }
    userErrors { field message }
  }
}
"""

REDIRECT_CREATE = """
mutation($redirect: UrlRedirectInput!) {
  urlRedirectCreate(urlRedirect: $redirect) {
    urlRedirect { id path target }
    userErrors { field message }
  }
}
"""


def create(record: dict, publish: bool = True, redirect: bool = True) -> dict:
    """Create the collection, publish it, add the redirect. Writes.

    Returns {ok, steps[], collection_id, error}. Each step is
    {name, ok, detail} so a partial success is legible rather than a single
    pass/fail on three different operations.
    """
    check = preflight(record)
    if not check["ok"]:
        return {"ok": False, "error": "; ".join(check["blocking"]), "steps": []}

    plan = check["plan"]
    steps: list[dict] = []

    payload = {
        "title": plan["title"],
        "handle": plan["handle"],
        "ruleSet": {
            "appliedDisjunctively": False,
            "rules": [{"column": "VENDOR", "relation": "EQUALS",
                       "condition": plan["vendor"]}],
        },
        "sortOrder": "BEST_SELLING",
    }
    if plan["template_suffix"]:
        payload["templateSuffix"] = plan["template_suffix"]
    blurb = (record.get("store_blurb") or "").strip()
    if blurb:
        payload["descriptionHtml"] = f"<p>{blurb}</p>"

    image_url, image_note = logo_url(record)
    if image_url:
        payload["image"] = {"src": image_url,
                            "altText": f"{plan['title']} logo"}

    result = _gql(COLLECTION_CREATE, {"input": payload})
    if not result["ok"]:
        return {"ok": False, "error": result["error"],
                "scope_error": result.get("scope_error"), "steps": steps}
    node = result["data"].get("collectionCreate")
    problem = _user_errors(node)
    if problem:
        return {"ok": False, "error": problem, "steps": steps}

    collection = node["collection"]
    rule = (collection.get("ruleSet") or {}).get("rules", [{}])[0]
    steps.append({
        "name": "Collection created", "ok": True,
        "detail": f"{collection['title']} ({collection['handle']}) — "
                  f"{rule.get('column')} {rule.get('relation')} "
                  f"“{rule.get('condition')}”",
    })
    # A missing image never fails the run -- it is reported so it can be fixed,
    # not treated as a reason the storefront does not exist.
    steps.append({
        "name": "Collection image", "ok": bool(image_url),
        "detail": "the partner's own logo" if image_url
                  else f"none — {image_note}",
    })

    if not publish:
        steps.append({"name": "Publish to sales channels", "ok": True,
                      "detail": "skipped — collection created hidden"})
    if publish and check["publications"]:
        pub_result = _gql(PUBLISH, {
            "id": collection["id"],
            "input": [{"publicationId": p["id"]} for p in check["publications"]],
        })
        if not pub_result["ok"]:
            steps.append({"name": "Publish to sales channels", "ok": False,
                          "detail": pub_result["error"]})
        else:
            problem = _user_errors(pub_result["data"].get("publishablePublish"))
            steps.append({
                "name": "Publish to sales channels", "ok": not problem,
                "detail": problem or ", ".join(plan["channels"]),
            })

    if redirect and plan["slug"]:
        red_result = _gql(REDIRECT_CREATE, {"redirect": {
            "path": plan["redirect_from"], "target": plan["redirect_to"]}})
        if not red_result["ok"]:
            steps.append({"name": "QR redirect", "ok": False, "detail": red_result["error"]})
        else:
            problem = _user_errors(red_result["data"].get("urlRedirectCreate"))
            steps.append({
                "name": "QR redirect", "ok": not problem,
                "detail": problem or f"{plan['redirect_from']} → {plan['redirect_to']}",
            })

    # Write the real GID back so the store link stops being a typed string and
    # anything downstream can address the collection directly.
    record["collection_gid"] = collection["id"]
    store.save(record)
    steps.append({"name": "Partner record updated", "ok": True,
                  "detail": collection["id"]})

    return {"ok": True, "collection_id": collection["id"], "steps": steps}


# ------------------------------------------------------------------ lookup

def vendor_map() -> list[dict]:
    """Partner org keys and their confirmed vendor strings, for the traveler.

    Reads the partner records so there is one confirmed vendor string per
    partner rather than a second hand-maintained list. Partners with no vendor
    set are skipped -- an unconfirmed guess is what this whole module exists to
    prevent.
    """
    rows = []
    for record in store.list_partners():
        vendor = (record.get("vendor") or "").strip()
        if not vendor:
            continue
        rows.append({
            "key": (record.get("collection_handle") or store.partner_id(record)).strip(),
            "vendor": vendor,
            "collection": (record.get("collection_handle") or "").strip(),
            "org_name": record.get("org_name", ""),
        })
    rows.sort(key=lambda r: r["vendor"].lower())
    return rows
