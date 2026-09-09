"""Optional: pull store facts from Shopify so they don't get typed twice.

Configure in .env (never commit it):
    SHOPIFY_STORE=xetewp-yj.myshopify.com    # the generated handle,
                                             # not steepleandstitch.*
    SHOPIFY_ADMIN_TOKEN=shpat_...
    SHOPIFY_API_VERSION=2025-07

Everything here degrades gracefully: if the token is missing or the call
fails, the app just leaves the fields for manual entry.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 20

COLLECTION_QUERY = """
query($handle: String!) {
  collectionByHandle(handle: $handle) {
    id
    title
    handle
    descriptionHtml
    products(first: 40) {
      nodes {
        title
        status
        productType
        options { name values }
      }
    }
  }
}
"""


def _load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def configured() -> bool:
    _load_env()
    return bool(os.environ.get("SHOPIFY_STORE") and os.environ.get("SHOPIFY_ADMIN_TOKEN"))


def _endpoint() -> str:
    store = os.environ["SHOPIFY_STORE"]
    version = os.environ.get("SHOPIFY_API_VERSION", "2025-07")
    return f"https://{store}/admin/api/{version}/graphql.json"


def fetch_collection(handle: str) -> dict:
    """Return {ok, title, products, size_values, error}."""
    if not configured():
        return {"ok": False, "error": "Shopify credentials not configured in .env"}
    try:
        response = requests.post(
            _endpoint(),
            headers={
                "X-Shopify-Access-Token": os.environ["SHOPIFY_ADMIN_TOKEN"],
                "Content-Type": "application/json",
            },
            json={"query": COLLECTION_QUERY, "variables": {"handle": handle}},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # network, auth, JSON — all non-fatal
        return {"ok": False, "error": str(exc)}

    if payload.get("errors"):
        return {"ok": False, "error": str(payload["errors"][:1])}

    collection = (payload.get("data") or {}).get("collectionByHandle")
    if not collection:
        return {"ok": False, "error": f"No collection found with handle '{handle}'"}

    products = [
        node for node in collection["products"]["nodes"]
        if node.get("status") == "ACTIVE"
    ]
    titles = [p["title"] for p in products]

    sizes: list[str] = []
    for product in products:
        for option in product.get("options") or []:
            if option["name"].strip().lower() == "size":
                for value in option["values"]:
                    if value not in sizes:
                        sizes.append(value)

    return {
        "ok": True,
        "title": collection["title"],
        "handle": collection["handle"],
        "products": titles,
        "product_count": len(titles),
        "size_values": sizes,
        "suggested_lineup": _lineup_sentence(titles),
        "suggested_sizes": _size_sentence(sizes),
    }


GARMENT_WORDS = [
    ("long sleeve", "performance long sleeve"),
    ("hoodie", "pullover hoodie"),
    ("crewneck", "crewneck sweatshirt"),
    ("sweatshirt", "crewneck sweatshirt"),
    ("hat", "embroidered cap"),
    ("cap", "embroidered cap"),
    ("beanie", "beanie"),
    ("tee", "crew tee"),
    ("t-shirt", "crew tee"),
    ("shirt", "crew tee"),
    ("tote", "tote bag"),
    ("magnet", "magnet"),
    ("sticker", "sticker"),
]


def _lineup_sentence(titles: list[str]) -> str:
    found: list[str] = []
    lowered = [t.lower() for t in titles]
    for needle, label in GARMENT_WORDS:
        if any(needle in t for t in lowered) and label not in found:
            found.append(label)
    if not found:
        return ""
    if len(found) == 1:
        return f"{found[0].capitalize()}."
    body = ", ".join(found[:-1])
    return f"{body} and {found[-1]}.".capitalize()


SIZE_ORDER = ["YXS", "YS", "YM", "YL", "YXL", "XS", "S", "M", "L", "XL",
              "2XL", "XXL", "3XL", "XXXL", "4XL", "5XL"]


def _size_sentence(sizes: list[str]) -> str:
    if not sizes:
        return ""
    normalized = [s.strip().upper() for s in sizes]
    ranked = [s for s in SIZE_ORDER if s in normalized]
    if not ranked:
        return ", ".join(sizes)
    youth = any(s.startswith("Y") for s in ranked)
    top = ranked[-1]
    start = "Youth" if youth else "Adult XS"
    return f"{start} through adult {top}."
