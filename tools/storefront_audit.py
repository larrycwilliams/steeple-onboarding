"""Every partner's record, checked against what is actually live.

    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py
    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py --all
    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py --quiet
    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py haven-of-hope-church

Read-only. Exits non-zero if anything is blocking, so it can be scheduled.

By default this shows the Shopify half -- collection, vendor rule, products,
redirect, publishing, and whether the record's collection_gid agrees with what
is live. `--all` adds the record, artwork, agreement and package checks, which
is everything the Readiness screen shows.

THE LOGIC IS NOT HERE. It is in onboarding/readiness.py, which the screen uses
too. Two copies would eventually disagree about what "ready" means, and a
check that quietly disagrees with the screen it is meant to back up is this
project's signature bug wearing a new hat -- see doc 18, doc 38, doc 39.

Why it exists. On 15 Sep a QR code printed on a postcard did not work. Behind
it: a collection created by hand rather than with the Create storefront button,
so no VENDOR EQUALS rule -- the condition doc 18 records as putting $2,973.76
of one partner's sales on another; a redirect entered with the columns
swapped, which broke the collection's own URL as well as the QR; and a
collection_gid never written back. The app had the record, Shopify had the
truth, and nothing compared them.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import readiness, store                         # noqa: E402

LEVEL = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    everything = "--all" in argv
    wanted = {a.strip().lower() for a in argv if not a.startswith("-")}

    records = store.list_partners()
    if wanted:
        records = [r for r in records if store.partner_id(r).lower() in wanted]
        if not records:
            print(f"No partner matched {', '.join(sorted(wanted))}")
            return 1
    if not records:
        print("No partner records found.")
        return 1

    assessed = readiness.assess_all(records)
    blocking = 0

    for entry in assessed:
        rows = [f for f in entry["findings"]
                if everything or f["section"] == "Storefront"]
        if not rows:
            continue
        bad = [f for f in rows if f["level"] == "fail"]
        blocking += len(bad)
        if quiet and not bad:
            continue
        print(entry["name"])
        for finding in rows:
            if quiet and finding["level"] == "ok":
                continue
            print(f"   {LEVEL[finding['level']]}  {finding['check']:<13} "
                  f"{finding['detail']}")
            if finding["fix"] and finding["level"] != "ok":
                print(f"         {' ' * 13}  {finding['fix']}")
        print()

    names = [e["name"] for e in assessed
             if any(f["level"] == "fail" and (everything or f["section"] == "Storefront")
                    for f in e["findings"])]
    print(f"{len(assessed) - len(names)} of {len(assessed)} clean"
          + ("" if everything else " (storefront checks only; --all for the rest)"))
    if names:
        print("Failing: " + ", ".join(names))
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
