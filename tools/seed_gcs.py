"""Regression check: build a GCS-shaped package from the app.

Compares the templating and the sampled palette against the kit Larry built by
hand.

    python tools/seed_gcs.py

It writes a **throwaway partner**, never a real one. It used to set
``record["id"] = "gcs"`` to stay out of the way, which worked while ids were
frozen at creation. Once partner ids started following the organization name,
``store.save`` recomputed this seed's id as ``germantown-christian-schools``
and the check silently overwrote the real GCS partner -- blanking Sarah Allen,
the address, and the negotiated 399 / 49 / 30% terms, and leaving a record
that would have generated a $0 agreement for a live customer.

Two guards now: the seed carries its own organization name so it can never
derive a real partner's id, and it refuses to touch any record it did not
create itself.
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from onboarding import package, store  # noqa: E402
from onboarding.palette import extract_palette  # noqa: E402

SEED_MARKER = "_seed_check"

GCS = {
    # Not "Germantown Christian Schools" -- that name derives the real
    # partner's id and this script would overwrite them.
    "org_name": "GCS Cardinals SEED CHECK",
    "org_legal_name": "GCS Cardinals SEED CHECK",
    "org_short": "GCS",
    "org_type": "school",
    "est_year": "1981",
    "mascot": "Cardinal",
    "mascot_plural": "Cardinals",
    "tagline": "",
    "poc_name": "",
    "poc_title": "",
    "poc_email": "",
    "poc_phone": "",
    "finance_name": "",
    "finance_email": "",
    "social_owner": "",
    "address_line1": "5088 Manning Rd",
    "address_line2": "",
    "city": "Miamisburg",
    "state": "OH",
    "zip": "45342",
    "shipping_same": "yes",
    "plan": "Growth",
    "setup_fee": "0",
    "monthly_fee": "0",
    "margin_pct": "10",
    "payout_method": "Check",
    "payout_frequency": "Quarterly",
    "payout_account": "",
    "fund_designation": "General fund",
    "collection_handle": "gcs-cardinals",
    "store_name": "GCS Cardinals Merch Store",
    "redirect_slug": "gcs",
    "product_lineup": "Crew tee, pullover hoodie, performance long sleeve and embroidered cap.",
    "size_range": "Youth through adult 3XL. (4X & 5X available in some sizes)",
    "fulfillment_days": "five to seven days",
    "agreement_date": "2026-09-05",
    "launch_date": "",
    "company_signer": "Lawrence C. Williams",
    "company_signer_title": "Owner",
    "client_signer": "",
    "client_signer_title": "",
}


def extract_gcs_logo() -> str:
    """Pull the cardinal mark out of the original kit so the test has real art."""
    src = ROOT / "source_docs" / "SRC_Launch_Week_Kit.docx"
    dest = store.asset_dir("gcs") / "gcs_cardinal_logo.png"
    if dest.exists():
        return str(dest)
    with zipfile.ZipFile(src) as archive:
        dest.write_bytes(archive.read("word/media/image1.png"))
    return str(dest)


def main() -> int:
    record = store.new_record()
    record.update(GCS)
    record[SEED_MARKER] = True

    # Refuse to write over anything this script did not create.
    pid = store.partner_id(record)
    existing = store.load(pid)
    if existing is not None and not existing.get(SEED_MARKER):
        print(f"Refusing to run: partners/{pid}.json exists and is not a seed "
              f"record ({existing.get('org_name')!r}).")
        return 1

    record["logo_path"] = extract_gcs_logo()
    record["palette"] = extract_palette(record["logo_path"])

    print("Sampled palette:")
    for colour in record["palette"]:
        print(f"  {colour['name']:<14} #{colour['hex']}")

    record = store.save(record)
    manifest = package.build(record)

    print("\nGenerated:")
    for label, path in manifest["files"].items():
        print(f"  {label:<26} {Path(path).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
