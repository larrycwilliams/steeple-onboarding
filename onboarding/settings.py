"""Larry's own details — the half of the welcome email that never changes.

The Welcome Builder artifact split its form into two zones for a reason: the
church details change every send, and "your details" are set once. That second
zone lives here, in one file, instead of being re-typed into a separate tool
for every partner.

Kept deliberately separate from the partner record. ``poc_name`` on a partner
is *their* contact -- Sarah Allen at GCS. ``point_of_contact_name`` in the
email is *Larry*, under "call or text directly during launch week". Wiring
those two together by their similar names would print the church's contact
next to Larry's phone number.
"""
from __future__ import annotations

import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = ROOT / "company.json"

FIELDS = [
    ("point_of_contact_name", "Your name", "As the partner should see it",
     "Larry Williams"),
    ("point_of_contact_email", "Your email", "The address partners reply to", ""),
    ("point_of_contact_phone", "Your direct phone",
     "Cell. This one earns trust — do not omit it", ""),
    ("company_address", "Mailing address",
     "Optional, and blank on purpose — leave it empty and the footer simply "
     "omits it. A 1:1 welcome to a partner who has just signed is a "
     "relationship message and needs no physical address. IF THIS COPY EVER "
     "MOVES INTO A BULK PLATFORM (Klaviyo, Mailchimp) a physical address and "
     "an unsubscribe link both become legally required — a PO box is the "
     "usual answer.", ""),
    ("asset_upload_link", "Brand asset upload link",
     "Optional. Leave blank and the email asks partners to reply with their "
     "files attached. Fill it with a Dropbox/Drive folder or intake form and "
     "the gold button comes back. A partner record can override it.", ""),
    ("support_email", "Support address", "Orders and fulfilment",
     "support@steepleandstitch.com"),
    ("signoff_name", "Sign-off name", "Bottom of the email", "Larry Williams"),
    ("signoff_title", "Sign-off title", "",
     "Founder · Steeple & Stitch Co."),
]

# Every field above is required in the email except these.
OPTIONAL = {"support_email", "signoff_name", "signoff_title",
            "asset_upload_link", "company_address"}


def defaults() -> dict:
    return {key: default for key, _, _, default in FIELDS}


def load() -> dict:
    """Current settings, with any missing key filled from the defaults."""
    values = defaults()
    if SETTINGS_PATH.exists():
        try:
            values.update(json.loads(SETTINGS_PATH.read_text()))
        except json.JSONDecodeError:
            pass
    return values


def clean(value: str) -> str:
    """Strip invisible formatting characters out of a pasted value.

    Copying a phone number out of Contacts, Messages or a browser wraps it in
    bidi marks (U+202A ... U+202C). They are invisible in every place you
    would think to check -- the form field, the JSON file, a terminal -- and
    then they ride into the email body and into the mailto: and tel: links,
    where they can stop the link working. Same for the non-breaking spaces
    that come with a copied address.
    """
    text = "".join(c for c in str(value) if unicodedata.category(c) != "Cf")
    return text.replace("\u00a0", " ").strip()


def save(values: dict) -> dict:
    current = load()
    for key, _, _, _ in FIELDS:
        if key in values:
            current[key] = clean(values[key])
    SETTINGS_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False))
    return current


def missing() -> list[str]:
    """Labels of the settings that still have to be filled in."""
    values = load()
    return [
        label for key, label, _, _ in FIELDS
        if key not in OPTIONAL and not str(values.get(key) or "").strip()
    ]
