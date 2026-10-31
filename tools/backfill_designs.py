#!/usr/bin/env python3
"""Write each in-house product's DTF design code to Shopify. Dry run by default.

designs.json (the Print Library, built by the DTF Assignment tool) maps product
-> design code. This copies the code onto the product as the metafield
custom.dtf_design, so it can be seen and pinned on the product page and read by
anything that only has the product. The slip and the Purchasing tab read
designs.json directly and do not need this to have run.

    python3 tools/backfill_designs.py            # show what would change
    python3 tools/backfill_designs.py --commit   # write it
    python3 tools/backfill_designs.py --define   # also create the pinned definition (once)

Only products whose stored code differs are written. A product that has left
designs.json keeps its old value -- clear it by hand if that is what you want.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import dtf                                    # noqa: E402
from onboarding.storefront import _gql, _user_errors          # noqa: E402

NS, KEY = "custom", "dtf_design"
BATCH = 25

READ = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on Product {
  id title metafield(namespace: "custom", key: "dtf_design") { value } } } }"""
WRITE = """mutation($m: [MetafieldsSetInput!]!) { metafieldsSet(metafields: $m) {
  metafields { id } userErrors { field message } } }"""
DEFINE = """mutation($d: MetafieldDefinitionInput!) { metafieldDefinitionCreate(definition: $d) {
  createdDefinition { id } userErrors { field message code } } }"""


def main(argv) -> int:
    commit, define = "--commit" in argv, "--define" in argv
    designs = dtf.load_designs()
    want = {pid: p.get("code") for pid, p in (designs.get("products") or {}).items() if p.get("code")}
    if not want:
        print(f"Nothing to do: no products in {dtf.designs_path()}.")
        return 1
    if define and commit:
        r = _gql(DEFINE, {"d": {"name": "DTF Design", "namespace": NS, "key": KEY,
                                "type": "single_line_text_field", "ownerType": "PRODUCT", "pin": True,
                                "description": "Design code from the Print Library (28-Print-Library/designs.json)."}})
        err = r.get("error") or _user_errors((r.get("data") or {}).get("metafieldDefinitionCreate"))
        print("Definition:", "created" if not err else f"not created — {err}")

    changes, missing = [], []
    ids = [f"gid://shopify/Product/{pid}" for pid in want]
    for i in range(0, len(ids), BATCH):
        r = _gql(READ, {"ids": ids[i:i + BATCH]})
        if not r["ok"]:
            print("Read failed:", r["error"])
            return 2
        for gid, node in zip(ids[i:i + BATCH], r["data"].get("nodes") or []):
            pid = gid.rsplit("/", 1)[-1]
            if not node:
                missing.append(pid)
                continue
            have = (node.get("metafield") or {}).get("value") or ""
            if have != want[pid]:
                changes.append((gid, node["title"], have, want[pid]))

    for _, title, have, new in changes:
        print(f"  {new:<10} {'(was ' + have + ')' if have else '':<16} {title}")
    if missing:
        print(f"{len(missing)} product(s) in designs.json no longer exist in Shopify: {', '.join(missing)}")
    print(f"{len(changes)} to write, {len(want) - len(changes) - len(missing)} already correct.")
    if not commit or not changes:
        if changes:
            print("Dry run — nothing written. Add --commit to write.")
        return 0

    failed = 0
    for i in range(0, len(changes), BATCH):
        m = [{"ownerId": gid, "namespace": NS, "key": KEY, "type": "single_line_text_field", "value": new}
             for gid, _, _, new in changes[i:i + BATCH]]
        r = _gql(WRITE, {"m": m})
        err = r.get("error") or _user_errors((r.get("data") or {}).get("metafieldsSet"))
        if err:
            failed += len(m)
            print("Write failed:", err)
    print(f"Wrote {len(changes) - failed} of {len(changes)}.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
