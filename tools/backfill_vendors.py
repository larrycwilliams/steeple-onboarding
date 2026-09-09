#!/usr/bin/env python3
"""Backfill the `vendor` field on existing partner records. Dry run by default.

The vendor string is what every collection's `VENDOR EQUALS` rule matches on,
so it is copied from the live Shopify collections rather than derived from the
organisation name. The two differ in at least one live case -- this app holds
"Germantown Christian Schools", Shopify holds "Germantown Christian School" --
and deriving it is the mechanism that put $2,973.76 on the wrong partner.

Records are edited as JSON in place. `store.save()` is deliberately NOT used:
it recomputes a record's id from the organisation name and migrates assets and
output folders to match, which is far more than a one-field backfill should be
able to do. See the seed_gcs.py note in 01-partner-onboarding-app.md.

    python3 tools/backfill_vendors.py            # show what would change
    python3 tools/backfill_vendors.py --commit   # write it
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTNERS = ROOT / "partners"

# collection handle -> the vendor string Shopify actually carries, read off the
# live collection rules on 2026-09-08.
VENDOR_BY_HANDLE = {
    "gcs-cardinals":                "Germantown Christian School",
    "gcs-athletics":                "GCS Athletics",
    "revive-church":                "Revive Church",
    "free-holiness-church-of-god":  "Free Holiness Church of God",
    "highway-of-holiness":          "Highway of Holiness",
    "community-christian-apparel":  "Community Christian",
    "the-men-of-faith":             "The Men of Faith",
    "alt-west":                     "ALT-WEST",
    "steeple-stitch":               "Steeple & Stitch Co.",
}


def main() -> int:
    commit = "--commit" in sys.argv
    changed = skipped = unknown = 0

    for path in sorted(PARTNERS.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        handle = (record.get("collection_handle") or "").strip().lower()
        org = record.get("org_name", "")
        current = (record.get("vendor") or "").strip()
        wanted = VENDOR_BY_HANDLE.get(handle)

        if not wanted:
            print(f"  ?  {org:34} handle {handle!r} is not in the map — set by hand")
            unknown += 1
            continue
        if current == wanted:
            print(f"  =  {org:34} already {wanted!r}")
            skipped += 1
            continue
        if current:
            print(f"  !  {org:34} has {current!r}, live store says {wanted!r} — LEFT ALONE")
            skipped += 1
            continue

        note = "" if org == wanted else f"   (org name differs: {org!r})"
        print(f"  +  {org:34} vendor = {wanted!r}{note}")
        changed += 1
        if commit:
            record["vendor"] = wanted
            path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    print()
    print(f"{changed} to set, {skipped} already correct or held, {unknown} unmapped")
    if changed and not commit:
        print("Dry run — nothing written. Re-run with --commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
