"""The covering email a partner gets with their quarterly statement.

Same shape as `welcome_email.py` on purpose -- merge fields resolved in one
place, a guard that refuses to render while any of them is empty, and the
house letterhead as a baked image rather than CSS the mail client will strip.
Read that module's comments before changing anything structural here; they
record why the header is an image and why the footer is light.

What is different is what the guard covers. A welcome email with a blank field
is embarrassing. A payout email with a wrong number is a dispute, so this one
also refuses to go out while the statement itself carries blockers -- and the
figure in the body is read from the statement rather than restated, so the
email and the attached PDF cannot disagree.

The 30% is **a charitable donation from Steeple & Stitch back to the partner**,
not the partner's cut of a joint venture -- Larry's wording, and the reason
nothing in here says "your share". See statement_pdf.py.

The body carries the headline figure, the order count and the five best
sellers by quantity. Everything else is in the attachment. A partner who wants
the detail opens the PDF; a partner who wants the number sees it without
opening anything, which is what most of them actually want on a phone.
"""
from __future__ import annotations

from pathlib import Path

import jinja2

from . import settings, welcome_email

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "email_templates"

SUBJECT = "{{ org_name }} — your {{ quarter }} donation from Steeple & Stitch"

# How many sellers to name in the body. Five fits a phone screen without
# scrolling; the attachment carries every line.
TOP_SELLERS = 5

FROM_RECORD = {
    "org_name": "Organization name",
    "contact_first_name": "Primary contact name",
    "to": "Primary contact email",
}
FROM_SETTINGS = {
    "point_of_contact_name": "Your name (Settings)",
    "point_of_contact_email": "Your email (Settings)",
    "signoff_name": "Sign-off name (Settings)",
}


def money(value) -> str:
    return "—" if value is None else f"${value:,.2f}"


def possessive(name: str) -> str:
    """Germantown Christian Schools' — not Schools's.

    A third of the book is schools, whose names end in s, and this word lands
    in the first sentence of an email about money. Getting it wrong reads as a
    mail merge, which is precisely what this email is trying not to be.
    """
    name = (name or "").strip()
    if not name:
        return ""
    return name + ("\u2019" if name.endswith(("s", "S")) else "\u2019s")


def fields(statement: dict, record: dict, config: dict | None = None) -> dict:
    config = config or settings.load()
    contact = str(record.get("poc_name") or "").strip()
    pct = statement["margin_pct"]

    sellers = [
        {"title": row["title"], "units": row["units"],
         "revenue": money(row["revenue"])}
        for row in statement["lines"][:TOP_SELLERS]
    ]

    return {
        "org_name": statement["org_name"],
        "org_possessive": possessive(statement["org_name"]),
        "contact_first_name": contact.split()[0] if contact else "",
        "to": record.get("poc_email", ""),
        "quarter": statement["quarter"],
        "period": statement["period"],
        "statement_number": statement["number"],
        "payout": money(statement["payout"]),
        "rate": f"{pct:g}%" if pct is not None else "—",
        "revenue": money(statement["revenue"]),
        "margin": money(statement["margin"]),
        "orders": statement["orders"],
        "units": statement["units"],
        "order_word": "order" if statement["orders"] == 1 else "orders",
        "unit_word": "item" if statement["units"] == 1 else "items",
        "sellers": sellers,
        "more_items": max(0, len(statement["lines"]) - TOP_SELLERS),
        "has_estimates": bool(statement["estimated_revenue"]),
        "has_uncosted": bool(statement["uncosted_revenue"]),
        "point_of_contact_name": config.get("point_of_contact_name", ""),
        "point_of_contact_email": config.get("point_of_contact_email", ""),
        "point_of_contact_phone": config.get("point_of_contact_phone", ""),
        "company_address": config.get("company_address", ""),
        "signoff_name": config.get("signoff_name", ""),
        "signoff_title": config.get("signoff_title", ""),
        "header_src": welcome_email.header_src(config),
    }


def missing(statement: dict, record: dict,
            config: dict | None = None) -> list[str]:
    """Everything that must be filled in before this can be sent.

    The statement's own blockers come first: a payout figure that cannot be
    stated is a worse problem than a missing first name, and listing them
    together means one screen answers "can I send this".
    """
    values = fields(statement, record, config)
    gaps = list(statement.get("blockers", []))
    for key, label in {**FROM_RECORD, **FROM_SETTINGS}.items():
        if not str(values.get(key) or "").strip():
            gaps.append(label)
    return gaps


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=False,          # the HTML template is escaped by hand
        undefined=jinja2.StrictUndefined,
    )


def render(statement: dict, record: dict, config: dict | None = None,
           attachments: list[dict] | None = None) -> dict:
    """Subject, HTML body, plain-text body and what is still missing."""
    config = config or settings.load()
    values = fields(statement, record, config)
    env = _env()
    return {
        "subject": env.from_string(SUBJECT).render(**values),
        "html": env.get_template("statement.html").render(**values),
        "text": env.get_template("statement.txt").render(**values),
        "to": values["to"],
        "to_name": record.get("poc_name", ""),
        "attachments": attachments or [],
        "missing": missing(statement, record, config),
        "fields": values,
    }
