"""What the postcard renderer sees when it looks at a partner's logo.

    ~/.venvs/steeple-onboarding-312/bin/python tools/logo_check.py            # every partner
    ~/.venvs/steeple-onboarding-312/bin/python tools/logo_check.py haven-of-hope-church

Reads the partner's real logo and their real card colour and prints the two
numbers the plate decision turns on, plus the verdict. Changes nothing.

This exists because the decision was guessed at twice from mock-ups and was
wrong both times. A share of pixels is not a share of meaning, and the only
way to know what a mark does on a card is to measure that mark.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import store                                    # noqa: E402
from onboarding.imaging import (CELL_READS_SHARE, READS_SHARE,   # noqa: E402
                                _logo_reads, prepare_logo)
from onboarding.schema import derive                            # noqa: E402


def _rgb(value: str) -> tuple[int, int, int]:
    value = (value or "11161D").lstrip("#")
    if len(value) != 6:
        value = "11161D"
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))


def check(record: dict) -> None:
    name = record.get("org_name") or store.partner_id(record)
    logo_path = store.resolve_logo(record)
    if not logo_path:
        print(f"{name}: no logo on the record")
        return

    ctx = derive(record)
    palette = ctx.get("palette") or []
    # Same two colours _front uses: the card body, and the ticket band.
    ink = _rgb(palette[2]["hex"] if len(palette) > 2 else "11161D")

    logo = prepare_logo(logo_path)
    width, height = logo.size
    # The card thumbnails it before placing it; measure what actually lands.
    logo.thumbnail((int(3600 * 0.20), int(2400 * 0.24)))
    reads, _ink, detail = _logo_reads(logo, ink)
    if detail is None:
        print(f"{name}: not measurable (no alpha channel)")
        return

    worst = detail["worst_region"]
    print(f"{name}")
    print(f"   logo        {Path(logo_path).name}  {width}x{height}")
    print(f"   card        {detail['background']}")
    print(f"   reads       {detail['share']:6.1%}   (plate below {READS_SHARE:.0%})")
    print(f"   worst patch {worst:6.1%}   (plate below {CELL_READS_SHARE:.0%})"
          if worst is not None else "   worst patch      —")
    print(f"   verdict     {'PLACED AS SUPPLIED' if reads else 'PLATED'}"
          + (f" — {detail['reason']}" if detail.get("reason") else ""))
    if reads and worst is not None and worst < 0.5:
        print("   note        close. Look at the card before it is printed.")
    print()


def main(argv: list[str]) -> int:
    records = store.list_partners()
    if argv:
        wanted = {a.strip().lower() for a in argv}
        records = [r for r in records if store.partner_id(r).lower() in wanted]
        if not records:
            print(f"No partner matched {', '.join(argv)}")
            return 1
    if not records:
        print("No partner records found.")
        return 1
    for record in records:
        check(record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
