"""The discovery-call reply, built from a lead.

The counterpart to `welcome_email`: that one fires after the agreement is
signed and is built from a partner record; this one fires the moment somebody
fills in the Book a Discovery Call form and is built from a lead. Same brand
kit, same Apple Mail draft mechanism, same rule that nothing renders while a
merge field is empty.

Why it exists at all
--------------------
The copy has been sitting in the project as `04-discovery-call-reply-email.html`
since before the pipeline did, and the setup notes ranked hand-sending it third
behind Klaviyo and Shopify Flow -- fine at this volume, with one catch they
called out: "this email's entire promise is that it lands fast and reads
personal." Retyping the tier paragraph into Mail is exactly the friction that
turns a one-day promise into a three-day one.

Everything it needs is already in the app. The size is a company metafield,
the tier bands are `leads.likely_tier`, the vocabulary is the same three-voice
map the Launch Week Kit uses, and Larry's own details are in company.json.

The tier paragraph is the whole point
-------------------------------------
"You told us Ignited Church is around 230 people. That puts you in our Growth
range" is the sentence that proves a human read the form. It is also the one
that does real damage when it is wrong, so `likely_tier` returns empty rather
than guess when the size field has no number in it, and the paragraph is
dropped entirely rather than rendered around a blank.
"""
from __future__ import annotations

from pathlib import Path

import jinja2

from . import leads as leads_module
from . import settings

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "email_templates"

# Option 2 from the setup notes. Option 1 ("Got it, {{ first }} — here's what
# happens next") tests as the warmer subject line, but Larry reached for this
# one when he wrote to Sherry Reed by hand, and the reasoning in the notes
# holds: a request from a church often gets forwarded to a board or an admin,
# and the organisation name in the subject is what lets it survive the
# forward. Change the string; nothing else depends on it.
SUBJECT = "{{ org_name }}: your discovery call, and what we'll cover"

# Mirrors the vocabulary map in `schema.derive`, which takes a partner record
# -- and a lead is not one. Duplicated deliberately rather than synthesising a
# fake record to feed it: three lines of data are cheaper to keep honest than a
# shim that silently produces "organization" for every church the day the
# record shape changes.
VOCAB = {
    "church": {
        "vocab_people": "congregation",
        "vocab_org": "church",
        "vocab_groups": "ministries",
    },
    "school": {
        "vocab_people": "families",
        "vocab_org": "school",
        "vocab_groups": "programs and teams",
    },
    "nonprofit": {
        "vocab_people": "supporters",
        "vocab_org": "organization",
        "vocab_groups": "programs and chapters",
    },
}

# Without these the email is either wrong or rude, so it will not render.
# Notably absent: org_size (the tier paragraph is dropped when it is empty)
# and booking_link (the button block is dropped, and the "or just reply" line
# carries the call to action on its own).
REQUIRED = {
    "contact_first_name": "Contact first name",
    "org_name": "Organization name",
    "point_of_contact_name": "Your name (Settings)",
    "point_of_contact_email": "Your email (Settings)",
    "point_of_contact_phone": "Your phone (Settings)",
}


def fields(lead: dict, config: dict | None = None) -> dict:
    """Every merge field, resolved from the lead plus company.json."""
    config = config or settings.load()

    # The form asks for first and last name separately, but a lead captured
    # before that split only has a display name.
    first_name = (lead.get("first_name") or "").strip()
    if not first_name:
        contact = (lead.get("name") or "").strip()
        first_name = contact.split()[0] if contact else ""

    org_size = str(lead.get("org_size") or "").strip()

    org_type = leads_module.normalise_org_type(lead.get("org_type") or "")
    vocab = VOCAB.get(org_type or "nonprofit", VOCAB["nonprofit"])

    # Resolve the type first: the tier one-liner is voiced too, or a school
    # gets "per campus and per ministry" in an otherwise correct email.
    tier, one_liner = leads_module.likely_tier(org_size, org_type)

    return {
        "contact_first_name": first_name,
        "org_name": (lead.get("org_name") or "").strip(),
        "org_size": org_size,
        "likely_tier": tier,
        "tier_one_liner": one_liner,
        # The tier block renders only when the size produced a real band. A
        # half-filled version of this paragraph is worse than none.
        "show_tier": bool(tier and org_size),
        **vocab,
        # Larry's details, entered once in Settings.
        "point_of_contact_name": config.get("point_of_contact_name", ""),
        "point_of_contact_email": config.get("point_of_contact_email", ""),
        "point_of_contact_phone": config.get("point_of_contact_phone", ""),
        "signoff_name": config.get("signoff_name", ""),
        "signoff_title": config.get("signoff_title", ""),
        # Optional. Each one drops its own block when empty rather than
        # shipping a link to nowhere -- an auto-send with a dead button is the
        # specific failure the setup notes warned about.
        "booking_link": str(config.get("booking_link") or "").strip(),
        "sample_store_link": str(config.get("sample_store_link") or "").strip(),
        "sample_store_label": (str(config.get("sample_store_label") or "").strip()
                               or "a live partner store"),
        # Empty until there is a PO box. The footer line is wrapped in a
        # conditional, same as the welcome email: the business runs out of the
        # house and that address is not going in a stranger's inbox.
        "company_address": config.get("company_address", ""),
        "logo_src": _logo_src(config),
        "header_src": _header_src(config),
    }


def _logo_src(config: dict) -> str:
    from . import welcome_email
    return welcome_email._logo_src(config)


def _header_src(config: dict) -> str:
    from . import welcome_email
    return welcome_email._header_src(config)


def missing(lead: dict, config: dict | None = None) -> list[str]:
    values = fields(lead, config)
    return [label for key, label in REQUIRED.items()
            if not str(values.get(key) or "").strip()]


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=False,          # the HTML template is escaped by hand
        undefined=jinja2.StrictUndefined,
    )


def render(lead: dict, config: dict | None = None) -> dict:
    """Subject, HTML body, plain-text body, recipient and what is missing."""
    config = config or settings.load()
    values = fields(lead, config)
    env = _env()
    return {
        "subject": env.from_string(SUBJECT).render(**values),
        "html": env.get_template("discovery_reply.html").render(**values),
        "text": env.get_template("discovery_reply.txt").render(**values),
        "to": lead.get("email", ""),
        "to_name": lead.get("name", ""),
        "missing": missing(lead, config),
        "fields": values,
    }
