"""Check this Mac has everything a full generate needs, and change nothing.

    ~/.venvs/steeple-onboarding/bin/python tools/preflight.py

Run it before a regenerate. Every line is OK, WARN or FAIL:

    OK    ready
    WARN  works, but the output is degraded in a way worth knowing about
    FAIL  something will not be produced at all

Exits non-zero if anything FAILed.
"""
from __future__ import annotations

import importlib
import platform
import sys
import warnings
from pathlib import Path

# macOS system Python links LibreSSL, so urllib3 prints a NotOpenSSLWarning on
# import. It is noise here and it buries the actual report.
warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    ("flask", "the app itself"),
    ("docx", "reading and writing .docx"),
    ("docxtpl", "merging partner data into templates"),
    ("jinja2", "template rendering"),
    ("segno", "QR codes"),
    ("PIL", "postcards and logo handling"),
    ("numpy", "palette sampling"),
    ("requests", "optional Shopify lookups"),
    ("reportlab", "the signable Service Agreement PDF"),
]

rows: list[tuple[str, str, str]] = []


def add(status: str, label: str, detail: str = "") -> None:
    rows.append((status, label, detail))


# 3.9 is what macOS ships and what the venv is built on. Every module carries
# `from __future__ import annotations`, so the modern type syntax in the
# signatures is never evaluated, and nothing uses match statements or runtime
# unions. Gating on 3.10 was wrong -- the real gate is whether the app's own
# modules import, checked below.
MIN_PYTHON = (3, 9)

APP_MODULES = [
    "onboarding.schema", "onboarding.store", "onboarding.merge",
    "onboarding.package", "onboarding.qr", "onboarding.palette",
    "onboarding.postcard", "onboarding.imaging", "onboarding.docx_tools",
    "onboarding.agreement_pdf", "onboarding.docx_pdf",
    "onboarding.settings", "onboarding.welcome_email", "onboarding.mail_draft",
    "onboarding.shopify_sales", "onboarding.dashboard",
]


def check_python() -> None:
    version = ".".join(str(p) for p in sys.version_info[:3])
    status = "OK" if sys.version_info >= MIN_PYTHON else "FAIL"
    where = (f"on {platform.machine()} macOS" if sys.platform == "darwin"
             else f"on {sys.platform}")
    add(status, f"Python {version}", where if status == "OK"
        else f"{where} — needs 3.9 or newer")


def check_app_modules() -> None:
    """The real gate: does this app load on this interpreter."""
    broken = []
    for module in APP_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            broken.append(f"{module.split('.')[-1]}: {exc}")
    if broken:
        add("FAIL", "app modules", broken[0])
        for extra in broken[1:]:
            add("FAIL", "", extra)
    else:
        add("OK", "app modules", f"all {len(APP_MODULES)} load")


def check_packages() -> None:
    for module, why in REQUIRED:
        try:
            importlib.import_module(module)
            add("OK", module, why)
        except ImportError:
            add("FAIL", module, f"missing — {why}. Re-run run.command to rebuild "
                                "the environment.")
    try:
        importlib.import_module("cv2")
        add("OK", "opencv", "branded QR codes are machine-verified")
    except ImportError:
        add("WARN", "opencv", "not installed — QR codes still generate, but "
                              "scan-test them by hand")


def check_pdf_engine() -> None:
    try:
        from onboarding import docx_pdf
    except Exception as exc:
        add("FAIL", "Launch Week Kit PDF", f"cannot load docx_pdf: {exc}")
        return
    engine = docx_pdf.available_engine()
    if engine:
        add("OK", "Launch Week Kit PDF", f"will convert with {engine}")
    else:
        add("FAIL", "Launch Week Kit PDF", "no converter found — install with: "
                                           "brew install --cask libreoffice")


def check_fonts() -> None:
    try:
        from onboarding import agreement_pdf
    except Exception as exc:
        add("FAIL", "Agreement PDF fonts", f"cannot load agreement_pdf: {exc}")
        return
    for family in ("Georgia", "Calibri"):
        agreement_pdf._register_family(family)
    for requested, resolved in agreement_pdf.font_report().items():
        if resolved.lower().startswith(requested.lower()):
            add("OK", f"font {requested}", "the real font")
        elif "fallback" in resolved:
            add("WARN", f"font {requested}",
                f"{resolved} — the agreement PDF will not match the .docx on "
                "the page. Drop the .ttf into assets/fonts/ to fix.")
        else:
            add("OK", f"font {requested}",
                f"{resolved} — metric-compatible, line breaks unchanged")


def check_email() -> None:
    """Can a welcome email actually go out from this Mac."""
    try:
        from onboarding import mail_draft, settings
    except Exception as exc:
        add("FAIL", "welcome email", str(exc))
        return

    gaps = settings.missing()
    if gaps:
        add("WARN", "your details",
            f"{len(gaps)} unset ({', '.join(gaps)}) — fill them on the Settings "
            "page before emailing a partner")
    else:
        add("OK", "your details", "complete")

    if mail_draft.available():
        add("OK", "Apple Mail", f"found at {mail_draft._found_at()}")
    else:
        add("WARN", "Apple Mail", "not found — use Download .html instead")


def check_dashboard() -> None:
    try:
        from onboarding import dashboard, shopify_sales, store
    except Exception as exc:
        add("FAIL", "dashboard", str(exc))
        return

    snapshot, source = shopify_sales.snapshot()
    if snapshot is None:
        add("FAIL", "Shopify data", source)
        return

    live = shopify_sales.configured()
    add("OK" if live else "WARN", "Shopify data",
        f"live pulls enabled ({source})" if live else
        f"{source} — connect the app from Settings → Shopify connection "
        "to pull live figures")

    records = store.list_partners()
    data = dashboard.rollup(snapshot, records)

    if data["collisions"]:
        for c in data["collisions"]:
            add("WARN", "vendor collision",
                f"{c['vendor']!r} credited to {c['winner']} over "
                f"{', '.join(c['others'])}")
    if data["unmatched"]:
        for row in data["unmatched"]:
            add("WARN", "unattributed sales",
                f"{row['vendor_label']} — ${row['revenue']:.2f} credited to no partner")
    if data["totals"].get("estimated_revenue"):
        add("WARN", "deleted variants",
            f"${data['totals']['estimated_revenue']:.2f} of revenue sold on "
            "variants since deleted — estimated from surviving ones; owed rises "
            f"to ${data['totals']['payout_with_estimates']:.2f} if you accept them")
    if data["totals"]["uncosted_revenue"]:
        add("WARN", "cost of goods",
            f"${data['totals']['uncosted_revenue']:.2f} of revenue has no cost "
            "recorded — payouts are floors until it is filled in")
    orphans = data["catalog"]["orphan_count"]
    if orphans:
        add("WARN", "orphan products",
            f"{orphans} active product(s) in no partner collection")
    add("OK", "payouts owed", f"${data['totals']['payout']:.2f} across "
                              f"{len(data['partners'])} partner(s)")


def check_content() -> None:
    templates = sorted((ROOT / "docx_templates").glob("*.docx"))
    add("OK" if len(templates) == 4 else "FAIL", "templates",
        f"{len(templates)} of 4 in docx_templates/")

    try:
        from onboarding import store
    except Exception as exc:
        add("FAIL", "partner records", str(exc))
        return
    partners = store.list_partners()
    ready = [r for r in partners if not store.readiness(r)]
    add("OK", "partner records", f"{len(partners)} on file, {len(ready)} complete")
    for record in partners:
        gaps = store.readiness(record)
        if gaps:
            add("WARN", record.get("org_name", "?"),
                f"{len(gaps)} field(s) unset — will generate, do not send")


def main() -> int:
    print("Steeple & Stitch onboarding — preflight\n")
    check_python()
    check_packages()
    check_app_modules()
    check_pdf_engine()
    check_fonts()
    check_email()
    check_dashboard()
    check_content()

    width = max(len(label) for _, label, _ in rows) + 2
    for status, label, detail in rows:
        print(f"  {status:5} {label:<{width}} {detail}")

    fails = sum(1 for s, _, _ in rows if s == "FAIL")
    warns = sum(1 for s, _, _ in rows if s == "WARN")
    print()
    if fails:
        print(f"{fails} blocking problem(s). Fix those, then run this again.")
    elif warns:
        print(f"Ready to generate. {warns} warning(s) above — read them once.")
    else:
        print("Ready to generate.")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
