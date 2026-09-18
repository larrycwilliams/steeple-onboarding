#!/usr/bin/env python3
"""Backfill `collection_gid` on partner records from the live store. Dry run by default.

Eight of eleven partners carry an empty `collection_gid` -- every storefront
that was built by hand in the Shopify admin rather than with **Create
storefront**, which writes it back. An empty gid has no customer-facing
symptom, which is exactly why it went unnoticed: it is the precondition for
the Haven of Hope failure in 18-storefront-automation.md, where the record did
not know its own collection and so nothing could compare the two sides.

The gid is read LIVE, by the record's own `collection_handle`. It is never
derived, and a record whose handle resolves to nothing is reported and left
alone rather than guessed at.

Records are edited as JSON in place. `store.save()` is deliberately NOT used:
it recomputes a record's id from the organisation name and migrates asset and
output folders to match -- far more than a one-field backfill should be able
to do. Same reasoning as tools/backfill_vendors.py.

    python3 tools/backfill_collection_gids.py            # show what would change
    python3 tools/backfill_collection_gids.py --commit   # write it
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PARTNERS = ROOT / "partners"

QUERY = """
query($handle: String!) {
  collectionByHandle(handle: $handle) { id title productsCount { count } }
}
"""


def main() -> int:
    commit = "--commit" in sys.argv
    try:
        from onboarding.storefront import _gql
    except Exception as exc:                      # no creds, no requests, ...
        print(f"Cannot reach Shopify: {exc}")
        return 2

    set_ = kept = missing = disagree = 0

    for path in sorted(PARTNERS.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        org = record.get("org_name", path.stem)
        handle = (record.get("collection_handle") or "").strip().lower()
        current = (record.get("collection_gid") or "").strip()

        if not handle:
            print(f"  ?  {org:34} no collection_handle on the record")
            missing += 1
            continue

        try:
            node = (_gql(QUERY, {"handle": handle}) or {}).get("collectionByHandle")
        except Exception as exc:
            print(f"  !  {org:34} lookup failed: {exc}")
            missing += 1
            continue

        if not node:
            print(f"  ?  {org:34} nothing live at handle {handle!r}")
            missing += 1
            continue

        live = node["id"]
        if current == live:
            print(f"  =  {org:34} already {live.rsplit('/', 1)[-1]}")
            kept += 1
            continue

        # A gid that is present and WRONG is a different fact from one that is
        # absent, and it is not this tool's job to overwrite it silently.
        if current:
            print(f"  !  {org:34} has {current.rsplit('/', 1)[-1]}, "
                  f"live is {live.rsplit('/', 1)[-1]} — LEFT ALONE")
            disagree += 1
            continue

        print(f"  +  {org:34} collection_gid = {live.rsplit('/', 1)[-1]}  "
              f"({node['title']}, {node['productsCount']['count']} product(s))")
        set_ += 1
        if commit:
            record["collection_gid"] = live
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print()
    print(f"{set_} to set, {kept} already correct, "
          f"{disagree} disagreeing (held), {missing} unresolvable")
    if disagree:
        print("A disagreeing gid means the record and the store point at "
              "different collections. Fix that by hand, and find out why.")
    if set_ and not commit:
        print("Dry run — nothing written. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
