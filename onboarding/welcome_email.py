"""The new-partner welcome email, built from the partner record.

Until now this lived in a separate Welcome Builder artifact, which meant
re-typing the church name, store name, tier and giveback percentage into a
second tool for every partner -- values this app already holds. Five of the
template's ten merge fields come straight off the partner record; four are
Larry's own details, entered once in company.json; one is the asset upload
link, global with a per-partner override.

The guardrail from the Builder is kept deliberately: nothing renders while a
merge field is empty, and the screen names which ones. That guard is the
reason a literal ``{{giveback_percent}}`` has never reached a partner.
"""
from __future__ import annotations

import base64
import glob
import mimetypes
from pathlib import Path

import jinja2

from . import schema, settings, store

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "email_templates"
LOGO = ROOT / "assets" / "email" / "logo-knockout.png"
# The whole header as one image: navy field, lockup and gold rule together.
# See the comment in the template for why this is not done with CSS.
HEADER = ROOT / "assets" / "email" / "header-band.png"

SUBJECT = "Welcome aboard, {{ org_name }} — here's what happens next"

# Attached in this order. Descriptor -> what the partner is getting.
ATTACHMENTS = [
    ("Service-Agreement-SIGNABLE", "pdf", "Service Agreement to sign"),
    ("Launch-Week-Kit", "pdf", "Launch Week Kit"),
    ("Postcard-PRINT", "pdf", "Print-ready postcards"),
    ("QR-Branded-Print", "png", "Branded QR code"),
]

# Merge field -> where it comes from, for the screen and for the guard.
FROM_RECORD = {
    "org_name": "Organization name",
    "store_name": "Store name",
    "tier": "Plan",
    "giveback_percent": "Client margin",
    "contact_first_name": "Primary contact name",
}
FROM_SETTINGS = {
    "point_of_contact_name": "Your name (Settings)",
    "point_of_contact_email": "Your email (Settings)",
    "point_of_contact_phone": "Your phone (Settings)",
}
# company_address is deliberately absent. Larry's business runs from his home
# and he does not want that address in a partner's inbox. A welcome email to a
# customer who has just signed is a relationship message, not an advertisement,
# so it needs no physical address. Set one in Settings -- a PO box, when he has
# one -- and the footer line reappears on its own.
# Deliberately not required. With no upload folder configured the email asks
# the partner to reply with their files attached, which is a complete
# instruction on its own -- see the {% if asset_upload_link %} branch in the
# templates. Set it in Settings to get the gold button back.


def _data_uri(path) -> str:
    """Inline an image as base64.

    Embedded rather than hosted: it renders where remote images are blocked,
    and it survives the copy-paste into Apple Mail, which is how these get
    sent.
    """
    if not path.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _header_src(config: dict) -> str:
    hosted = str(config.get("header_url") or "").strip()
    return hosted or _data_uri(HEADER)


def _logo_src(config: dict) -> str:
    """A data URI for the header lockup, or a hosted URL if one is configured.

    Embedded by default: it renders where remote images are blocked and it
    survives the copy-paste into Apple Mail, which is how these actually get
    sent.
    """
    hosted = str(config.get("logo_url") or "").strip()
    if hosted:
        return hosted
    if not LOGO.exists():
        return ""
    encoded = base64.b64encode(LOGO.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def fields(record: dict, config: dict | None = None) -> dict:
    """Every merge field, resolved."""
    config = config or settings.load()

    contact = str(record.get("poc_name") or "").strip()
    first_name = contact.split()[0] if contact else ""

    margin = str(record.get("margin_pct") or "").replace("%", "").strip()

    # The same vocabulary map the documents use. The email used to be written
    # in church language throughout -- congregation, ministry, "your church"
    # -- which reads wrong to the two thirds of the pipeline that is schools
    # and non-profits. Flattening it all to "organization" would have fixed
    # the schools and made it colder for the churches, who are most of the
    # book. org_type already resolves this per partner, so the email now
    # speaks the same three voices the Launch Week Kit does.
    vocab = schema.derive(record)

    return {
        # from the partner record
        "org_name": record.get("org_name", ""),
        "org_word": vocab.get("org_word", "organization"),
        "org_word_title": vocab.get("org_word_title", "Organization"),
        "audience": vocab.get("audience", "supporters"),
        "subgroup_examples": vocab.get("subgroup_examples", ""),
        "store_name": record.get("store_name", ""),
        "tier": record.get("plan", ""),
        "giveback_percent": f"{margin}%" if margin else "",
        "contact_first_name": first_name,
        # Larry's own details, not the partner's -- see settings.py
        "point_of_contact_name": config.get("point_of_contact_name", ""),
        "point_of_contact_email": config.get("point_of_contact_email", ""),
        "point_of_contact_phone": config.get("point_of_contact_phone", ""),
        "company_address": config.get("company_address", ""),
        # a partner may have their own upload folder
        "asset_upload_link": (str(record.get("asset_upload_link") or "").strip()
                              or config.get("asset_upload_link", "")),
        "support_email": config.get("support_email", ""),
        "signoff_name": config.get("signoff_name", ""),
        "signoff_title": config.get("signoff_title", ""),
        "logo_src": _logo_src(config),
        "header_src": _header_src(config),
    }


def missing(record: dict, config: dict | None = None) -> list[str]:
    """Human labels for every merge field still empty.

    A zero margin counts as missing: 0% giveback is the seed value, and it
    would print in the email as a real negotiated term.
    """
    values = fields(record, config)
    gaps = []
    for key, label in {**FROM_RECORD, **FROM_SETTINGS}.items():
        value = str(values.get(key) or "").strip()
        if not value or (key == "giveback_percent" and value in ("0%", "%")):
            gaps.append(label)
    return gaps


def attachments(record: dict) -> list[dict]:
    """The customer-facing files from this partner's latest package."""
    out_dir = store.output_dir(record)
    found = []
    for descriptor, suffix, label in ATTACHMENTS:
        matches = sorted(glob.glob(str(out_dir / f"*_{descriptor}_*.{suffix}")))
        if matches:
            path = Path(matches[-1])
            found.append({
                "label": label,
                "path": str(path),
                "name": path.name,
                "bytes": path.stat().st_size,
                "type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            })
    return found


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=False,          # the HTML template is already escaped by hand
        undefined=jinja2.StrictUndefined,
    )


def render(record: dict, config: dict | None = None) -> dict:
    """Subject, HTML body, plain-text body, attachments and what is missing."""
    config = config or settings.load()
    values = fields(record, config)
    found = attachments(record)

    # The copy describes the attachments, so it has to know which ones are
    # actually there. A partner with no logo gets no branded QR; promising one
    # in the body would be the same class of mistake as the kit that shipped
    # another school's product photo.
    labels = {item["label"] for item in found}
    values.update({
        "has_agreement": "Service Agreement to sign" in labels,
        "has_kit": "Launch Week Kit" in labels,
        "has_postcards": "Print-ready postcards" in labels,
        "has_qr": "Branded QR code" in labels,
        "attachment_count": len(found),
    })

    env = _env()

    return {
        "subject": env.from_string(SUBJECT).render(**values),
        "html": env.get_template("welcome.html").render(**values),
        "text": env.get_template("welcome.txt").render(**values),
        "to": record.get("poc_email", ""),
        "to_name": record.get("poc_name", ""),
        "attachments": found,
        "missing": missing(record, config),
        "fields": values,
    }
