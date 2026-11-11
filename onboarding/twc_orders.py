"""The Williams Collective's own Shopify orders, for the Orders board (3.90).

Until 3.90 the board only knew a TWC order through ssorder's Purchasing
proposal, so it could not say whether the order was paid, shipped or picked
up, and a TWC order ssorder had not listed (no stores[] entry, or nothing to
buy) never reached the board at all. This pulls the TWC store's orders the
same way the Dashboard pulls Steeple & Stitch's: one writer (the background
refresh), one cache file, pages read the file only (doc 69 rule).

Read-only: read_orders on the TWC connection (.env.twc). Without
read_all_orders Shopify returns the last 60 days -- enough for open orders.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import shopify_sales, twc_shop

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "twc_orders.json"
DAYS = 60
PAGE = 50


def _query() -> str:
    # The Dashboard's order query, so _clean_order reads both stores alike.
    return shopify_sales.ORDERS_QUERY


def fetch(days: int = DAYS) -> dict:
    """{orders, locations, pulled_at} from the TWC store. Raises on failure."""
    if not twc_shop.configured():
        raise RuntimeError("The TWC store is not connected (Settings › The Williams Collective store).")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    nodes, after = [], None
    while True:
        r = twc_shop.gql(_query(), {"first": PAGE, "after": after, "q": f"created_at:>={since}"})
        if not r["ok"]:
            raise RuntimeError(r.get("error") or "TWC order pull failed")
        block = r["data"]["orders"]
        nodes.extend(e["node"] for e in block["edges"])
        if not block["pageInfo"]["hasNextPage"]:
            break
        after = block["pageInfo"]["endCursor"]
    loc = twc_shop.gql("{ locations(first: 50) { nodes { name } } }")
    locations = [n["name"] for n in ((loc.get("data") or {}).get("locations") or {}).get("nodes") or []] \
        if loc["ok"] else []
    return {"pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "since": since,
            "orders": [shopify_sales._clean_order(n) for n in nodes], "locations": locations,
            "domain": twc_shop.domain()}


def load() -> dict | None:
    if not CACHE.exists():
        return None
    try:
        return json.loads(CACHE.read_text())
    except (ValueError, OSError):
        return None


def refresh() -> tuple[bool, str]:
    """Pull and save. A failed pull keeps the last good file (never blanks the board)."""
    if not twc_shop.configured():
        return True, "TWC store not connected — skipped"
    try:
        snap = fetch()
    except Exception as exc:                                  # noqa: BLE001
        return False, str(exc)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(snap, indent=1))
    tmp.replace(CACHE)
    return True, f"{len(snap['orders'])} orders"
