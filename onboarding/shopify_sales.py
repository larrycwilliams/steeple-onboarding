"""Sales, cost and payout figures pulled from Shopify.

Two decisions shape everything in this module.

**Sales are computed from orders, not from the analytics API.** ShopifyQL is
easier to call, but it hands back a rollup: no line items, no per-order dates
you control, and no way to see which lines had no cost recorded. Payouts are
money owed to a partner, so the arithmetic has to be inspectable line by line
-- and quarter boundaries have to be ours, not whatever the report defaults to.

**A line item is credited by the product's CURRENT vendor, not the vendor the
line recorded at the time of sale.** Those differ. Order #1001 sold a GCS
Tultex tee whose line item says vendor "Steeple & Stitch Co.", because the POD
app had not been corrected yet; the product's vendor says "Germantown Christian
School" today. The tee was always GCS's. Crediting the frozen line value would
pay the wrong party and would disagree with the numbers Shopify's own reports
show, which is the faster way to lose trust in a dashboard.

Everything degrades: no token means the cached snapshot is used, and a stale
cache says so rather than pretending to be live.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .shopify_pull import _load_env, configured  # noqa: F401  (re-exported)

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "shopify_sales.json"
TIMEOUT = 30
PAGE = 50

# read_orders + read_products + read_inventory. Inventory is the one people
# forget, and without it every unitCost comes back null and every margin
# silently becomes "no cost recorded".
SCOPES = "read_orders, read_products, read_inventory"

# Newer stores have no legacy custom apps, so there is no static token to paste:
# the app performs an OAuth install and writes the token itself. Telling someone
# to add SHOPIFY_ADMIN_TOKEN by hand sends them looking for a value their store
# will never show them.
CONNECT_HINT = "Connect the app from Settings → Shopify connection."

ORDERS_QUERY = """
query Orders($first: Int!, $after: String, $q: String) {
  orders(first: $first, after: $after, query: $q, sortKey: CREATED_AT) {
    edges {
      node {
        name
        createdAt
        test
        cancelledAt
        displayFinancialStatus
        lineItems(first: 100) {
          edges {
            node {
              title
              quantity
              currentQuantity
              vendor
              discountedUnitPriceAfterAllDiscountsSet { shopMoney { amount } }
              product { id title status vendor }
              variant { id inventoryItem { unitCost { amount } } }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

CATALOG_QUERY = """
query Catalog($first: Int!, $after: String) {
  products(first: $first, after: $after, sortKey: VENDOR) {
    edges {
      node {
        id
        title
        vendor
        status
        variants(first: 100) {
          edges { node { inventoryItem { unitCost { amount } } } }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

COLLECTIONS_QUERY = """
query Collections($first: Int!) {
  collections(first: $first) {
    edges {
      node {
        title
        handle
        productsCount { count }
        ruleSet { rules { column relation condition } }
      }
    }
  }
}
"""


# ------------------------------------------------------------------ helpers --

def _endpoint() -> str:
    store = os.environ["SHOPIFY_STORE"]
    version = os.environ.get("SHOPIFY_API_VERSION", "2025-07")
    return f"https://{store}/admin/api/{version}/graphql.json"


def _call(query: str, variables: dict) -> dict:
    response = requests.post(
        _endpoint(),
        headers={
            "X-Shopify-Access-Token": os.environ["SHOPIFY_ADMIN_TOKEN"],
            "Content-Type": "application/json",
        },
        json={"query": query, "variables": variables},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"][:1]))
    return payload["data"]


def _money(node: dict | None) -> float | None:
    """A MoneyBag (has shopMoney), or None. None is not zero."""
    if not node:
        return None
    return _amount((node or {}).get("shopMoney"))


def _amount(node: dict | None) -> float | None:
    """A MoneyV2 — `amount` sits directly on it, with no shopMoney wrapper.

    Worth its own function because getting this wrong is silent: reading
    unitCost as though it were a MoneyBag returns None for every product, and
    the dashboard then reports the entire store as having no cost recorded
    rather than raising anything.
    """
    if not node:
        return None
    amount = node.get("amount")
    if amount in (None, ""):
        return None
    try:
        return float(amount)
    except (TypeError, ValueError):
        return None


def _paginate(query: str, root: str, variables: dict) -> list[dict]:
    nodes: list[dict] = []
    after = None
    while True:
        data = _call(query, {**variables, "first": PAGE, "after": after})
        block = data[root]
        nodes.extend(edge["node"] for edge in block["edges"])
        page = block["pageInfo"]
        if not page["hasNextPage"]:
            return nodes
        after = page["endCursor"]


# ------------------------------------------------------------------- fetch --

def fetch(since: str = "2020-01-01") -> dict:
    """Pull orders, catalog and collections. Returns a cacheable snapshot."""
    if not configured():
        raise RuntimeError(
            f"Not connected to Shopify. {CONNECT_HINT} "
            f"The app requests scopes: {SCOPES}"
        )

    orders = _paginate(ORDERS_QUERY, "orders", {"q": f"created_at:>={since}"})
    catalog = _paginate(CATALOG_QUERY, "products", {})
    collections = _call(COLLECTIONS_QUERY, {"first": 50})["collections"]
    collections = [edge["node"] for edge in collections["edges"]]

    return {
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": since,
        "orders": [_clean_order(o) for o in orders],
        "catalog": [_clean_product(p) for p in catalog],
        "collections": collections,
    }


def _clean_order(node: dict) -> dict:
    lines = []
    for edge in node["lineItems"]["edges"]:
        line = edge["node"]
        product = line.get("product") or {}
        variant = line.get("variant") or {}
        inventory = (variant or {}).get("inventoryItem") or {}
        lines.append({
            "title": line["title"],
            # currentQuantity is quantity minus anything refunded or removed.
            # quantity alone would keep paying a partner for returned goods.
            "qty": line.get("currentQuantity", line["quantity"]) or 0,
            "qty_ordered": line["quantity"],
            "product_id": product.get("id") or "",
            # A deleted variant and a variant with no cost entered look
            # identical downstream -- both give None -- but only one of them
            # can be fixed by typing a cost into Shopify. Record which.
            "variant_missing": line.get("variant") is None,
            "vendor_at_sale": line.get("vendor") or "",
            "vendor": product.get("vendor") or line.get("vendor") or "",
            "product_status": product.get("status") or "",
            # ...AfterAllDiscounts, not discountedUnitPriceSet: the latter
            # excludes order-level and code-based discounts, so a store-wide
            # promo would be paid out to the partner as though it never
            # happened. This is the number actually collected.
            "unit_price": _money(line.get("discountedUnitPriceAfterAllDiscountsSet")),
            "unit_cost": _amount(inventory.get("unitCost")),
        })
    return {
        "name": node["name"],
        "created_at": node["createdAt"],
        "test": bool(node.get("test")),
        "cancelled": bool(node.get("cancelledAt")),
        "status": node.get("displayFinancialStatus") or "",
        "lines": lines,
    }


def _clean_product(node: dict) -> dict:
    edges = (node.get("variants") or {}).get("edges") or []
    costs = []
    for edge in edges:
        value = _amount(((edge["node"].get("inventoryItem")) or {}).get("unitCost"))
        if value is not None:
            costs.append(value)
    return {
        "id": node["id"],
        "title": node["title"],
        "vendor": node.get("vendor") or "",
        "status": node.get("status") or "",
        # First costed variant, for the catalog panel's "has a cost" check.
        "unit_cost": costs[0] if costs else None,
        # Every costed variant, so a line whose own variant has been deleted
        # can be estimated from its siblings instead of vanishing.
        "unit_costs": costs,
    }


# ------------------------------------------------------------------- cache --

def save_cache(snapshot: dict) -> Path:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(snapshot, indent=1))
    return CACHE


def load_cache() -> dict | None:
    if not CACHE.exists():
        return None
    try:
        return json.loads(CACHE.read_text())
    except (ValueError, OSError):
        return None


def snapshot(refresh: bool = False) -> tuple[dict | None, str]:
    """(snapshot, source). source is 'live', 'cache' or an error message.

    Refresh never destroys a good cache: if the pull fails, the previous
    snapshot is still returned, with the failure reported alongside it. A
    dashboard that goes blank because the network blipped is worse than one
    showing this morning's numbers and saying so.
    """
    cached = load_cache()
    if not refresh and cached:
        return cached, "cache"
    if not configured():
        if cached:
            return cached, "cache — not connected to Shopify yet"
        return None, (
            f"No Shopify connection and no cached data. {CONNECT_HINT}"
        )
    try:
        fresh = fetch()
        save_cache(fresh)
        return fresh, "live"
    except Exception as exc:
        if cached:
            return cached, f"cache — refresh failed: {exc}"
        return None, str(exc)
