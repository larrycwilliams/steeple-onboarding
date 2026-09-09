"""Download each partner's product photography into their assets folder.

Run this on the Mac -- the remote bridge cannot reach the Shopify CDN, so a
package generated through it falls back to the placeholder card.

    ~/.venvs/steeple-onboarding/bin/python tools/fetch_product_shots.py
    ~/.venvs/steeple-onboarding/bin/python tools/fetch_product_shots.py --force
    ~/.venvs/steeple-onboarding/bin/python tools/fetch_product_shots.py revive-church

Images land in ``assets/<partner>/product-shots/`` and are reused from there
on every later generate, so this only has to succeed once per partner.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import product_shot, store  # noqa: E402


def main(argv: list[str]) -> int:
    force = "--force" in argv
    wanted = [a for a in argv if not a.startswith("-")]

    records = store.list_partners()
    if wanted:
        records = [r for r in records if store.partner_id(r) in wanted]
        if not records:
            print("No partner matched:", ", ".join(wanted))
            return 1

    failures = 0
    for record in records:
        name = record.get("org_name", "?")
        urls = record.get("product_shot_urls") or []
        if not urls:
            print(f"{name:32} — no image URLs on the record (store has no products)")
            continue

        result = product_shot.download_shots(record, force=force)
        have = len(product_shot.cached_shots(record))
        status = "ok" if result["ok"] else "FAILED"
        print(
            f"{name:32} {status:7} "
            f"downloaded={result['downloaded']} cached={result['skipped']} "
            f"on disk={have}"
        )
        for line in result["errors"]:
            print(f"{'':32}   {line}")
        if not result["ok"]:
            failures += 1

    print()
    print("Regenerate each partner afterwards so the kit picks up the new sheet.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
