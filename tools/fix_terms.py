#!/usr/bin/env python3
"""Restore plan terms on partner records that still carry the schema seed.

Dry run by default.

`schema.py` used to seed `margin_pct` to 10 and both fees to 0. Any partner
created without hand-editing those fields silently carried terms nobody had
agreed to -- and those figures render above the signature line on the Service
Agreement. The seed is fixed going forward; existing records were deliberately
left alone, because a negotiated exception is legitimate and only Larry knows
which is which. See 25-live-terms-audit.md.

This script does not decide. It restores a field to the plan's value ONLY when
the field still holds the exact old seed value (0 / 0 / 10), which is the
fingerprint of a record nobody edited. Anything else -- a fee of 250, a margin
of 35, a blank -- is reported and left alone, because that is somebody's
decision and not a bug.

Records are edited as JSON in place. `store.save()` is deliberately NOT used:
it recomputes a record's id from the organisation name and migrates assets and
output folders to match, which is far more than a terms fix should be able to
do. Same reasoning as backfill_vendors.py.

    python3 tools/fix_terms.py                    # show what would change
    python3 tools/fix_terms.py --only gcs-athletics   # limit to one record
    python3 tools/fix_terms.py --commit           # write it

A backup of every file it touches is written alongside, once per run.
"""
import json
import shutil
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTNERS = ROOT / "partners"
sys.path.insert(0, str(ROOT))

from onboarding import terms  # noqa: E402

# The exact values schema.py used to seed. A field still holding one of these
# is a record nobody edited; anything else was somebody's decision.
# A blank is deliberately NOT here. Zero is the seed nobody edited; empty is a
# field somebody cleared, which is a decision this script must not guess at.
SEED = {"setup_fee": (0, 0.0, "0"), "monthly_fee": (0, 0.0, "0"),
        "margin_pct": (10, 10.0, "10")}

FIELDS = (("setup_fee", "setup"), ("monthly_fee", "monthly"), ("margin_pct", "margin"))




def main() -> int:
    commit = "--commit" in sys.argv
    only = ""
    if "--only" in sys.argv:
        index = sys.argv.index("--only")
        if index + 1 < len(sys.argv):
            only = sys.argv[index + 1].strip().lower()

    backup = PARTNERS.parent / f"partners_backup_{date.today():%Y-%m-%d}_terms"
    touched = fixed = left = ok = 0

    for path in sorted(PARTNERS.glob("*.json")):
        if only and only not in path.stem.lower():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        org = record.get("org_name", path.stem)
        plan_name = record.get("plan") or ""
        want = terms.plan(plan_name)
        if not want:
            print(f"  ?  {org:32} no plan set — nothing to compare against")
            continue

        changes = {}
        notes = []
        for key, label in FIELDS:
            if key not in want:
                continue
            have = record.get(key)
            expected = want[key]
            same = str(have).replace("$", "").replace("%", "").strip() == str(expected)
            if same:
                continue
            if have in SEED.get(key, ()):
                changes[key] = expected
                notes.append(f"{label} {have!r} -> {expected}")
            else:
                notes.append(f"{label} {have!r} (plan says {expected}) LEFT ALONE")

        if not notes:
            print(f"  =  {org:32} matches plan {plan_name}")
            ok += 1
            continue

        mark = "->" if changes else " !"
        print(f"  {mark} {org:32} plan {plan_name}")
        for note in notes:
            print(f"       {note}")

        if not changes:
            left += 1
            continue
        fixed += 1
        if commit:
            backup.mkdir(exist_ok=True)
            shutil.copy2(path, backup / path.name)
            record.update(changes)
            path.write_text(json.dumps(record, indent=1, ensure_ascii=False) + "\n",
                            encoding="utf-8")
            touched += 1

    print()
    print(f"  {ok} already correct · {fixed} would be restored · "
          f"{left} differ but were not seeds")
    if commit:
        print(f"  {touched} file(s) written. Backup: {backup}")
    else:
        print("  Dry run. Re-run with --commit to write.")
    print("\n  Regenerate any agreement already produced for a changed record —"
          "\n  the old PDF still carries the old figures above the signature line.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
