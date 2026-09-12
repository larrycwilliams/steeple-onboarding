"""The written recommendation: the discovery call turned into a proposal.

Stage 4 of the lead-to-live-store path, and the last one with no tooling. The
discovery-call auto-reply promises, in writing, to every prospect:

    "After the call you'll get a short written recommendation -- tier,
     pricing, and a launch timeline -- so you have something to take to your
     team."

Until now that was typed by hand every time, which is why it is the slowest
step between the call and a contract. It is also the document that leaves the
room: the person on the call forwards it to whoever signs, so it is read by
people who never spoke to Larry.

Why an email and not a .docx
----------------------------
Every other document comes from a hand-built `source_docs/SRC_*.docx` that
`build_templates.py` converts into a merge template. There is no source
document for a recommendation, so a .docx would mean inventing a layout that
does not match the agreement or the leave-behind -- and those three end up in
the same hand. This is a letter: short, personal, sent within a day of the
call, and it prints cleanly if a board wants paper.

What it must never do
---------------------
**Commercial terms come from terms.json and nowhere else.** Not from the call
notes, not from a transcript. Five live partners once carried $0 setup and 10%
margin because a seed value was treated as a decision; see
25-live-terms-audit.md. The notes supply what was *said*; terms.json supplies
what things *cost*.

**A section with no answer behind it is dropped, not filled.** Half this
document's value is that it repeats the prospect's own words back to them; a
paragraph invented around a blank does the opposite. Emerald Coast's first
real recommendation had no price answer and no launch Sunday -- both were
still open when the call ended -- so neither section renders.
"""
from __future__ import annotations

from pathlib import Path

import jinja2

from . import discovery, leads as leads_module, settings, terms

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "email_templates"

SUBJECT = "{{ org_name }} — what I'd recommend"

# Mirrors discovery_reply.VOCAB. Duplicated deliberately: see the note there.
VOCAB = {
    "church": {"vocab_people": "congregation", "vocab_org": "church",
               "vocab_groups": "ministries"},
    "school": {"vocab_people": "families", "vocab_org": "school",
               "vocab_groups": "programs and teams"},
    "nonprofit": {"vocab_people": "supporters", "vocab_org": "organization",
                  "vocab_groups": "programs and chapters"},
}

REQUIRED = {
    "contact_first_name": "Contact first name",
    "org_name": "Organization name",
    "tier": "Tier (needs an attendance figure)",
    "point_of_contact_name": "Your name (Settings)",
    "point_of_contact_email": "Your email (Settings)",
    "point_of_contact_phone": "Your phone (Settings)",
}


def _answer(session: dict, qid: str) -> str:
    return ((session.get("answers") or {}).get(qid) or {}).get("note", "").strip()


def fields(session: dict, lead: dict | None = None,
           config: dict | None = None) -> dict:
    config = config or settings.load()
    lead = lead or {}
    ref = discovery.promise()

    contact = (lead.get("first_name") or "").strip()
    if not contact:
        whole = (lead.get("name") or session.get("contact") or "").strip()
        contact = whole.split()[0] if whole else ""

    # The call's attendance answer beats the form's number. The form is filled
    # in by whoever found the website; the answer to question 2 is what the
    # person actually said out loud, and it is often the corrected figure --
    # Emerald Coast's form said 270 and Adam said 300+. Both land in Growth
    # here, but a lead near a band edge would not.
    said = _answer(session, "attendance")
    tier, one_liner = leads_module.likely_tier(said)
    tier_source = "the call" if tier else ""
    if not tier:
        tier, one_liner = leads_module.likely_tier(lead.get("org_size") or "")
        tier_source = "the form" if tier else ""

    plan = (ref.get("plans") or {}).get(tier, {})
    giveback = ref.get("giveback") or {}

    org_type = leads_module.normalise_org_type(
        session.get("org_type") or lead.get("org_type") or "")
    vocab = VOCAB.get(org_type or "nonprofit", VOCAB["nonprofit"])

    # Their case, not both cases. The generic string is written for the call
    # screen, where Larry needs to see every option; a document sent to a
    # partner in Florida that opens with free pickup in Dayton offers
    # something they cannot use.
    mode = session.get("delivery_mode") or ""
    if mode == "ship":
        delivery = (
            "Everything ships straight to the person who ordered it — in-house "
            "items $8 a shipment, free over $100, and print-on-demand items at "
            "the vendor's rate. Nobody at your end handles a box or hands "
            "anything out.")
    elif mode == "pickup":
        delivery = (
            "Free pickup at your office on in-house items, or shipped straight "
            "to the buyer for $8 a shipment, free over $100. Print-on-demand "
            "items ship at the vendor's rate.")
    else:
        delivery = next((item["text"] for item in ref.get("items") or []
                         if item.get("label") == "Delivery"), "")

    return {
        "contact_first_name": contact,
        "org_name": (session.get("org_name") or lead.get("org_name") or "").strip(),
        **vocab,
        # what they told us -- every one of these may be empty
        "identity": _answer(session, "identity"),
        "history": _answer(session, "history"),
        "calendar": _answer(session, "calendar"),
        "anchor": _answer(session, "anchor"),
        "lineup": _answer(session, "lineup"),
        "sizes": _answer(session, "sizes"),
        "artwork": _answer(session, "artwork"),
        "receiving": _answer(session, "receiving"),
        "launch_answer": _answer(session, "launch"),
        "decider": _answer(session, "decider"),
        # the numbers -- terms.json only
        "tier": tier,
        "tier_one_liner": one_liner,
        "tier_source": tier_source,
        "attendance_said": said,
        "setup_fee": plan.get("setup_fee", ""),
        "monthly_fee": plan.get("monthly_fee", ""),
        "best_for": plan.get("best_for", ""),
        "giveback_pct": giveback.get("pct", ""),
        "giveback_spoken": giveback.get("spoken", ""),
        "giveback_example": giveback.get("example", ""),
        "payout_frequency": (ref.get("payout") or {}).get("frequency", ""),
        "payout_statement": (ref.get("payout") or {}).get("statement", ""),
        "launch_weeks": ref.get("launch_weeks", ""),
        "term_cadence": (ref.get("term") or {}).get("cadence", ""),
        "term_notice_days": (ref.get("term") or {}).get("notice_days", ""),
        "delivery_terms": delivery,
        "pickup_area": ref.get("pickup_area", ""),
        "prices": ref.get("prices") or {},
        # Larry
        "point_of_contact_name": config.get("point_of_contact_name", ""),
        "point_of_contact_email": config.get("point_of_contact_email", ""),
        "point_of_contact_phone": config.get("point_of_contact_phone", ""),
        "signoff_name": config.get("signoff_name", ""),
        "signoff_title": config.get("signoff_title", ""),
        "company_address": config.get("company_address", ""),
        "drive_folder": session.get("drive_folder", ""),
        "logo_src": _logo_src(config),
    }


def _logo_src(config: dict) -> str:
    from . import welcome_email
    return welcome_email._logo_src(config)


def missing(session: dict, lead: dict | None = None,
            config: dict | None = None) -> list[str]:
    values = fields(session, lead, config)
    return [label for key, label in REQUIRED.items()
            if not str(values.get(key) or "").strip()]


def _env() -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(TEMPLATES)),
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )


def render(session: dict, lead: dict | None = None,
           config: dict | None = None) -> dict:
    config = config or settings.load()
    values = fields(session, lead, config)
    env = _env()
    return {
        "subject": env.from_string(SUBJECT).render(**values),
        "html": env.get_template("recommendation.html").render(**values),
        "text": env.get_template("recommendation.txt").render(**values),
        "to": (lead or {}).get("email", ""),
        "to_name": (lead or {}).get("name", "") or session.get("contact", ""),
        "missing": missing(session, lead, config),
        "fields": values,
    }
