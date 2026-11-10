"""The Williams Collective's Shopify store (doc 73).

Steeple & Stitch's store is the partner program: churches, schools and
non-profits, with giveback, statements and church metafields. Businesses --
NFG - National Fuel first -- are TWC customers: no payout, all made in-house,
sold from TWC's own store under TWC's name (doc 36's test: someone running
Steeple & Stitch would not need them).

Credentials live in their own file, `.env.twc`, with the same key names as
`.env` (SHOPIFY_STORE, SHOPIFY_ADMIN_TOKEN, SHOPIFY_CLIENT_ID/SECRET), so:
  * the two tokens can never be mixed up, and
  * ssorder can read TWC orders by adding a second `stores[]` entry whose
    env_file is this file -- no second copy of the token.
The token is minted by the same OAuth flow as S&S's (doc 07), against a
separate Dev Dashboard app installed on the TWC store.

TWC_HOUSE_LOCATION (in .env.twc, set from Settings) names the location TWC
ships from; with a single location it is not needed.
"""
from __future__ import annotations

import os

import requests

from . import secrets as env_secrets

PATH = env_secrets.TWC_ENV_PATH
TIMEOUT = 20

# What the TWC app asks for. Products, collections and inventory to create
# listings; locations and publications to stock and publish them; orders for
# ssorder and the Orders board; metaobjects for colour swatches.
# read_all_orders is NOT asked for: Dev Dashboard refuses it on a new app
# (2026-11-10, "Contains invalid scopes"), and asking for a scope the app does
# not declare can make the install fail. Without it Shopify returns the last
# 60 days of orders -- enough for open orders and the Orders board. Add it
# here AND in a new app version if TWC revenue history is ever pulled (doc 73).
SCOPES = ("read_orders,read_products,write_products,read_inventory,write_inventory,"
          "read_locations,read_publications,write_publications,read_metaobjects,read_files")


def values() -> dict:
    return env_secrets.read_env(PATH)


def configured() -> bool:
    v = values()
    return bool(v.get("SHOPIFY_STORE") and v.get("SHOPIFY_ADMIN_TOKEN"))


def domain() -> str:
    return values().get("SHOPIFY_STORE", "")


def house_location_name() -> str:
    return values().get("TWC_HOUSE_LOCATION", "")


def gql(query: str, variables: dict | None = None) -> dict:
    """{ok, data, error} against the TWC store. Never raises."""
    v = values()
    store, token = v.get("SHOPIFY_STORE"), v.get("SHOPIFY_ADMIN_TOKEN")
    if not store or not token:
        return {"ok": False, "error": "The TWC store is not connected yet (Settings › The Williams Collective store)."}
    version = v.get("SHOPIFY_API_VERSION") or os.environ.get("SHOPIFY_API_VERSION", "2025-07")
    try:
        r = requests.post(f"https://{store}/admin/api/{version}/graphql.json",
                          headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                          json={"query": query, "variables": variables or {}}, timeout=TIMEOUT)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "error": f"Could not reach the TWC store: {exc}"}
    if r.status_code in (401, 403):
        return {"ok": False, "scope_error": r.status_code == 403,
                "error": f"The TWC store refused the token ({r.status_code}). Reconnect it from Settings."}
    try:
        payload = r.json()
    except ValueError:
        return {"ok": False, "error": f"The TWC store returned {r.status_code} and no JSON."}
    if payload.get("errors"):
        first = payload["errors"][0]
        msg = first.get("message", str(first))
        return {"ok": False, "scope_error": "access denied" in msg.lower(), "error": msg}
    return {"ok": True, "data": payload.get("data") or {}}
