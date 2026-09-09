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

CLONE_FLAGS = [
    ("--pod-cost N",  "The POD app pushed cost = retail (Printify does this). Supply the true cost."),
    ("--price N",     "Override retail across all variants."),
    ("--keep-images", "Copy the POD mockups instead of starting empty."),
    ("--no-flip",     "Leave the clone DRAFT and the POD half untouched."),
]

PHASES = [
    {
        "mark": "A", "title": "Build the POD half", "where": "POD platform",
        "steps": [
            {
                "key": "build",
                "title": "Build the product with the full colour and size run",
                "body": "Create it in the POD app. Choose every colourway and size you intend "
                        "to sell — not a subset you plan to extend later. Generate mockups.",
                "why": "<b>Why the full run:</b> the in-house twin inherits this grid. If the POD "
                       "half is narrower, the flip guard blocks the capacity valve later — exactly "
                       "when you need it.",
                "shot": "variant-grid",
                "shot_hint": "The POD app's variant grid with every colour and size selected.",
            },
            {
                "key": "price",
                "title": "Set retail price here — not in Shopify",
                "body": "Price to the standard ladder inside the POD app.",
                "ladder": True,
                "stop": "<b>Order matters.</b> The POD app is upstream of Shopify. A price set in "
                        "Shopify is overwritten on the next sync; a price set in the POD app "
                        "survives. Confirmed the hard way on 2026-09-08.",
                "shot": "pod-pricing",
                "shot_hint": "The POD app's pricing screen with the ladder entered.",
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
                "title": "Set Vendor to the exact partner org string",
                "body": "Product organization card, right-hand column. Type it exactly — the "
                        "match is literal.",
                "vendor_box": True,
                "why": "<b>Vendor is the one load-bearing field.</b> Every storefront collection is "
                       "a <code>VENDOR EQUALS</code> smart rule, so vendor <em>is</em> the "
                       "collection assignment. Printify writes <code>Printify</code>; Ninja POD "
                       "leaves <code>Steeple &amp; Stitch Co.</code> Neither matches an org, so "
                       "neither lands anywhere.",
                "shot": "product-organization",
                "shot_hint": "Shopify product page → Product organization card, corrected.",
            },
            {
                "key": "vendor-check",
                "title": "Confirm the vendor took",
                "body": "Check the product carries no <code>needs-vendor-fix</code> tag. The Flow "
                        "workflow of that name tags anything created with a vendor matching no "
                        "known org.",
                "shot": "needs-vendor-fix",
                "shot_hint": "Products list filtered on the needs-vendor-fix tag, returning nothing.",
            },
            {
                "key": "product-id",
                "title": "Record the product ID",
                "body": "The last segment of the admin URL. Paste it into the run details above "
                        "and the clone command below fills itself in.",
            },
        ],
    },
    {
        "mark": "C", "title": "Clone the in-house half", "where": "Terminal · pod2twc",
        "steps": [
            {
                "key": "dry-run",
                "title": "Dry run the clone and read the plan",
                "cmd": "dry",
                "body": "<code>--cost</code> is your <b>fully loaded</b> in-house cost, labor "
                        "included. It is expected to sit <em>above</em> the POD basis. That is "
                        "deliberate and must not be \"corrected\".",
                "flags": True,
                "shot": "clone-dry-run",
                "shot_hint": "Terminal showing the dry-run plan, including the inventory-location lines.",
            },
            {
                "key": "commit",
                "title": "Commit it",
                "cmd": "commit",
                "body": "The clone lands ACTIVE, the POD half goes DRAFT, and the two are linked "
                        "by metafield. This replaces the entire copy/paste pass.",
                "why": "<b>The step that actually matters</b> happens inside this command: a plain "
                       "Shopify duplicate stays stocked at the POD fulfillment location, so an "
                       "order on the in-house listing routes straight back to the printer — POD "
                       "cost paid on a garment you printed yourself. The script turns tracking off "
                       "<em>first</em>, detaches every POD location, then stocks the house and "
                       "pickup locations.",
            },
        ],
    },
    {
        "mark": "D", "title": "Finish the in-house half", "where": "Shopify admin · Terminal",
        "steps": [
            {
                "key": "mockups",
                "title": "Upload your own mockups",
                "body": "The clone starts with no images by design — POD mockups show the "
                        "printer's render, not your artwork.",
            },
            {
                "key": "exceptions",
                "title": "Record any supplier coverage gaps",
                "cmd": "except",
                "body": "Some gaps are permanent — the supplier simply doesn't make that "
                        "combination. Record them so the guard stays quiet about those and loud "
                        "about everything else.",
                "stop": "<b>Don't train yourself to ignore the guard.</b> Forcing past it every "
                        "time is how a real gap gets shipped.",
            },
            {
                "key": "metafields",
                "title": "Eyeball the metafields on the product page",
                "body": "All six definitions are pinned, so they sit directly on the product page "
                        "rather than behind “Show all”.",
                "metafields": True,
                "why": "<b>POD Basis Cost is not your cost.</b> It's what the printer charges, and "
                       "it's what the partner org's payout is calculated against — whichever half "
                       "fulfills. Your loaded in-house cost lives in Cost per item and is "
                       "deliberately higher.",
                "shot": "pinned-metafields",
                "shot_hint": "Product page metafields, all six pinned fields populated.",
            },
        ],
    },
    {
        "mark": "E", "title": "Verify", "where": "Terminal",
        "steps": [
            {
                "key": "audit",
                "title": "Audit the pair",
                "cmd": "audit",
                "body": "The new pair should report no gaps. Fix anything it flags now — a gap "
                        "found later is a gap found while you're drowning.",
                "shot": "clean-audit",
                "shot_hint": "Terminal showing a clean audit.",
            },
            {
                "key": "live",
                "title": "Confirm the pair is live the right way round",
                "body": "In-house twin <b>ACTIVE</b>, POD half <b>DRAFT</b>. Retail identical on "
                        "both halves — if they disagree, the twin's price is the correct one, and "
                        "the fix goes in at the POD app followed by a republish.",
            },
        ],
    },
]

STEP_KEYS = [s["key"] for p in PHASES for s in p["steps"]]
SHOT_KEYS = [s["shot"] for p in PHASES for s in p["steps"] if s.get("shot")]


# ----------------------------------------------------------------- commands

def commands(run: dict) -> dict:
    """Build the pod2twc commands for this run, filled in where we can.

    Placeholders stay visible rather than collapsing to an empty string, so a
    half-filled run yields a command you can see is unfinished instead of one
    that looks complete and silently targets nothing.
    """
    pid = (run.get("product_id") or "").strip() or "<product-id>"
    org = (run.get("org_key") or "").strip() or "<org>"
    cost = (run.get("cost") or "").strip() or "<cost>"
    ref = (run.get("product_id") or "").strip() or "<ref>"
    base = "$P ~/Dev/pod2twc/pod2twc.py"
    return {
        "prefix": "P=~/.venvs/pod2twc/bin/python",
        "dry":    f"{base} clone {pid} --org {org} --cost {cost}",
        "commit": f"{base} clone {pid} --org {org} --cost {cost} --commit",
        "except": f"{base} except {ref} --add \"White / S\" --commit",
        "audit":  f"{base} audit",
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
