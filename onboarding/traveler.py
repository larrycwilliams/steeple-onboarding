"""Product Pair Traveler — the shop checklist for adding a product pair.

Every sellable product is a matched pair: the POD-app listing and its in-house
"TWC" duplicate. This module holds the checklist itself, the per-product run
records, and the shared screenshot library.

Three things live here rather than in the template:

1.  STEP CONTENT IS DATA. The template loops over PHASES, so the step list,
    the progress denominator and the printed sheet cannot drift apart.

2.  THE ORG TABLE COMES FROM THE PARTNER RECORDS. Shopify collections are
    `VENDOR EQUALS` smart rules, so the vendor string has to match the store
    exactly, and partner *record* names do not always match it ("Germantown
    Christian Schools" here vs "Germantown Christian School" in Shopify). That
    string is now a confirmed field on each partner record, so it is read from
    there rather than kept as a second hand-maintained list. FALLBACK_ORGS
    below is only used when no record carries a vendor yet.

3.  RUNS ARE SERVER-SIDE. The app gets opened from an iPad over Tailscale as
    well as on the Mac, so run progress is stored as JSON next to the partner
    records rather than in one browser's localStorage.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "product_runs"
SHOTS = ROOT / "assets" / "_traveler"

SHOT_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# Only used when no partner record carries a vendor string — see note 2.
# Mirrors ~/Dev/pod2twc/config.json.
FALLBACK_ORGS = [
    {"key": "gcs",                 "vendor": "Germantown Christian School",  "collection": "gcs-cardinals"},
    {"key": "gcs-athletics",       "vendor": "GCS Athletics",                "collection": "gcs-athletics"},
    {"key": "revive",              "vendor": "Revive Church",                "collection": "revive-church"},
    {"key": "fhcog",               "vendor": "Free Holiness Church of God",  "collection": "free-holiness-church-of-god"},
    {"key": "hoh",                 "vendor": "Highway of Holiness",          "collection": "highway-of-holiness"},
    {"key": "community-christian", "vendor": "Community Christian",          "collection": "community-christian-apparel"},
    {"key": "men-of-faith",        "vendor": "The Men of Faith",             "collection": "the-men-of-faith"},
    {"key": "alt-west",            "vendor": "ALT-WEST",                     "collection": "alt-west"},
    {"key": "steeple-stitch",      "vendor": "Steeple & Stitch Co.",         "collection": "steeple-stitch"},
]

POD_PLATFORMS = ["Ninja POD", "Printify", "Printful", "Gelato"]

PRICE_LADDER = [("S–XL", "22.00"), ("2XL", "24.00"), ("3XL", "25.00"), ("4XL", "26.00")]

# Metafield definitions as pinned on the product page, for the eyeball check.
PINNED_METAFIELDS = [
    ("1", "Church Store",             "the partner org's collection"),
    ("3", "Church Store Name",        "the org's display name"),
    ("4", "Church Store URL",         "the org's collection URL"),
    ("5", "POD Source Product",       "the POD half of this pair"),
    ("6", "POD Basis Cost",           "the POD charge — the payout basis"),
    ("7", "Pair Coverage Exceptions", "blank, or accepted gaps"),
]

# Options the Clone panel exposes. Same flags as the CLI; the panel sets them.
CLONE_FLAGS = [
    ("POD cost override", "--pod-cost N", "The POD app pushed cost = retail (Printify does this). Supply the true cost."),
    ("Price override",    "--price N",    "Override retail across all variants. Normally leave blank — retail must match the POD half."),
    ("Keep POD mockups",  "--keep-images", "On by default in the app. Untick to start the clone with no images."),
    ("Leave as draft",    "--no-flip",     "Leave the clone DRAFT and the POD half untouched."),
]

# 2026-09-30 (doc 46): collapsed from 13 steps to 6. The clone is a button now,
# so the steps that were "run this command" -- dry run, commit, except, audit --
# became the Clone panel or moved to the Terminal tools card at the bottom.
# What is left is what is genuinely manual. Step keys that survived keep their
# names so old runs keep their ticks.
PHASES = [
    {
        "mark": "A", "title": "Build the POD half", "where": "POD platform",
        "steps": [
            {
                "key": "build",
                "title": "Build it with the full colour and size run, priced to the ladder",
                "body": "Create it in the POD app. Choose every colourway and size you intend "
                        "to sell — not a subset you plan to extend later. Set retail here, in "
                        "the POD app, to the standard ladder. Generate mockups.",
                "ladder": True,
                "why": "<b>Why the full run:</b> the in-house twin inherits this grid. If the POD "
                       "half is narrower, the flip guard blocks the capacity valve later — exactly "
                       "when you need it.",
                "stop": "<b>Price in the POD app, not Shopify.</b> The POD app is upstream: a "
                        "price set in Shopify is overwritten on the next sync. Confirmed the hard "
                        "way on 2026-09-08.",
                "shot": "variant-grid",
                "shot_hint": "The POD app's variant grid with every colour and size selected.",
            },
            {
                "key": "publish",
                "title": "Publish to Shopify",
                "body": "Publish from the POD app. If you edit anything after this — price "
                        "especially — you must <em>republish</em>, not just save. A price change "
                        "alone does not sync.",
            },
        ],
    },
    {
        "mark": "B", "title": "Attribute the POD half", "where": "Shopify admin",
        "steps": [
            {
                "key": "vendor",
                "title": "Set Vendor to the exact partner string, and copy the product ID",
                "body": "Product organization card, right-hand column — type it exactly, the "
                        "match is literal. Then paste the product ID (last segment of the admin "
                        "URL) into Run details above.",
                "vendor_box": True,
                "why": "<b>Vendor is the one load-bearing field.</b> Every storefront collection is "
                       "a <code>VENDOR EQUALS</code> smart rule, so vendor <em>is</em> the "
                       "collection assignment. Printify writes <code>Printify</code>; Ninja POD "
                       "leaves <code>Steeple &amp; Stitch Co.</code> Neither lands anywhere. The "
                       "Clone preview warns if this was skipped.",
                "shot": "product-organization",
                "shot_hint": "Shopify product page → Product organization card, corrected.",
            },
        ],
    },
    {
        "mark": "C", "title": "Clone the in-house half", "where": "This app",
        "steps": [
            {
                "key": "clone",
                "title": "Preview the clone, read the plan, commit",
                "clone_panel": True,
                "body": "Preview is a dry run — nothing is written. Commit builds the in-house "
                        "twin: it lands ACTIVE, the POD half goes DRAFT, the two are linked by "
                        "metafield, and this step ticks itself.",
                "why": "<b>The step that actually matters</b> happens inside the commit: a plain "
                       "Shopify duplicate stays stocked at the POD fulfillment location, so an "
                       "order on the in-house listing routes straight back to the printer. The "
                       "clone turns tracking off, stocks the house location, detaches every POD "
                       "location, and carries the real weights over — the three things a hand "
                       "duplicate gets wrong.",
            },
        ],
    },
    {
        "mark": "D", "title": "Finish and check", "where": "Shopify admin",
        "steps": [
            {
                "key": "mockups",
                "title": "Check the mockups — replace with your own if you have them",
                "body": "The clone copies the POD app's mockups by default (untick “Keep POD "
                        "mockups” before Preview to start empty). They are the printer's render "
                        "of your design — fine to sell from; swap in your own shots when you "
                        "have them.",
            },
            {
                "key": "live",
                "title": "Check the pair on the product page",
                "body": "In-house twin <b>ACTIVE</b>, POD half <b>DRAFT</b>, retail identical on "
                        "both. The six pinned metafields sit directly on the product page:",
                "metafields": True,
                "why": "<b>POD Basis Cost is not your cost.</b> It's what the printer charges, and "
                       "what the partner payout is calculated against. Your loaded in-house cost "
                       "lives in Cost per item and is deliberately higher. If retail disagrees "
                       "between the halves, the fix goes in at the POD app, then republish.",
                "shot": "pinned-metafields",
                "shot_hint": "Product page metafields, all six pinned fields populated.",
            },
        ],
    },
]

STEP_KEYS = [s["key"] for p in PHASES for s in p["steps"]]
SHOT_KEYS = [s["shot"] for p in PHASES for s in p["steps"] if s.get("shot")]


# ----------------------------------------------------------------- commands

def commands(run: dict) -> dict:
    """The pod2twc commands still run by hand (flip, except, audit).

    Clone is a button now. These use the app's own venv -- pod2twc needs only
    `requests`, so ~/.venvs/pod2twc was never needed -- and pod2twc reads this
    app's .env for the token, so nothing else needs setting up.

    The org is resolved through the vendor string, never passed straight
    through: the traveler's key for Haven of Hope is "haven-of-hope", and
    pod2twc's "hoh" is Highway of Holiness.
    """
    from onboarding import clone
    ref = (run.get("product_id") or "").strip() or "<ref>"
    org = clone.pod_org(run)[0] or "<org>"
    base = "$P ~/Dev/pod2twc/pod2twc.py"
    return {
        "prefix": "P=~/.venvs/steeple-onboarding-312/bin/python",
        "org": org,
        "except": f"{base} except {ref} --add \"White / S\" --commit",
        "audit":  f"{base} audit",
        "flip_dry": f"{base} flip {ref} --to pod",
        "flip":     f"{base} flip {ref} --to pod --commit",
    }


def orgs() -> list[dict]:
    """Partner orgs and their confirmed Shopify vendor strings.

    Read from the partner records so there is one confirmed vendor string per
    partner. Falls back to the static table only when no record has a vendor
    set — a fresh install, or before tools/backfill_vendors.py has run.
    """
    try:
        from onboarding import storefront
        rows = storefront.vendor_map()
    except Exception:
        rows = []
    return rows or FALLBACK_ORGS


def org_by_key(key: str) -> dict | None:
    for org in orgs():
        if org["key"] == key:
            return org
    return None


# --------------------------------------------------------------------- runs

def _slug(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", (text or "").strip()).strip("-").lower()
    return text[:60] or "untitled"


def new_run(title: str = "", org_key: str = "", platform: str = "") -> dict:
    now = datetime.now().isoformat(timespec="seconds")
    return {
        "id": f"{datetime.now():%Y%m%d}-{_slug(title)}-{uuid.uuid4().hex[:6]}",
        "title": title.strip(),
        "org_key": org_key,
        "platform": platform,
        "product_id": "",
        "cost": "",
        "notes": "",
        "steps": {},
        "created": now,
        "updated": now,
    }


def _path(run_id: str) -> Path:
    # Runs are addressed by a generated id, but the id still reaches this
    # function from a URL. Refuse anything that could climb out of the folder.
    if not re.fullmatch(r"[A-Za-z0-9._-]+", run_id or ""):
        raise ValueError(f"unsafe run id: {run_id!r}")
    return RUNS / f"{run_id}.json"


def save(run: dict) -> dict:
    RUNS.mkdir(parents=True, exist_ok=True)
    run["updated"] = datetime.now().isoformat(timespec="seconds")
    _path(run["id"]).write_text(json.dumps(run, indent=2), encoding="utf-8")
    return run


def load(run_id: str) -> dict | None:
    try:
        path = _path(run_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def delete(run_id: str) -> bool:
    try:
        path = _path(run_id)
    except ValueError:
        return False
    if path.exists():
        path.unlink()
        return True
    return False


def list_runs() -> list[dict]:
    if not RUNS.exists():
        return []
    runs = []
    for path in RUNS.glob("*.json"):
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    runs.sort(key=lambda r: r.get("updated", ""), reverse=True)
    return runs


def progress(run: dict) -> tuple[int, int]:
    steps = run.get("steps") or {}
    return sum(1 for k in STEP_KEYS if steps.get(k)), len(STEP_KEYS)


def is_complete(run: dict) -> bool:
    done, total = progress(run)
    return done == total


# -------------------------------------------------------------- screenshots

def shot_path(key: str) -> Path | None:
    """The stored screenshot for a step, if one has been uploaded.

    Screenshots are shared across runs, not stored per run — the admin screen
    for step 4 looks the same on every product, and re-uploading it each time
    would be busywork nobody does.
    """
    if key not in SHOT_KEYS:
        return None
    for ext in SHOT_EXTS:
        candidate = SHOTS / f"{key}{ext}"
        if candidate.exists():
            return candidate
    return None


def save_shot(key: str, filename: str, data: bytes) -> Path | None:
    if key not in SHOT_KEYS:
        return None
    ext = Path(filename).suffix.lower()
    if ext not in SHOT_EXTS:
        return None
    SHOTS.mkdir(parents=True, exist_ok=True)
    for old in SHOTS.glob(f"{key}.*"):   # one image per step; replace, don't pile up
        old.unlink()
    dest = SHOTS / f"{key}{ext}"
    dest.write_bytes(data)
    return dest


def delete_shot(key: str) -> bool:
    removed = False
    if key in SHOT_KEYS and SHOTS.exists():
        for old in SHOTS.glob(f"{key}.*"):
            old.unlink()
            removed = True
    return removed


def shots_present() -> dict[str, bool]:
    return {key: shot_path(key) is not None for key in SHOT_KEYS}
