#!/usr/bin/env python3
"""Shift the recorded cost of every variant in the store by a fixed amount.

    ~/.venvs/steeple-onboarding-312/bin/python tools/bump_costs.py --by 1.50
    ~/.venvs/steeple-onboarding-312/bin/python tools/bump_costs.py --by 1.50 --write
    ~/.venvs/steeple-onboarding-312/bin/python tools/bump_costs.py --revert <csv>

Written for one specific job -- an overhead figure that was left out of the
cost of goods and has to be added to everything -- but it takes any delta, so
it is also how the next one gets corrected.

**Dry run by default.** It prints what it would change and exits. `--write` is
the only thing that touches the store.

## Cost lives on the VARIANT

There is no such thing as a product's cost. Shopify records `unitCost` on the
`InventoryItem` behind each variant, so "every product" means 1,149 variants
across 95 products, and each one is its own write. See
claude/ops/06-sales-dashboard.md, which is where that distinction was first
paid for.

## A missing cost is not a zero cost

The rule this whole system is built on. A variant with no cost recorded is
**skipped**, never treated as 0.00 and bumped to the delta -- that would turn
"we don't know" into a confident, wrong number, and it would flow straight into
a partner's donation. The dry run counts them separately and names the products
so they can be fixed properly.

## This changes history

`shopify_sales.py` reads the CURRENT `unitCost` when it pulls orders; the cost
is not frozen onto the order line at the time of sale. So raising costs lowers
the margin on **every order already taken**, and with it every partner's
donation. That is correct if the overhead was always being incurred and simply
was not recorded -- which is the case here -- but it is not a
going-forward-only change, and the statements screen will show different
figures the moment the cache is refreshed.

The dry run prints that impact before anything is written.

## Getting out of it

Every write run saves a CSV of before/after to `cost_changes/`. `--revert`
takes that file and puts every value back exactly as it was. Nothing here
depends on remembering what the delta was.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import os
import sys
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests                                              # noqa: E402

from onboarding.shopify_pull import _load_env, configured    # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cost_changes"
TIMEOUT = 30

# How many aliased mutations to put in one request. Each inventoryItemUpdate
# costs 10 points and the Basic-plan bucket is 2,000 with 100/sec restore, so
# 25 is 250 points -- comfortably inside a single request and cheap to retry if
# one request fails. Bigger batches save wall-clock and cost more to redo.
BATCH = 25

READ = """
query Variants($first: Int!, $after: String) {
  productVariants(first: $first, after: $after) {
    edges {
      node {
        id
        displayName
        inventoryItem { id unitCost { amount } }
        product { title vendor status }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


def endpoint() -> str:
    store = os.environ["SHOPIFY_STORE"]
    version = os.environ.get("SHOPIFY_API_VERSION", "2025-07")
    return f"https://{store}/admin/api/{version}/graphql.json"


def call(query: str, variables: dict | None = None) -> dict:
    """One GraphQL call. Returns the whole payload -- extensions included.

    The throttle status lives in `extensions`, and a bulk write that ignores it
    gets a 'Throttled' error partway through, which on a write is the worst
    place to stop.
    """
    response = requests.post(
        endpoint(),
        headers={"X-Shopify-Access-Token": os.environ["SHOPIFY_ADMIN_TOKEN"],
                 "Content-Type": "application/json"},
        json={"query": query, "variables": variables or {}},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("errors"):
        raise RuntimeError(str(payload["errors"][:2]))
    return payload


def breathe(payload: dict, need: int = 300) -> None:
    """Wait until the leaky bucket has room for the next batch."""
    status = ((payload.get("extensions") or {}).get("cost") or {}).get("throttleStatus")
    if not status:
        return
    available = status.get("currentlyAvailable", 0)
    restore = status.get("restoreRate", 100) or 100
    if available < need:
        time.sleep(min((need - available) / restore + 0.2, 10))


def money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


# ------------------------------------------------------------------- read --

def fetch_all() -> list[dict]:
    rows, after, page = [], None, 0
    while True:
        payload = call(READ, {"first": 50, "after": after})
        block = payload["data"]["productVariants"]
        for edge in block["edges"]:
            node = edge["node"]
            cost = (node["inventoryItem"].get("unitCost") or {}).get("amount")
            product = node.get("product") or {}
            rows.append({
                "inventory_item_id": node["inventoryItem"]["id"],
                "variant_id": node["id"],
                "name": node.get("displayName") or "",
                "product": product.get("title") or "",
                "vendor": product.get("vendor") or "",
                "status": product.get("status") or "",
                # None, not 0. Everything downstream depends on the difference.
                "cost": Decimal(cost) if cost not in (None, "") else None,
            })
        page += 1
        print(f"\r  read {len(rows)} variants ({page} pages)…", end="", flush=True)
        if not block["pageInfo"]["hasNextPage"]:
            print()
            return rows
        after = block["pageInfo"]["endCursor"]
        breathe(payload, need=120)


# ---------------------------------------------------------------- preview --

def preview(rows: list[dict], delta: Decimal, statuses: set[str] | None) -> list[dict]:
    costed = [r for r in rows if r["cost"] is not None]
    missing = [r for r in rows if r["cost"] is None]
    if statuses:
        costed = [r for r in costed if r["status"] in statuses]

    for row in costed:
        row["new_cost"] = row["cost"] + delta

    negative = [r for r in costed if r["new_cost"] < 0]

    print()
    print(f"  {len(rows):>5} variants in the store")
    print(f"  {len(costed):>5} will change  ({money(delta)} each)")
    print(f"  {len(missing):>5} have no cost recorded — SKIPPED, not set to {money(delta)}")
    if statuses:
        skipped = len([r for r in rows if r['cost'] is not None
                       and r['status'] not in statuses])
        print(f"  {skipped:>5} excluded by --status {','.join(sorted(statuses))}")

    by_status: dict[str, int] = {}
    for row in costed:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    print("\n  Changing, by product status:")
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"    {status:<10} {count:>5}")

    by_vendor: dict[str, list[dict]] = {}
    for row in costed:
        by_vendor.setdefault(row["vendor"] or "(no vendor)", []).append(row)
    print("\n  Changing, by vendor:")
    for vendor, items in sorted(by_vendor.items(), key=lambda kv: -len(kv[1])):
        low = min(i["cost"] for i in items)
        high = max(i["cost"] for i in items)
        print(f"    {vendor[:34]:<34} {len(items):>5}   "
              f"{money(low)}–{money(high)} → {money(low + delta)}–{money(high + delta)}")

    if missing:
        products = sorted({r["product"] for r in missing})
        print(f"\n  No cost recorded on {len(missing)} variants across "
              f"{len(products)} product(s):")
        for title in products[:15]:
            count = len([r for r in missing if r["product"] == title])
            print(f"    {title[:56]:<56} {count:>4} variant(s)")
        if len(products) > 15:
            print(f"    …and {len(products) - 15} more")
        print("  These are left alone. A blank cost means the cost is unknown,")
        print("  not zero — adding to it would invent a number.")

    if negative:
        print(f"\n  REFUSING: {len(negative)} variant(s) would go below zero:")
        for row in negative[:10]:
            print(f"    {row['name'][:60]:<60} {money(row['cost'])} → {money(row['new_cost'])}")

    print("\n  Effect on margin, and so on donations:")
    print("  Cost is read live when orders are pulled, so this also changes")
    print("  every order already taken. For each unit sold of an affected")
    print(f"  variant, margin moves by {money(-delta)} and a partner's 30%")
    print(f"  donation by {money(-delta * Decimal('0.30'))}.")
    print("  Refresh the dashboard afterwards and re-read /statements before")
    print("  sending anything.")

    return [] if negative else costed


# ----------------------------------------------------------------- write --

def write_changes(rows: list[dict], label: str) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    path = OUT / f"{stamp}_{label}.csv"
    with path.open("w", newline="", encoding="utf8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["inventory_item_id", "variant_id", "name",
                         "old_cost", "new_cost"])
        for row in rows:
            writer.writerow([row["inventory_item_id"], row["variant_id"],
                             row["name"], money(row["cost"]),
                             money(row["new_cost"])])
    return path


def apply(rows: list[dict]) -> int:
    """Send the updates in aliased batches. Returns the number that failed."""
    failed = 0
    done = 0
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        parts, variables, types = [], {}, []
        for index, row in enumerate(chunk):
            types.append(f"$id{index}: ID!")
            parts.append(
                f'  u{index}: inventoryItemUpdate(id: $id{index}, '
                f'input: {{cost: "{money(row["new_cost"])}"}}) '
                f'{{ userErrors {{ field message }} }}'
            )
            variables[f"id{index}"] = row["inventory_item_id"]
        document = "mutation Bump(" + ", ".join(types) + ") {\n" + "\n".join(parts) + "\n}"

        payload = call(document, variables)
        data = payload.get("data") or {}
        for index, row in enumerate(chunk):
            result = data.get(f"u{index}") or {}
            for problem in result.get("userErrors") or []:
                failed += 1
                print(f"\n  FAILED {row['name'][:50]}: {problem.get('message')}")
        done += len(chunk)
        print(f"\r  wrote {done}/{len(rows)}…", end="", flush=True)
        breathe(payload)
    print()
    return failed


def revert(path: Path) -> int:
    rows = []
    with path.open(encoding="utf8") as handle:
        for record in csv.DictReader(handle):
            rows.append({
                "inventory_item_id": record["inventory_item_id"],
                "variant_id": record["variant_id"],
                "name": record["name"],
                # Deliberately swapped: putting back what was there before.
                "cost": Decimal(record["new_cost"]),
                "new_cost": Decimal(record["old_cost"]),
            })
    print(f"  reverting {len(rows)} variants from {path.name}")
    failed = apply(rows)
    write_changes(rows, "revert")
    return failed


# ------------------------------------------------------------------ main --

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Shift every variant's recorded cost by a fixed amount.")
    parser.add_argument("--by", type=str,
                        help="amount to add, e.g. 1.50 or -1.50")
    parser.add_argument("--write", action="store_true",
                        help="actually change the store (default is a dry run)")
    parser.add_argument("--status", type=str, default="",
                        help="limit to these product statuses, comma separated "
                             "(ACTIVE,DRAFT,UNLISTED,ARCHIVED). Default: all.")
    parser.add_argument("--revert", type=str,
                        help="undo a previous run from its CSV")
    args = parser.parse_args()

    _load_env()
    if not configured():
        print("Not connected to Shopify — SHOPIFY_STORE and SHOPIFY_ADMIN_TOKEN")
        print("must be set in .env. Connect the app from Settings → Shopify.")
        return 1

    print(f"Store: {os.environ['SHOPIFY_STORE']}")

    if args.revert:
        path = Path(args.revert)
        if not path.exists():
            path = OUT / args.revert
        if not path.exists():
            print(f"No such file: {args.revert}")
            return 1
        return 1 if revert(path) else 0

    if not args.by:
        parser.error("--by is required (e.g. --by 1.50)")
    delta = Decimal(args.by)
    statuses = {s.strip().upper() for s in args.status.split(",") if s.strip()}

    print(f"Reading every variant…")
    rows = fetch_all()
    changes = preview(rows, delta, statuses or None)

    if not changes:
        print("\nNothing to do.")
        return 1

    if not args.write:
        print("\n  Dry run — nothing was changed.")
        print("  Re-run with --write to apply.")
        return 0

    path = write_changes(changes, f"bump_{money(delta).replace('-', 'minus')}")
    print(f"\n  Before/after saved to {path.relative_to(ROOT)}")
    print("  Undo any time with:")
    print(f"    python tools/bump_costs.py --revert {path.name}")
    print()

    failed = apply(changes)
    print(f"\n  {len(changes) - failed} updated, {failed} failed.")
    print("\n  Now refresh the dashboard from Shopify so the cached order data")
    print("  picks up the new costs, then re-read /statements before sending.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
