"""Stamp each partner record with the Shopify vendor string that credits it.

Guessing the vendor from the organisation's name is what put $2,973.76 of
Germantown's sales onto GCS Athletics: the GCS Athletics record inherited
`org_legal_name` from its parent school, so both records guessed the same
vendor and whichever sorted first won. The names cannot arbitrate this.

The store can. Every partner collection except the two manual ones is a smart
collection with a `VENDOR EQUALS "<name>"` rule -- that rule IS the definition
of which products belong to that partner, so it is the authority. This reads it
off the collection named by the record's own `collection_handle` and writes it
to `shopify_vendor`, which `dashboard.vendor_map()` treats as final.

Dry run by default. Pass --write to save.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import dashboard, shopify_sales, store   # noqa: E402

WRITE = "--write" in sys.argv


def vendor_from_collection(handle: str, collections: list[dict]) -> str:
    for collection in collections:
        if collection.get("handle") != handle:
            continue
        rules = ((collection.get("ruleSet") or {}).get("rules")) or []
        for rule in rules:
            if rule.get("column") == "VENDOR" and rule.get("relation") == "EQUALS":
                return rule.get("condition") or ""
    return ""


def main() -> int:
    snapshot, source = shopify_sales.snapshot()
    if not snapshot:
        print(f"No Shopify data: {source}")
        return 1
    collections = snapshot.get("collections", [])
    print(f"Shopify data source: {source}\n")

    changes = 0
    for record in store.list_partners():
        handle = record.get("collection_handle", "")
        current = record.get("shopify_vendor", "")
        found = vendor_from_collection(handle, collections)

        if not found:
            # A manual collection has no rule to read. The organisation's own
            # name is then the best available answer, but say so rather than
            # writing a guess in the field that means "confirmed".
            guess = record.get("org_name", "")
            print(f"  --  {record.get('org_name','?'):32} /{handle} is a manual "
                  f"collection — no vendor rule. Leaving as a guess: {guess!r}")
            continue

        if current == found:
            print(f"  ok  {record.get('org_name','?'):32} {found!r}")
            continue

        print(f"  ->  {record.get('org_name','?'):32} {current!r} -> {found!r}")
        changes += 1
        if WRITE:
            record["shopify_vendor"] = found
            store.save(record)

    print()
    if not WRITE:
        print(f"Dry run. {changes} record(s) would change. Re-run with --write.")
    else:
        print(f"Wrote {changes} record(s).")

    mapping, collisions = dashboard.vendor_map(store.list_partners())
    if collisions:
        print("\nRemaining vendor collisions:")
        for c in collisions:
            print(f"  {c['vendor']!r}: {c['winner']} wins over {', '.join(c['others'])}"
                  f"  ({'resolved by exact name' if c['resolved'] else 'ARBITRARY'})")
    else:
        print("\nNo vendor collisions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
