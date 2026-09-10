"""The master documents and brand assets, as an explicit allowlist.

Deliberately NOT a path parameter. The existing /download route is guarded to
store.OUTPUT; widening it to reach the master files would turn a file server
into a read-anything hole the moment the app binds to anything but localhost.
Each entry here is a fixed slug -> fixed path, and nothing else is servable.
"""
from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

def _business_root() -> Path:
    """Locate the 20-Steeple-Stitch folder in iCloud.

    It cannot be derived from this file's position, because the code and the
    documents no longer live together. In the retired iCloud copy the app sat
    inside 20-Steeple-Stitch, so parents[3] happened to land on it. On the hub
    the app is a git clone at ~/Dev/steeple-onboarding, where parents[3] is
    /Users/lw -- every master path would resolve under the home directory and
    all seventeen would report Missing, on a page that still returned 200.
    """
    env = os.environ.get("SS_BUSINESS_ROOT")
    if env and Path(env).is_dir():
        return Path(env)
    icloud = (Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
              / "20-Steeple-Stitch")
    if icloud.is_dir():
        return icloud
    guess = Path(__file__).resolve().parents[3]        # the old in-iCloud layout
    if (guess / "Business-Files").is_dir():
        return guess
    return icloud            # report Missing against the real path, not a guess


SS = _business_root()

CUSTOMER = SS / "Business-Files" / "Customer Packet"
MASTERS = SS / "Business-Files" / "Master Files - Do Not Delete"
BRAND = SS / "21-Brand" / "Graphic-Files"
KIT = SS / "21-Brand" / "Branding-Kit"
SOCIAL = SS / "27-Social Media" / "Launch Kit"

# slug: (label, path, group, note)
ITEMS: dict[str, tuple[str, Path, str, str]] = {
    # ---- what a partner receives -------------------------------------------
    "slick-pdf":      ("Marketing Slick (PDF)", CUSTOMER / "Steeple-Stitch_Marketing_Slick.pdf", "Partner-facing", "Hand this out. 1 page."),
    "slick-docx":     ("Marketing Slick (Word)", CUSTOMER / "Steeple-Stitch_Marketing_Slick.docx", "Partner-facing", "Edit copy here, then re-export."),
    "checklist-pdf":  ("Setup Checklist (PDF)", CUSTOMER / "Steeple-Stitch_Setup_Checklist.pdf", "Partner-facing", "Six stages, three weeks."),
    "checklist-docx": ("Setup Checklist (Word)", CUSTOMER / "Steeple-Stitch_Setup_Checklist.docx", "Partner-facing", ""),
    "agreement-pdf":  ("Service Agreement (PDF)", CUSTOMER / "Steeple-Stitch_Service_Agreement.pdf", "Partner-facing", "Generic. Per-partner copies come from the pipeline."),
    "agreement-docx": ("Service Agreement (Word)", CUSTOMER / "Steeple-Stitch_Service_Agreement.docx", "Partner-facing", ""),
    "catalog-pdf":    ("Catalog Examples (PDF)", CUSTOMER / "Steeple-Stitch_Catalog_Examples.pdf", "Partner-facing", "Three store line-ups by size."),
    "catalog-docx":   ("Catalog Examples (Word)", CUSTOMER / "Steeple-Stitch_Catalog_Examples.docx", "Partner-facing", ""),
    "launchkit-pdf":  ("Live Store Launch Kit (PDF)", CUSTOMER / "Launch Kit" / "Steeple-Stitch_Live_Store_Launch_Kit.pdf", "Partner-facing", ""),
    "promo-pdf":      ("Promo Templates One-Pager (PDF)", CUSTOMER / "Launch Kit" / "Steeple-Stitch_Promo_Templates_OnePager.pdf", "Partner-facing", ""),

    # ---- selling ------------------------------------------------------------
    "leavebehind":    ("Founding-Partner Leave-Behind (PDF)", SOCIAL / "FaceBook Kit" / "2026-08-19_SteepleStitch_Facebook-Launch-Kit" / "sales" / "SteepleStitch_Founding-Partner-Leave-Behind.pdf", "Selling", "Your strongest sales asset. Bring a garment with it."),
    "plans-pricing":  ("Plans & Pricing Sheet (PDF)", SOCIAL / "SteepleStitch_Plans-and-Pricing.pdf", "Selling", ""),

    # ---- brand --------------------------------------------------------------
    "guidelines":     ("Brand Guidelines (Word)", KIT / "Steeple_Stitch_Brand_Guidelines.docx", "Brand", "Palette, type, voice."),
    "logo-primary":   ("Logo — primary, transparent", BRAND / "Steeple_Stitch_Logo_Primary_Transparent_300dpi.png", "Brand", "Light backgrounds."),
    "logo-reversed":  ("Logo — reversed, transparent", BRAND / "Steeple_Stitch_Logo_Reversed_Transparent_300dpi.png", "Brand", "Use on Steeple Navy."),
    "logo-horizontal":("Logo — horizontal lockup", BRAND / "Steeple_Stitch_Logo_Horizontal_Transparent_300dpi.png", "Brand", "Wide formats only."),

    # ---- internal -----------------------------------------------------------
    "business-plan":  ("Business Plan (Word)", MASTERS / "Steeple-Stitch_Business_Plan.docx", "Internal", "Not for partners."),
}

GROUPS = ["Partner-facing", "Selling", "Brand", "Internal"]


def resolve(slug: str) -> Path | None:
    entry = ITEMS.get(slug)
    if not entry:
        return None
    path = entry[1]
    return path if path.is_file() else None


def listing() -> dict[str, list[dict]]:
    """Grouped, with real file state so a missing master is visible rather than
    a dead link."""
    out: dict[str, list[dict]] = {g: [] for g in GROUPS}
    for slug, (label, path, group, note) in ITEMS.items():
        exists = path.is_file()
        out.setdefault(group, []).append({
            "slug": slug, "label": label, "note": note, "exists": exists,
            "name": path.name,
            "kb": round(path.stat().st_size / 1024) if exists else 0,
            "modified": (_dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%d %b %Y")
                         if exists else ""),
            "ext": path.suffix.lstrip(".").upper(),
        })
    for g in out:
        out[g].sort(key=lambda d: d["label"])
    return out


def missing() -> list[str]:
    """Labels of masters that are not where they should be."""
    return [label for (label, path, _group, _note) in ITEMS.values()
            if not path.is_file()]


def root() -> Path:
    """Exposed so /tools can show which folder it is reading."""
    return SS
