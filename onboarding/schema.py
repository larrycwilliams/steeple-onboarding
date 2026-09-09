"""Partner data model, defaults and the org-type vocabulary map.

One source of truth: the intake form, the JSON records, the merge context and
the validators are all generated from FIELDS below.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field

COMPANY_NAME = "Steeple & Stitch Co."
COMPANY_PARENT = "A Division of The Williams Collective"
COMPANY_DOMAIN = "www.steepleandstitch.com"
SUPPORT_EMAIL = "support@steepleandstitch.com"
GOVERNING_STATE = "Ohio"
AGREEMENT_TEMPLATE_VERSION = "2026.09"

# Steeple & Stitch brand colours, sampled from the logo artwork.
SS_NAVY = "102C48"        # primary — headings and section titles
SS_NAVY_DEEP = "0D2742"   # dark fills and cover panels
SS_GOLD = "CB9C52"        # secondary accent — rules, flourishes
SS_LIGHT = "F4F1EC"
SS_MUTED = "7A8089"

KIT_THEMES = ["Steeple & Stitch", "Partner colours"]

PLANS = ["Starter", "Growth", "Multi-Campus"]
ORG_TYPES = ["church", "school", "nonprofit"]
PAYOUT_METHODS = ["Check", "Direct Deposit", "PayPal"]
PAYOUT_FREQUENCIES = ["Quarterly", "Monthly"]


@dataclass
class Field:
    key: str
    label: str
    group: str
    kind: str = "text"          # text | textarea | select | date | money | percent | color | file
    choices: tuple = ()
    default: str = ""
    required: bool = False
    help: str = ""
    placeholder: str = ""


FIELDS: list[Field] = [
    # ---------------------------------------------------------------- identity
    Field("org_name", "Organization name (display)", "Identity", required=True,
          placeholder="Germantown Christian Schools",
          help="How the organization is named in the Launch Kit and marketing copy."),
    Field("org_legal_name", "Legal name (for the agreement)", "Identity", required=True,
          placeholder="Germantown Christian Schools, Inc.",
          help="Exact legal entity name. Goes on the Services Agreement."),
    Field("org_short", "Short name / initials", "Identity", required=True,
          placeholder="GCS", help="Used throughout the Launch Kit copy."),
    Field("org_type", "Organization type", "Identity", kind="select",
          choices=tuple(ORG_TYPES), default="church", required=True,
          help="Drives the vocabulary swap across every document."),
    Field("est_year", "Established year", "Identity", placeholder="1981",
          help="Optional. Appears in the cover lockup and the alumni copy."),
    Field("mascot", "Mascot (singular)", "Identity", placeholder="Cardinal",
          help="Leave blank for organizations without one."),
    Field("mascot_plural", "Mascot (plural)", "Identity", placeholder="Cardinals"),
    Field("tagline", "Tagline / motto / scripture", "Identity", kind="textarea",
          placeholder="Optional. Approved for use on products."),

    # ---------------------------------------------------------------- contacts
    Field("poc_name", "Primary contact name", "Contacts", required=True),
    Field("poc_title", "Primary contact title", "Contacts"),
    Field("poc_email", "Primary contact email", "Contacts", required=True),
    Field("poc_phone", "Primary contact phone", "Contacts"),
    Field("finance_name", "Finance / payout contact", "Contacts",
          help="Who receives payout questions. Defaults to the primary contact."),
    Field("finance_email", "Finance contact email", "Contacts"),
    Field("social_owner", "Owns social accounts at launch", "Contacts",
          help="Named in the Launch Kit responsibility table."),

    # ---------------------------------------------------------------- address
    Field("address_line1", "Street address", "Address", required=True),
    Field("address_line2", "Suite / unit", "Address"),
    Field("city", "City", "Address", required=True),
    Field("state", "State", "Address", default="OH", required=True),
    Field("zip", "ZIP", "Address", required=True),
    Field("shipping_same", "Shipping same as billing", "Address", kind="select",
          choices=("yes", "no"), default="yes"),
    Field("ship_address_line1", "Shipping street", "Address"),
    Field("ship_city", "Shipping city", "Address"),
    Field("ship_state", "Shipping state", "Address"),
    Field("ship_zip", "Shipping ZIP", "Address"),

    # ------------------------------------------------------------- commercial
    Field("plan", "Service tier", "Commercial", kind="select",
          choices=tuple(PLANS), default="Starter", required=True),
    Field("setup_fee", "One-time setup fee", "Commercial", kind="money",
          default="0", required=True, help="Dollars. Section 2 of the agreement."),
    Field("monthly_fee", "Monthly platform fee", "Commercial", kind="money",
          default="0", required=True),
    Field("margin_pct", "Organization margin %", "Commercial", kind="percent",
          default="10", required=True,
          help="Negotiated per organization. Paid against POD cost basis."),
    Field("payout_method", "Payout method", "Commercial", kind="select",
          choices=tuple(PAYOUT_METHODS), default="Check", required=True),
    Field("payout_frequency", "Payout frequency", "Commercial", kind="select",
          choices=tuple(PAYOUT_FREQUENCIES), default="Quarterly", required=True),
    Field("payout_account", "Payout account / mailing info", "Commercial",
          kind="textarea", help="Bank detail or the address checks are mailed to."),
    Field("fund_designation", "Designation for funds", "Commercial",
          placeholder="General fund", help="e.g. general fund, athletics, tuition assistance."),

    # ------------------------------------------------------------------ store
    Field("collection_handle", "Shopify collection handle", "Store", required=True,
          placeholder="gcs-cardinals",
          help="The live collection slug. Used to build the store URL."),
    # The vendor string is the load-bearing field in the whole store. Every
    # collection is a VENDOR EQUALS smart rule, so this string IS the partner's
    # storefront assignment. It is stored rather than derived from org_name
    # because the two legitimately differ -- this app holds "Germantown
    # Christian Schools", Shopify holds "Germantown Christian School" -- and
    # deriving it is the exact mechanism that moved $2,973.76 of one partner's
    # sales onto another. Confirm it once, here, and everything downstream
    # (the collection rule, set_shopify_vendors.py, the product traveler)
    # reads the same confirmed string.
    Field("vendor", "Shopify vendor string", "Store", required=True,
          placeholder="Germantown Christian School",
          help="Must match the product Vendor field in Shopify EXACTLY, including "
               "punctuation and singular/plural. This is what the collection rule "
               "matches on. Not derived from the organization name -- the two "
               "often differ."),
    Field("store_name", "Store name", "Store", placeholder="GCS Cardinals Merch Store",
          help="Blank = generated from the short name."),
    Field("redirect_slug", "QR redirect slug", "Store", placeholder="men-of-faith",
          help="QR codes point at /go/<slug> so printed pieces survive a collection "
               "rename. Lowercase letters, numbers and hyphens — hyphens are fine "
               "and often better (/go/men-of-faith). Spaces and capitals are "
               "tidied automatically."),
    Field("product_lineup", "Launch product lineup", "Store", kind="textarea",
          default="Crew tee, pullover hoodie, performance long sleeve and embroidered cap.",
          help="One sentence. Appears in the store-at-a-glance table and the copy."),
    Field("size_range", "Size range", "Store",
          default="Youth through adult 3XL. (4X & 5X available in some sizes)"),
    Field("fulfillment_days", "Fulfillment window", "Store", default="five to seven days"),

    # ------------------------------------------------------------------ dates
    Field("agreement_date", "Agreement date", "Dates", kind="date", required=True),
    Field("launch_date", "Launch date", "Dates", kind="date",
          help="Leave blank and the kit prints a blank line for them to fill in."),

    # -------------------------------------------------------------- signatures
    Field("company_signer", "Company signer", "Signatures",
          default="Lawrence C. Williams", required=True),
    Field("company_signer_title", "Company signer title", "Signatures",
          default="Owner", required=True),
    Field("client_signer", "Client signer", "Signatures"),
    Field("client_signer_title", "Client signer title", "Signatures"),

    # ------------------------------------------------------------------ brand
    Field("logo_path", "Primary logo file", "Brand", kind="file",
          help="High-res PNG or vector. Used for the QR centre mark and palette sampling."),
    Field("kit_theme", "Launch Week Kit theme", "Brand", kind="select",
          choices=tuple(KIT_THEMES), default="Steeple & Stitch",
          help="Steeple & Stitch navy and gold, or the partner's own sampled palette. "
               "Postcards and the QR always use the partner's colours."),
]

FIELDS_BY_KEY = {f.key: f for f in FIELDS}
GROUPS = list(dict.fromkeys(f.group for f in FIELDS))

PALETTE_ROLES = [
    ("primary", "Primary accent. Headlines, the postcard ticket band, buttons on the store."),
    ("shadow", "Secondary accent and link text on light backgrounds."),
    ("ink", "Backgrounds, garment colour, body copy."),
    ("light", "Light backgrounds and printed cardstock."),
    ("muted", "Captions and secondary text only."),
]

DEFAULT_PALETTE = [
    {"name": "Primary", "hex": "1B222C", "role": PALETTE_ROLES[0][1]},
    {"name": "Shadow", "hex": "3A4450", "role": PALETTE_ROLES[1][1]},
    {"name": "Ink", "hex": "11161D", "role": PALETTE_ROLES[2][1]},
    {"name": "Chalk", "hex": "F4F1EC", "role": PALETTE_ROLES[3][1]},
    {"name": "Steel", "hex": "8A9099", "role": PALETTE_ROLES[4][1]},
]


# --------------------------------------------------------------------------
# Org-type vocabulary. One template set, three voices.
# --------------------------------------------------------------------------
VOCAB = {
    "church": {
        "org_word": "church",
        "org_word_title": "Church",
        "audience": "congregation",
        "audience_plural": "members",
        "audience_address": "Church family",
        "leaders": "Church Leadership",
        "office": "church office",
        "announcement_slot": "Sunday announcement",
        "announcement_verb": "announced from the platform",
        "newsletter": "bulletin",
        "newsletter_title": "Bulletin or newsletter blurb",
        "gathering": "service",
        "gathering_plural": "services",
        "big_event": "Sunday",
        "big_event_title": "Sunday",
        "subgroups": "ministries",
        "subgroup_examples": "youth, men's and women's, worship, campuses",
        "alumni_word": "longtime members",
        "supports": "supports our ministry",
        "supports_short": "supports the ministry",
        "pa_context": "announcement slide before service",
        "roster_word": "small group and ministry lists",
        "extra_collection_1": "Youth ministry collection",
        "extra_collection_2": "Worship and creative arts collection",
        "event_plural": "services",
        "audience_all": "the whole congregation",
        "audience_gather": "people gather after service",
        "ahead_of": "the congregation",
        "signoff_default": "See you Sunday.",
    },
    "school": {
        "org_word": "school",
        "org_word_title": "School",
        "audience": "families",
        "audience_plural": "families",
        "audience_address": "Families",
        "leaders": "Leadership",
        "office": "front office",
        "announcement_slot": "Morning announcement or chapel read",
        "announcement_verb": "read in morning announcements",
        "newsletter": "newsletter",
        "newsletter_title": "Newsletter or bulletin blurb",
        "gathering": "chapel",
        "gathering_plural": "chapels",
        "big_event": "game day",
        "big_event_title": "Game day",
        "subgroups": "programs",
        "subgroup_examples": "athletics, fine arts, staff, alumni",
        "alumni_word": "alumni",
        "supports": "supports the school",
        "supports_short": "supports the school",
        "pa_context": "PA read between quarters or sets",
        "roster_word": "team, class and parent lists",
        "extra_collection_1": "Athletics sub-collection, by team",
        "extra_collection_2": "Fine arts and music sub-collection",
        "event_plural": "games",
        "audience_all": "all families",
        "audience_gather": "families wait at pickup",
        "ahead_of": "families",
        "signoff_default": "Thank you!",
    },
    "nonprofit": {
        "org_word": "organization",
        "org_word_title": "Organization",
        "audience": "supporters",
        "audience_plural": "supporters",
        "audience_address": "Friends",
        "leaders": "Leadership",
        "office": "main office",
        "announcement_slot": "Event announcement",
        "announcement_verb": "shared at your next event",
        "newsletter": "newsletter",
        "newsletter_title": "Newsletter blurb",
        "gathering": "event",
        "gathering_plural": "events",
        "big_event": "event day",
        "big_event_title": "Event day",
        "subgroups": "programs",
        "subgroup_examples": "programs, chapters, volunteer teams",
        "alumni_word": "longtime supporters",
        "supports": "supports our mission",
        "supports_short": "supports the mission",
        "pa_context": "announcement from the stage",
        "roster_word": "volunteer and supporter lists",
        "extra_collection_1": "Program-specific collection",
        "extra_collection_2": "Volunteer and staff collection",
        "event_plural": "events",
        "audience_all": "all supporters",
        "audience_gather": "people gather",
        "ahead_of": "supporters",
        "signoff_default": "Thank you!",
    },
}


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------
def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower())
    return re.sub(r"-{2,}", "-", value).strip("-")


def money(value) -> str:
    try:
        amount = float(str(value).replace("$", "").replace(",", "") or 0)
    except ValueError:
        return str(value)
    if amount == int(amount):
        return f"{int(amount):,}"
    return f"{amount:,.2f}"


def pretty_date(value: str) -> str:
    if not value:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y"):
        try:
            return _dt.datetime.strptime(value, fmt).strftime("%B %-d, %Y")
        except ValueError:
            continue
    return value


def blank_line(value: str, width: int = 22) -> str:
    """Fill-in value, or an underscore rule the client completes by hand."""
    return value if value else "_" * width


def default_record() -> dict:
    record = {f.key: f.default for f in FIELDS}
    record["palette"] = [dict(c) for c in DEFAULT_PALETTE]
    record["agreement_date"] = _dt.date.today().isoformat()
    return record


def derive(record: dict) -> dict:
    """Build the full merge context: entered fields + derived + vocabulary."""
    ctx = dict(record)
    org_type = (record.get("org_type") or "church").lower()
    if org_type not in VOCAB:
        org_type = "church"
    ctx.update(VOCAB[org_type])
    ctx["org_type"] = org_type
    ctx["is_school"] = org_type == "school"
    ctx["is_church"] = org_type == "church"
    ctx["is_nonprofit"] = org_type == "nonprofit"

    short = record.get("org_short") or record.get("org_name", "")
    handle = slugify(record.get("collection_handle") or "") or slugify(record.get("org_name", ""))
    # Normalise on the way out too, so a record edited outside the app can
    # never put a space or a capital into a printed QR target.
    slug = slugify(record.get("redirect_slug") or "") or slugify(short) or handle

    ctx["org_short"] = short
    ctx["collection_handle"] = handle
    ctx["redirect_slug"] = slug
    ctx["store_url"] = f"https://{COMPANY_DOMAIN}/collections/{handle}"
    ctx["store_url_display"] = f"steepleandstitch.com/collections/{handle}"
    ctx["redirect_url"] = f"https://{COMPANY_DOMAIN}/go/{slug}"
    ctx["redirect_url_display"] = f"steepleandstitch.com/go/{slug}"
    ctx["qr_target"] = ctx["redirect_url"]

    mascot = record.get("mascot") or ""
    mascot_plural = record.get("mascot_plural") or (mascot + "s" if mascot else "")
    # Most organizations have no mascot. Fall back to the short name so no
    # sentence is left with a hole in it.
    ctx["has_mascot"] = bool(mascot)
    ctx["mascot"] = mascot or short
    ctx["mascot_plural"] = mascot_plural or short
    ctx["mascot_lower"] = mascot.lower() if mascot else "logo"
    audience = ctx["audience"]
    ctx["audience_address"] = (
        f"{mascot} {audience}" if mascot else ctx["audience_address"]
    )
    ctx["cheer"] = f"Go {mascot_plural}!" if mascot else ctx["signoff_default"]
    ctx["mark_word"] = f"{mascot.lower()} mark" if mascot else "logo"

    ctx["store_name"] = record.get("store_name") or (
        f"{short} {mascot_plural} Merch Store" if mascot_plural else f"{short} Merch Store"
    )

    lineup = (record.get("product_lineup") or "").strip()
    ctx["product_lineup"] = lineup
    bare = lineup.rstrip(".")
    ctx["product_lineup_short"] = bare[:1].upper() + bare[1:] if bare else ""
    ctx["product_lineup_lower"] = bare[:1].lower() + bare[1:] if bare else ""
    sizes = (record.get("size_range") or "").strip()
    ctx["size_range"] = sizes
    inline = sizes.split("(")[0].strip().rstrip(".")
    ctx["size_range_inline"] = inline[:1].lower() + inline[1:] if inline else ""

    ctx["setup_fee_fmt"] = money(record.get("setup_fee"))
    ctx["monthly_fee_fmt"] = money(record.get("monthly_fee"))
    ctx["margin_pct_fmt"] = str(record.get("margin_pct", "")).replace("%", "").strip()

    ctx["agreement_date_fmt"] = pretty_date(record.get("agreement_date", ""))
    ctx["launch_date_fmt"] = blank_line(pretty_date(record.get("launch_date", "")))
    ctx["has_launch_date"] = bool(record.get("launch_date"))

    parts = [record.get("address_line1", ""), record.get("address_line2", "")]
    street = ", ".join(p for p in parts if p)
    ctx["address_full"] = (
        f"{street}, {record.get('city','')}, {record.get('state','')} {record.get('zip','')}"
    ).strip(", ")
    if (record.get("shipping_same") or "yes") == "yes":
        ctx["shipping_full"] = ctx["address_full"]
    else:
        ctx["shipping_full"] = (
            f"{record.get('ship_address_line1','')}, {record.get('ship_city','')}, "
            f"{record.get('ship_state','')} {record.get('ship_zip','')}"
        ).strip(", ")

    ctx["poc_line"] = " · ".join(
        p for p in [record.get("poc_name", ""), record.get("poc_title", "")] if p
    )
    ctx["poc_contact"] = " · ".join(
        p for p in [record.get("poc_email", ""), record.get("poc_phone", "")] if p
    )
    ctx["finance_name"] = record.get("finance_name") or record.get("poc_name", "")
    ctx["finance_email"] = record.get("finance_email") or record.get("poc_email", "")
    ctx["poc_contact_line"] = "   ·   ".join(
        [blank_line(record.get("poc_name", "")), blank_line(record.get("poc_email", ""))]
    )
    ctx["finance_contact_line"] = "   ·   ".join(
        [blank_line(ctx["finance_name"]), blank_line(ctx["finance_email"])]
    )
    ctx["finance_line"] = " · ".join(
        p for p in [ctx["finance_name"], ctx["finance_email"]] if p
    )
    ctx["social_owner_line"] = blank_line(record.get("social_owner", ""))

    ctx["est_line"] = f"Est. {record['est_year']}" if record.get("est_year") else ""
    ctx["has_est"] = bool(record.get("est_year"))

    palette = record.get("palette") or [dict(c) for c in DEFAULT_PALETTE]
    ctx["palette"] = palette
    for i, colour in enumerate(palette[:5]):
        ctx[f"color{i+1}_name"] = colour.get("name", "")
        ctx[f"color{i+1}_hex"] = colour.get("hex", "").upper().lstrip("#")
        ctx[f"color{i+1}_role"] = colour.get("role", "")

    # Launch Week Kit chrome. The kit is a Steeple & Stitch document, so it
    # defaults to the S&S palette; the partner's colours remain an option and
    # still drive the postcards, the QR and the palette table either way.
    if (record.get("kit_theme") or KIT_THEMES[0]) == "Partner colours":
        ctx["theme_accent"] = ctx.get("color1_hex") or SS_NAVY
        ctx["theme_accent_deep"] = ctx.get("color2_hex") or SS_GOLD
        ctx["theme_ink"] = ctx.get("color3_hex") or SS_NAVY_DEEP
        ctx["theme_light"] = ctx.get("color4_hex") or SS_LIGHT
        ctx["theme_muted"] = ctx.get("color5_hex") or SS_MUTED
        ctx["theme_rule"] = ctx.get("color1_hex") or SS_GOLD
    else:
        ctx["theme_accent"] = SS_NAVY
        ctx["theme_accent_deep"] = SS_GOLD
        ctx["theme_ink"] = SS_NAVY_DEEP
        ctx["theme_light"] = SS_LIGHT
        ctx["theme_muted"] = SS_MUTED
        ctx["theme_rule"] = SS_GOLD      # gold flourish under navy headings

    ctx["company_name"] = COMPANY_NAME
    ctx["company_parent"] = COMPANY_PARENT
    ctx["company_footer"] = f"{COMPANY_NAME}  •  {COMPANY_PARENT}"
    ctx["support_email"] = SUPPORT_EMAIL
    ctx["governing_state"] = GOVERNING_STATE
    ctx["template_version"] = AGREEMENT_TEMPLATE_VERSION

    ctx["payout_frequency_lower"] = (record.get("payout_frequency") or "Quarterly").lower()
    ctx["payout_method_lower"] = (record.get("payout_method") or "Check").lower()
    ctx["fund_designation"] = record.get("fund_designation") or "General fund"

    return ctx


# Fields that end up inside a URL. Hyphens are kept -- plenty of partners have
# them in their names -- but spaces, capitals and punctuation are not printable
# in a QR target, so they are normalised rather than silently shipped.
URL_SAFE_FIELDS = ("collection_handle", "redirect_slug")


def normalize(record: dict) -> list[str]:
    """Clean URL fields in place. Returns a note for anything that changed."""
    notes = []
    for key in URL_SAFE_FIELDS:
        raw = (record.get(key) or "").strip()
        if not raw:
            continue
        clean = slugify(raw)
        if clean != raw:
            record[key] = clean
            notes.append(f"{FIELDS_BY_KEY[key].label}: “{raw}” → “{clean}”")
    return notes


REQUIRED_KEYS = [f.key for f in FIELDS if f.required]


def validate(record: dict) -> list[str]:
    problems = []
    for key in REQUIRED_KEYS:
        if not str(record.get(key, "")).strip():
            problems.append(f"{FIELDS_BY_KEY[key].label} is required")
    for key in URL_SAFE_FIELDS:
        value = record.get(key, "")
        if value and value != slugify(value):
            problems.append(
                f"{FIELDS_BY_KEY[key].label} must be lowercase letters, numbers "
                f"and hyphens only — “{value}” is not usable in a URL"
            )
    try:
        pct = float(str(record.get("margin_pct", "0")).replace("%", ""))
        if not 0 <= pct <= 100:
            problems.append("Margin % must be between 0 and 100")
    except ValueError:
        problems.append("Margin % must be a number")
    return problems
