"""Compare every partner record against what is actually live in Shopify.

    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py
    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py haven-of-hope-church
    ~/.venvs/steeple-onboarding-312/bin/python tools/storefront_audit.py --quiet

Reads only. Exits non-zero if anything failed, so it can be scheduled.

Why this exists. On 15 Sep a QR code printed on a postcard did not work. Behind
it: a collection created by hand rather than with the Create storefront button,
so it had no VENDOR EQUALS rule; a redirect entered with the two columns
swapped, which broke the collection's own URL as well as the QR; and a partner
record whose collection_gid was never filled in. Three faults, live, and the
first thing that noticed any of them was a person scanning a printed card.

Every one of them is a second's worth of API call. The app had the record and
Shopify had the truth and nothing ever compared the two.

The checks, per partner:

  collection    exists at the record's handle
  rule          VENDOR EQUALS, condition matching the record's vendor EXACTLY
  products      the rule catches something
  redirect      /go/<slug> exists AND points at the collection, not away from it
  published     on the Online Store
  gid           collection_gid on the record matches the live collection

The rule check compares strings exactly and deliberately does not normalise
case or trailing 's'. "Germantown Christian Schools" and "Germantown Christian
School" are the two strings that put $2,973.76 on the wrong partner, and a
check that smoothed over the difference would have passed that too.

The redirect DIRECTION check is not fussiness. Shopify's own form labels invite
the mistake, and a reversed redirect cannot be corrected by editing it --
Shopify refuses with "Target can't redirect to another redirect", because while
the record still claims the collection's URL as its source, nothing may point
at that URL, including itself. It has to be deleted and recreated.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import store                                    # noqa: E402
from onboarding.shopify_pull import _load_env, configured       # noqa: E402
from onboarding.storefront import _gql                          # noqa: E402

OK, WARN, FAIL = "OK  ", "WARN", "FAIL"

QUERY = """
query($handle: String!, $redirects: String!) {
  collectionByHandle(handle: $handle) {
    id
    title
    productsCount { count }
    ruleSet { appliedDisjunctively rules { column relation condition } }
    resourcePublicationsV2(first: 20) {
      edges { node { isPublished publication { id } } }
    }
  }
  urlRedirects(first: 50, query: $redirects) {
    edges { node { id path target } }
  }
}
"""


class Report:
    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: list[tuple[str, str, str]] = []

    def add(self, level: str, check: str, detail: str) -> None:
        self.rows.append((level, check, detail))

    @property
    def failed(self) -> bool:
        return any(level == FAIL for level, _, _ in self.rows)

    def print(self, quiet: bool) -> None:
        if quiet and not self.failed:
            return
        print(self.name)
        for level, check, detail in self.rows:
            if quiet and level == OK:
                continue
            print(f"   {level}  {check:<11} {detail}")
        print()


def audit(record: dict) -> Report:
    name = record.get("org_name") or store.partner_id(record)
    report = Report(name)

    handle = (record.get("collection_handle") or "").strip().lower()
    vendor = (record.get("vendor") or "").strip()
    slug = (record.get("redirect_slug") or "").strip().lower()
    stored_gid = (record.get("collection_gid") or "").strip()

    if not handle:
        report.add(FAIL, "record", "no collection handle — nothing to check against")
        return report
    if not vendor:
        report.add(FAIL, "record", "no vendor string — the rule would catch nothing")

    # One round trip per partner. The redirect query is a free-text search, so
    # it is filtered properly below rather than trusted.
    result = _gql(QUERY, {"handle": handle, "redirects": slug or handle})
    if not result["ok"]:
        report.add(FAIL, "shopify", result["error"])
        return report

    data = result.get("data") or {}
    collection = data.get("collectionByHandle")
    if not collection:
        report.add(FAIL, "collection", f"nothing live at /collections/{handle}")
        return report
    report.add(OK, "collection", f"{collection['title']}  ({handle})")

    # --- the rule
    rules = ((collection.get("ruleSet") or {}).get("rules")) or []
    vendor_rules = [r for r in rules if r["column"] == "VENDOR" and r["relation"] == "EQUALS"]
    if not rules:
        report.add(FAIL, "rule", "no rules at all — a manual collection. Sales "
                                 "attribution falls back to guessing the owner "
                                 "from the name (doc 18).")
    elif not vendor_rules:
        report.add(FAIL, "rule", "has rules but none is VENDOR EQUALS: "
                   + ", ".join(f"{r['column']} {r['relation']} {r['condition']}" for r in rules))
    elif vendor and vendor_rules[0]["condition"] != vendor:
        report.add(FAIL, "rule", f"matches {vendor_rules[0]['condition']!r} but the "
                                 f"record says {vendor!r} — exact string, singular "
                                 "and plural are different partners")
    else:
        report.add(OK, "rule", f"VENDOR EQUALS {vendor_rules[0]['condition']!r}")

    # --- does it catch anything
    count = (collection.get("productsCount") or {}).get("count", 0)
    if count:
        report.add(OK, "products", f"{count}")
    else:
        report.add(WARN, "products", "0 — the QR resolves to an empty page. "
                                     "Nothing to print postcards for yet.")

    # --- published
    pubs = [e["node"] for e in
            ((collection.get("resourcePublicationsV2") or {}).get("edges") or [])]
    live = [p for p in pubs if p.get("isPublished")]
    if live:
        report.add(OK, "published", f"{len(live)} channel{'' if len(live) == 1 else 's'}")
    else:
        report.add(FAIL, "published", "on no sales channel — the page 404s")

    # --- the redirect, and which way round it points
    want_path = f"/go/{slug}" if slug else ""
    want_target = f"/collections/{handle}"
    found = [e["node"] for e in ((data.get("urlRedirects") or {}).get("edges") or [])]
    if not slug:
        report.add(WARN, "redirect", "no redirect slug on the record; the QR "
                                     "points straight at the collection")
    else:
        forward = [r for r in found if r["path"] == want_path]
        reversed_ = [r for r in found if r["target"] == want_path]
        if forward and forward[0]["target"] == want_target:
            report.add(OK, "redirect", f"{want_path} → {want_target}")
        elif forward:
            report.add(FAIL, "redirect", f"{want_path} → {forward[0]['target']} "
                                         f"(should be {want_target})")
        elif reversed_:
            report.add(FAIL, "redirect", f"BACKWARDS: {reversed_[0]['path']} → "
                       f"{reversed_[0]['target']}. This also breaks the "
                       "collection's own URL. It cannot be edited — delete it "
                       "and create the redirect the other way round.")
        else:
            report.add(FAIL, "redirect", f"{want_path} does not exist. Every QR "
                                         "code printed with it is dead.")

    # --- the record pointing back at the collection
    if not stored_gid:
        report.add(WARN, "gid", "collection_gid is empty on the record — built "
                                "by hand rather than with Create storefront?")
    elif stored_gid != collection["id"]:
        report.add(FAIL, "gid", f"record says {stored_gid}, live is {collection['id']}")
    else:
        report.add(OK, "gid", "matches")

    return report


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    wanted = {a.strip().lower() for a in argv if not a.startswith("-")}

    _load_env()
    if not configured():
        print("Shopify is not connected. Settings > Shopify.")
        return 2

    records = store.list_partners()
    if wanted:
        records = [r for r in records if store.partner_id(r).lower() in wanted]
        if not records:
            print(f"No partner matched {', '.join(sorted(wanted))}")
            return 1
    if not records:
        print("No partner records found.")
        return 1

    reports = [audit(r) for r in records]
    for report in reports:
        report.print(quiet)

    bad = [r for r in reports if r.failed]
    print(f"{len(records) - len(bad)} of {len(records)} clean.")
    if bad:
        print("Failing: " + ", ".join(r.name for r in bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
