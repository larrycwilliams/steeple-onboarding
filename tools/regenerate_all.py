"""Rebuild every partner's package, or just the ones you name.

    ~/.venvs/steeple-onboarding/bin/python tools/regenerate_all.py
    ~/.venvs/steeple-onboarding/bin/python tools/regenerate_all.py revive-church

Prints where each partner's product photo came from and which fields are still
unset, so nothing goes out with placeholder terms or a placeholder photo
without you having seen it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from onboarding import package, store  # noqa: E402


def main(argv: list[str]) -> int:
    wanted = [a for a in argv if not a.startswith("-")]
    records = store.list_partners()
    if wanted:
        records = [r for r in records if store.partner_id(r) in wanted]
        if not records:
            print("No partner matched:", ", ".join(wanted))
            return 1

    # Read manifest keys with .get(). This loop used to do
    # manifest["product_photo"], and when that key was removed with the
    # product-photo section the whole run died with a KeyError after the first
    # partner -- looking exactly like a hang, because the traceback scrolled
    # past and the remaining seven were simply never built.
    failed, incomplete = [], []
    for record in records:
        name = record.get("org_name", "?")
        try:
            manifest = package.build(record)
        except Exception as exc:
            failed.append(name)
            print(f"{name:32} FAILED — {exc}")
            continue

        gaps = manifest.get("incomplete") or []
        kit = manifest.get("kit_pdf") or {}
        qr = manifest.get("qr_check") or {}

        if gaps:
            incomplete.append(name)

        bits = [f"{len(manifest.get('files', {}))} files"]
        bits.append("kit PDF " + ("ok" if kit.get("ok") else "MISSING"))
        if qr:
            bits.append("QR " + ("verified" if qr.get("verified") else "unverified"))
        print(f"{name:32} {', '.join(bits)}")
        if not kit.get("ok") and kit.get("error"):
            print(f"{'':32}   {kit['error']}")
        if gaps:
            print(f"{'':32}   still unset: {', '.join(gaps)}")

    print()
    if failed:
        print("Did not build:")
        print("  " + ", ".join(failed))
    if incomplete:
        print("Do not send until the listed fields are filled:")
        print("  " + ", ".join(incomplete))
    if not failed and not incomplete:
        print("All partners complete.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
