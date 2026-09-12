"""The discovery call: twelve questions, in the order the conversation goes.

This is the "Church store discovery" checklist that started life as a
standalone page on the iPad, moved inside the app so the notes land on the
lead instead of in one browser's local storage.

Why it had to move
------------------
The standalone page kept everything in localStorage. That is one browser on
one device: notes taken on the iPad in a church office were invisible on the
Mac that writes the agreement, a cleared Safari cache lost a whole call, and
"Clear for the next partner" was the only way to start another one -- so two
calls in a week meant the first one's notes were gone unless someone had
copied the write-up out first. The write-up then had to be pasted back into a
chat to become a partner record. Here each call is a file next to the lead,
the pipeline shows how far it got, and the answers that belong on the partner
record (launch line-up, size range) are carried across when the lead is
promoted.

Where things live
-----------------
* Sessions: ``leads/discovery/<id>.json``. ``leads/`` is live business data --
  gitignored, and swept into the nightly Onboarding-Data-Backup with the
  stage file.
* The questions: ``QUESTIONS`` below. Code, because the ids are load-bearing
  (saved answers are keyed by them) and the wording is reviewed like code.
* What can be promised in the room: ``discovery.json``. Plans, the giveback
  and the contract term come from ``terms.json`` through ``terms.load()`` --
  never copied here, so the two cannot disagree the way the documents did.

Session ids
-----------
``lead-<shopify key>`` for a call with someone who came through the Book a
Discovery Call form, ``walkin-<slug>-<yyyymmdd>`` for a conversation that did
not (a merch-table introduction, a pastor who rang). A lead has exactly one
session -- opening it twice resumes, it never forks.

Saving
------
Every note and tick saves on its own (fetch, per field) the moment you stop
typing, so a second device can only ever overwrite the one field it touched,
and a dropped connection in a church basement costs nothing: the page keeps
unsent edits and resends them when the network returns. The file is rewritten
under an exclusive lock because gunicorn runs more than one worker.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from . import leads, store, terms
from .schema import FIELDS_BY_KEY, slugify

ROOT = Path(__file__).resolve().parents[1]
SESSIONS = ROOT / "leads" / "discovery"
PROMISE_PATH = ROOT / "discovery.json"

SESSION_ID = re.compile(r"^(lead|walkin)-[a-z0-9-]{1,120}$")


@dataclass(frozen=True)
class Question:
    id: str
    phase: str
    ask: str
    why: str          # trusted HTML, authored here; {placeholders} filled from discovery.json / terms.json
    placeholder: str
    # The partner-record field this answer seeds on promotion, if any.
    partner_field: str = ""


PHASES = ["Understand them", "Build the line-up", "Make it real"]

QUESTIONS: list[Question] = [
    Question(
        "decider", "Understand them",
        "Who's in the room, and who actually signs off?",
        "Pastor, comms person, a committee? <b>If the person deciding isn't "
        "here, find out today</b> — that's the difference between a launch in "
        "three weeks and a launch in three months.",
        "Names, roles, who approves…"),
    Question(
        "attendance", "Understand them",
        "How many people show up on a Sunday?",
        "Get a number, plus the family-versus-adult split and rough age skew. "
        "This sets everything downstream — how many items, which sizes, what "
        "price band, and which plan.",
        "Attendance, families, ages…"),
    Question(
        "identity", "Understand them",
        "What is this church known for?",
        "Ask them to say it in a sentence. A phrase people already repeat is "
        "worth more on a shirt than a logo. Note the exact wording — <b>their "
        "words, not a paraphrase.</b>",
        "Identity, tagline, how people describe it…"),
    Question(
        "history", "Understand them",
        "Have you done shirts before? How did it go?",
        "Almost every church has. Find out what they ordered, what it cost, how "
        "many were left in a box afterwards, and what they'd never do again. "
        "<b>Their last flop tells you more than their wish list.</b>",
        "Past orders, vendor, what worked, leftovers…"),
    Question(
        "calendar", "Understand them",
        "What's on the calendar for the next six months?",
        "Sermon series, camp, missions trip, anniversary, Christmas. <b>Merch "
        "sells against an event, not against a website.</b> Every date is a "
        "reason for someone to buy.",
        "Dates and what they're called…"),
    Question(
        "anchor", "Build the line-up",
        "If everyone owned one thing, what is it?",
        "Force a single answer. This is the anchor — the item you photograph, "
        "they promote, and everyone recognises. The rest of the store exists "
        "to support it.",
        "The anchor item…"),
    Question(
        "lineup", "Build the line-up",
        "What are the four to six items we launch with?",
        "A tee, a warm layer, a hat, and one thing for kids covers most "
        "congregations. <b>Resist more.</b> A short store outsells a long one, "
        "and every extra item is real setup work before a single sale.",
        "The launch line-up…",
        partner_field="product_lineup"),
    Question(
        "price", "Build the line-up",
        "What will this congregation pay without flinching?",
        "Say your numbers out loud — <span class=\"mono\">{tee}</span> for a "
        "tee, <span class=\"mono\">{layer}</span> for a hoodie — and watch the "
        "reaction. Also settle the awkward one now: <b>is this meant to raise "
        "money, break even, or just get people wearing it?</b>",
        "Price band, and what they expect it to earn…"),
    Question(
        "sizes", "Build the line-up",
        "Who needs sizes we haven't discussed?",
        "Youth and toddler if there are families. Women's cut if the room asks "
        "for it. Extended sizes to 4XL as standard. <b>A store with no "
        "children's sizes tells a family of four to buy two shirts.</b>",
        "Youth, toddler, women's fit, extended…",
        partner_field="size_range"),
    Question(
        "artwork", "Make it real",
        "What artwork exists, and who owns it?",
        "Vector files or a JPEG off a bulletin? Who made it, and can they use "
        "it freely? <b>Ask for the files today</b> — waiting on artwork is the "
        "single most common reason a launch slips.",
        "Files, formats, who owns it, what we design…"),
    Question(
        "receiving", "Make it real",
        "Who receives the orders, and how often?",
        "Office pickup only works if someone is expecting the box and hands "
        "things out. Get a name, and agree a rhythm — weekly, or after each "
        "order batch. <b>Pickup is {pickup_area} only; everyone else ships to "
        "their door.</b>",
        "Contact at the office, delivery cadence…"),
    Question(
        "launch", "Make it real",
        "What Sunday does it launch, and who announces it?",
        "Pin a date, a first-order deadline, and the channel — from the stage, "
        "in the bulletin, by email, a QR code on the seat back. A store takes "
        "{launch_weeks} weeks from final artwork, so count back from the date. "
        "<b>Leave with a date on the calendar or this conversation repeats in "
        "six weeks.</b>",
        "Launch Sunday, deadline, who announces, channel…"),
]

QUESTIONS_BY_ID = {q.id: q for q in QUESTIONS}


# --------------------------------------------------------------- reference --

def promise() -> dict:
    """discovery.json, plus the commercial terms from terms.json.

    A missing or broken discovery.json degrades to an empty card rather than
    a 500 -- this screen is used live, across a table, and a traceback in
    front of a pastor is worse than a card with nothing on it.
    """
    try:
        data = json.loads(PROMISE_PATH.read_text())
    except (OSError, ValueError):
        data = {}
    t = terms.load()
    prices = data.get("price_points") or {}
    return {
        "items": data.get("promise") or [],
        "verified": data.get("verified", ""),
        "prices": prices,
        "pickup_area": data.get("pickup_area") or "the Dayton area",
        "plans": t.get("plans", {}),
        "giveback": t.get("giveback", {}),
        "term": t.get("term", {}),
        "launch_weeks": (t.get("launch") or {}).get("weeks", ""),
        "payout": t.get("payout", {}),
    }


def rendered_questions(ref: dict | None = None) -> list[dict]:
    """QUESTIONS with their placeholders filled from the reference data.

    ``format_map`` with a default so a key missing from discovery.json renders
    as a visible gap ("…") instead of raising in the middle of a call.
    """
    ref = ref or promise()
    values = _Blank({
        "tee": ref["prices"].get("tee", ""),
        "layer": ref["prices"].get("layer", ""),
        "pickup_area": ref["pickup_area"],
        "launch_weeks": ref["launch_weeks"] or "a few",
    })
    out = []
    for number, q in enumerate(QUESTIONS, start=1):
        out.append({
            "number": number,
            "id": q.id,
            "phase": q.phase,
            "ask": q.ask,
            "why": q.why.format_map(values),
            "placeholder": q.placeholder,
            "partner_field": q.partner_field,
            "partner_label": (FIELDS_BY_KEY[q.partner_field].label
                              if q.partner_field in FIELDS_BY_KEY else ""),
        })
    return out


class _Blank(dict):
    def __missing__(self, key):
        return "…"


# ----------------------------------------------------------------- storage --

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _path(sid: str) -> Path:
    if not SESSION_ID.match(sid or ""):
        raise ValueError(f"{sid!r} is not a discovery session id")
    return SESSIONS / f"{sid}.json"


def _blank_answers() -> dict:
    return {q.id: {"note": "", "covered": False} for q in QUESTIONS}


@contextmanager
def _locked(sid: str):
    """Exclusive lock around a read-modify-write of one session.

    Two gunicorn workers can each take a save for the same call a few hundred
    milliseconds apart -- the note and the tick fire together. Without the
    lock the second write is built from a copy read before the first landed,
    and silently undoes it.
    """
    SESSIONS.mkdir(parents=True, exist_ok=True)
    lock_path = SESSIONS / f".{sid}.lock"
    with open(lock_path, "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def load(sid: str) -> dict | None:
    try:
        path = _path(sid)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        session = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    # A question added after this call was taken simply shows up empty.
    answers = _blank_answers()
    answers.update({k: v for k, v in (session.get("answers") or {}).items()
                    if k in answers and isinstance(v, dict)})
    session["answers"] = answers
    # Sessions written before the link existed simply have none.
    session.setdefault("meet_link", "")
    return session


def _write(session: dict) -> dict:
    session["updated_at"] = _now()
    path = _path(session["id"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(session, indent=1, ensure_ascii=False))
    # Rename is atomic, so a reader never sees a half-written file.
    os.replace(tmp, path)
    return session


def list_sessions() -> list[dict]:
    if not SESSIONS.is_dir():
        return []
    out = []
    for path in SESSIONS.glob("*.json"):
        session = load(path.stem)
        if session:
            out.append(session)
    out.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return out


def progress(session: dict) -> dict:
    answers = session.get("answers") or {}
    covered = sum(1 for q in QUESTIONS if (answers.get(q.id) or {}).get("covered"))
    noted = sum(1 for q in QUESTIONS if (answers.get(q.id) or {}).get("note", "").strip())
    return {"covered": covered, "noted": noted, "total": len(QUESTIONS),
            "started": bool(covered or noted)}


def by_lead_key() -> dict[str, dict]:
    """{lead key: session} for the pipeline cards."""
    return {s["lead_key"]: s for s in list_sessions()
            if s.get("kind") == "lead" and s.get("lead_key")}


# ----------------------------------------------------------------- create --

def lead_session_id(key: str) -> str:
    return f"lead-{slugify(key)}"


def open_for_lead(lead: dict) -> dict:
    """The lead's session, created on first open. Never a second one."""
    sid = lead_session_id(lead["key"])
    with _locked(sid):
        existing = load(sid)
        if existing:
            # Keep the display name current -- the org name can be corrected
            # on the pipeline card after the call was opened.
            if lead.get("org_name") and existing.get("org_name") != lead["org_name"]:
                existing["org_name"] = lead["org_name"]
                _write(existing)
            return existing
        session = {
            "id": sid,
            "kind": "lead",
            "lead_id": lead["id"],
            "lead_key": lead["key"],
            "org_name": lead.get("org_name") or "",
            "org_type": lead.get("org_type") or "",
            "contact": lead.get("name") or "",
            "created_at": _now(),
            "held_at": "",
            "partner_id": lead.get("partner_id") or "",
            "meet_link": "",
            "answers": _blank_answers(),
        }
        return _write(session)


def open_walkin(org_name: str, org_type: str = "") -> tuple[dict | None, str]:
    """A call with someone who never filled the form. (session, message)."""
    org_name = " ".join((org_name or "").split())
    if not org_name:
        return None, "Give the organisation a name — it's how you'll find these notes again."
    org_type = leads.normalise_org_type(org_type) or ""
    stamp = date.today().strftime("%Y%m%d")
    base = f"walkin-{slugify(org_name)[:80] or 'call'}-{stamp}"
    sid, n = base, 2
    # Same organisation, same day, second conversation: its own notes.
    while _path(sid).exists():
        sid, n = f"{base}-{n}", n + 1
    with _locked(sid):
        session = {
            "id": sid,
            "kind": "walkin",
            "lead_id": "",
            "lead_key": "",
            "org_name": org_name,
            "org_type": org_type,
            "contact": "",
            "created_at": _now(),
            "held_at": "",
            "partner_id": "",
            "meet_link": "",
            "answers": _blank_answers(),
        }
        return _write(session), f"Started notes for {org_name}."


# ------------------------------------------------------------------- edit --

EDITABLE_HEADER = ("org_name", "org_type", "contact")

# The video call's join link, kept out of EDITABLE_HEADER on purpose: it is
# validated as a URL rather than squeezed into 200 characters of prose, and it
# must never reach writeup(). A live meeting link is a door into a room, not a
# fact about a partner, and the write-up is pasted into emails and partner
# files -- see claude/ops/26-discovery-call-screen.
MEET_LINK_MAX = 500


# A calendar entry copied whole, with the link somewhere inside it.
MEET_LINK_IN_TEXT = re.compile(r"https://\S+")


def clean_meet_link(value) -> tuple[str, str]:
    """(url, error). An empty value clears it.

    Accepts a pasted block as well as a bare URL. Copying the whole calendar
    entry is the obvious thing to do -- the first real use of this field was
    "Steeple & Stitch - Emerald Coast Church - Discovery Call Thursday,
    September 10 - 10:00 - 10:30am ..." with the join link buried in it -- so
    the link is pulled out of the text rather than the paste being refused.
    """
    url = " ".join(str(value or "").split())
    if not url:
        return "", ""
    if not url.lower().startswith("https://"):
        found = MEET_LINK_IN_TEXT.search(url)
        if found:
            # Trailing punctuation belongs to the sentence, not the URL.
            url = found.group(0).rstrip(".,;:)]>\"'")
    if len(url) > MEET_LINK_MAX:
        return "", f"That link is longer than {MEET_LINK_MAX} characters."
    # https only. A join link is pasted from a browser or a calendar invite, so
    # http:// is a typo worth catching -- and refusing everything that is not
    # https keeps javascript: and data: out of an href the template renders as
    # a button. Not restricted to meet.google.com: Zoom and Teams links are the
    # same kind of thing and there is no reason to make the field lie.
    if not url.lower().startswith("https://"):
        return "", "A meeting link has to start with https://"
    return url, ""


def save_field(sid: str, field: str, value) -> tuple[dict | None, str]:
    """One field. ``<question>.note``, ``<question>.covered`` or a header field.

    (session, error). Returns the error rather than raising: the caller is a
    fetch() from a page mid-call, and it needs a sentence it can show.
    """
    with _locked(sid):
        session = load(sid)
        if session is None:
            return None, "Those notes no longer exist."
        if "." in field:
            qid, part = field.split(".", 1)
            if qid not in QUESTIONS_BY_ID or part not in ("note", "covered"):
                return None, f"{field!r} is not a discovery field."
            if part == "note":
                session["answers"][qid]["note"] = str(value or "")[:5000]
            else:
                session["answers"][qid]["covered"] = _truthy(value)
        elif field == "meet_link":
            url, error = clean_meet_link(value)
            if error:
                return None, error
            session["meet_link"] = url
        elif field in EDITABLE_HEADER:
            value = " ".join(str(value or "").split())[:200]
            if field == "org_type":
                value = leads.normalise_org_type(value)
            if field == "org_name" and session.get("kind") == "lead" and not value:
                return None, "A lead's organisation name can't be blank."
            session[field] = value
        else:
            return None, f"{field!r} is not a discovery field."
        return _write(session), ""


def save_form(sid: str, form) -> tuple[dict | None, str]:
    """Everything at once -- the no-JavaScript fallback's Save button."""
    session = load(sid)
    if session is None:
        return None, "Those notes no longer exist."
    for field in EDITABLE_HEADER:
        if field in form:
            session, error = save_field(sid, field, form.get(field))
            if error:
                return None, error
    if "meet_link" in form:
        session, error = save_field(sid, "meet_link", form.get("meet_link"))
        if error:
            return None, error
    for q in QUESTIONS:
        if f"{q.id}.note" in form:
            save_field(sid, f"{q.id}.note", form.get(f"{q.id}.note"))
        # An unticked checkbox is simply absent from a form post.
        save_field(sid, f"{q.id}.covered", form.get(f"{q.id}.covered"))
    return load(sid), ""


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


# Stages at or past "Call held". Marking a call held never drags a lead
# BACKWARDS -- a lead already in "Proposal out" that gets its notes tidied up
# afterwards must not reappear as merely "Call held".
_HELD_INDEX = leads.STAGES.index("Call held")


def mark_held(sid: str) -> tuple[bool, str]:
    session = load(sid)
    if session is None:
        return False, "Those notes no longer exist."
    moved = ""
    if session.get("kind") == "lead" and session.get("lead_key"):
        lead = leads.find_lead(session["lead_key"])
        if lead is None:
            return False, "That lead is no longer in the pipeline."
        current = lead.get("stage") or "New"
        if current in leads.STAGES and leads.STAGES.index(current) < _HELD_INDEX:
            leads.set_stage(lead["id"], "Call held")
            moved = f" {session.get('org_name') or 'Lead'} → Call held."
        else:
            moved = f" Stage left at {current}."
    with _locked(sid):
        session = load(sid)
        session["held_at"] = session.get("held_at") or _now()
        _write(session)
    return True, "Call marked held." + moved


# ---------------------------------------------------------------- promote --

def _is_seed(record: dict, key: str) -> bool:
    """Blank, or still the schema default -- i.e. nobody has written it yet.

    product_lineup and size_range are seeded with GCS-flavoured defaults by
    new_record(), so "only fill blanks" alone would never fire.
    """
    value = (record.get(key) or "").strip()
    default = (FIELDS_BY_KEY[key].default or "").strip() if key in FIELDS_BY_KEY else ""
    return value == "" or value == default


def carry_to_partner(session: dict, record: dict) -> list[str]:
    """Copy answers into a partner record where the record has nothing of its own.

    Never overwrites something typed on the partner form: that is the
    negotiated, tidied version and the call notes are the rough draft.
    Returns the labels of the fields it filled.
    """
    filled = []
    for q in QUESTIONS:
        if not q.partner_field:
            continue
        note = (session["answers"].get(q.id) or {}).get("note", "").strip()
        if note and _is_seed(record, q.partner_field):
            record[q.partner_field] = note
            filled.append(FIELDS_BY_KEY[q.partner_field].label)
    record["discovery_id"] = session["id"]
    return filled


def promote(sid: str) -> tuple[dict | None, str]:
    """Create the partner record from a call. (record, message)."""
    session = load(sid)
    if session is None:
        return None, "Those notes no longer exist."

    if session.get("kind") == "lead":
        lead = leads.find_lead(session.get("lead_key", ""))
        if lead is None:
            return None, "That lead is no longer in the pipeline."
        # leads.promote owns the rules (name required, no duplicates, stage
        # to Won). Reuse it rather than growing a second copy of them.
        record, message = leads.promote(lead)
        if record is None:
            return None, message
    else:
        org_name = (session.get("org_name") or "").strip()
        if not org_name:
            return None, "These notes have no organisation name yet."
        if store.load(store.partner_id({"org_name": org_name})):
            return None, f"A partner record for {org_name!r} already exists — use Copy into partner instead."
        record = store.new_record()
        record.update({
            "org_name": org_name,
            "org_type": session.get("org_type") or leads.guess_org_type(org_name),
            "poc_name": session.get("contact") or "",
        })
        message = (f"Created partner record for {org_name}. Fees, margin and "
                   "signers are still blank — fill them in before generating anything.")

    filled = carry_to_partner(session, record)
    record = store.save(record)
    with _locked(sid):
        fresh = load(sid)
        fresh["partner_id"] = store.partner_id(record)
        _write(fresh)
    if filled:
        message += " Copied from the call: " + ", ".join(filled) + " — tidy them before generating."
    return record, message


def apply_to_partner(sid: str) -> tuple[dict | None, str]:
    """For a call with an organisation that is already a partner."""
    session = load(sid)
    if session is None:
        return None, "Those notes no longer exist."
    pid = session.get("partner_id") or ""
    record = store.load(pid) if pid else None
    if record is None:
        return None, "No partner record is linked to these notes yet."
    filled = carry_to_partner(session, record)
    store.save(record)
    if not filled:
        return record, ("Nothing copied — the partner record already has its own "
                        "line-up and size range, and those win over call notes.")
    return record, "Copied into the partner record: " + ", ".join(filled) + "."


def link_partner(session: dict) -> dict:
    """Attach an existing partner to a lead session whose lead was promoted
    some other way (from the pipeline card, before these notes existed)."""
    if session.get("partner_id") or session.get("kind") != "lead":
        return session
    lead = leads.find_lead(session.get("lead_key", ""))
    if lead and lead.get("partner_id"):
        with _locked(session["id"]):
            fresh = load(session["id"])
            fresh["partner_id"] = lead["partner_id"]
            session = _write(fresh)
    return session


# ---------------------------------------------------------------- write-up --

def writeup(session: dict, lead: dict | None = None) -> str:
    """Plain text, for an email, Notes, or the partner's file."""
    name = session.get("org_name") or "Unnamed partner"
    stamp = datetime.now().strftime("%B %-d, %Y")
    out = ["CHURCH STORE DISCOVERY", name, stamp]
    if lead:
        contact = " · ".join(x for x in (lead.get("name"), lead.get("email"),
                                         lead.get("phone")) if x)
        if contact:
            out.append(contact)
        if lead.get("org_size"):
            tier = lead.get("likely_tier")
            out.append(f"Form said {lead['org_size']} people"
                       + (f" → {tier} likely" if tier else ""))
    elif session.get("contact"):
        out.append(session["contact"])
    out.append("")

    covered = 0
    for number, q in enumerate(QUESTIONS, start=1):
        answer = session["answers"].get(q.id) or {}
        note = (answer.get("note") or "").strip()
        ticked = bool(answer.get("covered"))
        covered += ticked
        out.append(f"{number}. {q.ask}")
        if note:
            out.append("   " + note.replace("\n", "\n   "))
        else:
            out.append("   (covered, no notes)" if ticked else "   — not covered —")
        out.append("")
    out.append(f"Covered {covered} of {len(QUESTIONS)}.")
    return "\n".join(out)
