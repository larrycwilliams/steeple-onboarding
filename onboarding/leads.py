"""The discovery-call pipeline: leads from the website form, tracked to signed.

Where leads come from
---------------------
The Book a Discovery Call page runs the **Shopify Forms** app, and each
submission creates a **Company** plus a Customer. The company is the lead:
it carries the organisation name and every custom answer as company
metafields. The customer attached to it is just the person who filled the
form -- name, email, phone.

That distinction cost an evening. This module first read the *customer*
record, found no metafields on it, and reported that the company name and
size "only exist in the notification email" -- so those values were typed in
by hand for two leads that already had them in Shopify. The data was one
object over the whole time. **Read the company; use the customer only for
contact details.**

Traps, all of which produce a screen that looks fine
---------------------------------------------------
**1. `tag_prefix:` is not a customer search field.** Shopify silently ignores
filter fields it does not recognise and returns EVERYTHING -- every customer
in the store, looking exactly like a working lead list.

**2. Larry's own test submissions look like real leads.** Excluding them by
record id fixes today and breaks the next time he tests, so the exclusion is
by email address. Shopify also writes the submitter's address into the
company note when they are already a customer, which catches the submissions
whose company has no contact attached.

**3. A guessed organisation type is indistinguishable from a stated one**
unless it is labelled. "Haven of Hope Church" was filed as a non-profit by a
name heuristic while the customer's own answer sat unread. Precedence is
now: set by hand > answered on the form > guessed, and the guess says so.

What is deliberately NOT here
-----------------------------
Stage is stored locally, not written back to Shopify. That would need
`write_companies`, and the mobile case that usually justifies it is already
solved -- the app is reachable from the iPad over Tailscale.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from . import store
from .shopify_pull import configured
from .shopify_sales import _amount, _endpoint  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache" / "shopify_leads.json"
STAGES_PATH = ROOT / "leads" / "_stages.json"
TIMEOUT = 30

# Companies carry the answers, customers carry the contact. Both are needed.
SCOPES = "read_customers, read_companies"

# Company metafields, as Shopify Forms named them from the field labels.
MF_SIZE = "size_of_congregationmembers"
MF_TYPE = "type_of_organization"
MF_PROMPT = "what_is_prompting_you_to_look_at_steeple_stitch_co"
MF_LOGO = "upload_your_logo"

# Only companies created by the form are leads. Shopify writes this phrase
# into the note; a company added by hand for a real wholesale account must
# not show up in the sales pipeline.
FORM_NOTE = "submitted to the form"

# Ordered. "New" is where every lead lands; the two terminal stages sit at the
# end so the pipeline view can stop showing them without a special case.
#
# "Replied" exists because answering someone and booking them are different
# events, often days apart. Without it the only way to clear a lead off the
# overdue list was to book a call -- so a lead Larry answered within the hour
# went on reading "past the promise" until they wrote back. The board was
# reporting his responsiveness as the prospect's response time.
STAGES = ["New", "Replied", "Call booked", "Call held", "Proposal out",
          "Won", "Lost"]

# Derived by name, not by slice. This was `STAGES[:4]`, which silently means
# something different the moment a stage is inserted -- exactly what this
# change does. Naming the two terminal stages makes the next insertion free.
CLOSED_STAGES = ("Won", "Lost")
OPEN_STAGES = tuple(stage for stage in STAGES if stage not in CLOSED_STAGES)

# The promise is about the FIRST REPLY, so only a lead nobody has answered can
# be overdue. Previously this was `stage in OPEN_STAGES`, which meant a lead
# sitting in "Proposal out" for three weeks -- entirely normal, the ball is in
# their court -- still showed up red as though Larry had ignored it.
AWAITING_REPLY_STAGES = ("New",)

# A lead that has been sitting this long is the one that needs looking at. The
# auto-reply promises "within one business day", so the threshold is the promise.
STALE_DAYS = 1

# Snoozing exists because "not answered yet" and "ignored" are different, and
# only one of them deserves a red border. Sherry Reed's church was in
# Mississippi the weekend she enquired; Larry saw that and deliberately held
# the reply until they were home. Correct call -- and the board called it a
# broken promise for three days running. A response-time metric that punishes
# good judgement is one you stop reading, so a lead can be held until a date
# with the reason attached.
SNOOZE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

LEADS_QUERY = """
query Leads($first: Int!, $after: String) {
  companies(first: $first, after: $after, sortKey: CREATED_AT, reverse: true) {
    edges {
      node {
        id
        name
        createdAt
        note
        metafields(first: 20) { edges { node { namespace key value } } }
        contacts(first: 5) {
          edges {
            node {
              customer {
                id
                displayName
                firstName
                lastName
                defaultEmailAddress { emailAddress }
                defaultPhoneNumber { phoneNumber }
              }
            }
          }
        }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""


# ------------------------------------------------------------------- fetch --

def own_addresses() -> set[str]:
    """Addresses whose submissions are Larry testing his own form.

    Seeded from company.json so it follows the settings, plus the two he has
    already used. Lowercased; compared exactly.
    """
    from . import settings as company_settings

    values = company_settings.load()
    addresses = {
        values.get("point_of_contact_email", ""),
        values.get("support_email", ""),
        "larry@thewilliamscollective.com",
        "larrycwilliams@gmail.com",
        "info@steepleandstitch.com",
    }
    extra = os.environ.get("SS_OWN_ADDRESSES", "")
    addresses.update(part.strip() for part in extra.split(",") if part.strip())
    return {a.strip().lower() for a in addresses if a and a.strip()}


def fetch() -> dict:
    """Pull every form-created company. Returns a cacheable snapshot."""
    if not configured():
        raise RuntimeError("Not connected to Shopify. Connect from Settings.")

    nodes, after = [], None
    while True:
        response = requests.post(
            _endpoint(),
            headers={
                "X-Shopify-Access-Token": os.environ["SHOPIFY_ADMIN_TOKEN"],
                "Content-Type": "application/json",
            },
            json={"query": LEADS_QUERY,
                  "variables": {"first": 50, "after": after}},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            message = str(payload["errors"][:1])
            # Say which scope is missing and how to add it, rather than
            # surfacing Shopify's bare "access denied".
            if "access denied" in message.lower() or "scope" in message.lower():
                raise RuntimeError(
                    f"Shopify refused the request — the app's token is missing "
                    f"`{SCOPES}`. Add them to the app's scopes in Dev "
                    "Dashboard, release a version, and reconnect from Settings.")
            raise RuntimeError(message)

        block = payload["data"]["companies"]
        nodes.extend(edge["node"] for edge in block["edges"])
        if not block["pageInfo"]["hasNextPage"]:
            break
        after = block["pageInfo"]["endCursor"]

    leads = [_clean(node) for node in nodes
             if FORM_NOTE in (node.get("note") or "")]
    return {
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "companies",
        "leads": leads,
    }


def short_id(gid: str) -> str:
    """The numeric tail of a Shopify GID.

    `gid://shopify/Customer/24806813368688` cannot go in a route path — the
    slashes make it three path segments. Routes carry the tail and resolve
    back by suffix match, which is unambiguous because Shopify ids are unique.
    """
    return (gid or "").rstrip("/").rsplit("/", 1)[-1]


def _metafields(node: dict) -> dict:
    values = {}
    for edge in ((node.get("metafields") or {}).get("edges") or []):
        field = edge.get("node") or {}
        if field.get("namespace") == "custom":
            values[field.get("key")] = field.get("value")
    return values


def _first_contact(node: dict) -> dict:
    for edge in ((node.get("contacts") or {}).get("edges") or []):
        customer = (edge.get("node") or {}).get("customer") or {}
        if customer:
            return customer
    return {}


def _clean(node: dict) -> dict:
    fields = _metafields(node)
    customer = _first_contact(node)
    email = ((customer.get("defaultEmailAddress") or {}).get("emailAddress")
             or "").strip()
    phone = ((customer.get("defaultPhoneNumber") or {}).get("phoneNumber")
             or "").strip()
    return {
        "id": node["id"],
        "key": short_id(node["id"]),
        "org_name": (node.get("name") or "").strip(),
        "created_at": node.get("createdAt") or "",
        "note": node.get("note") or "",
        # Contact details belong to the person, not the organisation.
        "customer_id": customer.get("id") or "",
        "name": (customer.get("displayName") or "").strip(),
        "first_name": (customer.get("firstName") or "").strip(),
        "last_name": (customer.get("lastName") or "").strip(),
        "email": email,
        "phone": phone,
        # The form's own answers.
        "org_size": fields.get(MF_SIZE),
        "org_type": fields.get(MF_TYPE),
        "prompt": fields.get(MF_PROMPT),
        # A file reference gid, when they uploaded artwork with the form.
        "logo_ref": fields.get(MF_LOGO),
    }


def save_cache(snapshot: dict) -> Path:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(snapshot, indent=1))
    return CACHE


def load_cache() -> dict | None:
    if not CACHE.exists():
        return None
    try:
        return json.loads(CACHE.read_text())
    except (ValueError, OSError):
        return None


def snapshot(refresh: bool = False) -> tuple[dict | None, str]:
    """(snapshot, source). A failed refresh keeps the previous data."""
    cached = load_cache()
    if not refresh and cached:
        return cached, "cache"
    if not configured():
        if cached:
            return cached, "cache — not connected to Shopify"
        return None, "Not connected to Shopify. Connect from Settings."
    try:
        fresh = fetch()
        save_cache(fresh)
        return fresh, "live"
    except Exception as exc:
        if cached:
            return cached, f"cache — refresh failed: {exc}"
        return None, str(exc)


# ------------------------------------------------------------------ stages --

def _load_stages() -> dict:
    if not STAGES_PATH.exists():
        return {}
    try:
        return json.loads(STAGES_PATH.read_text())
    except (ValueError, OSError):
        return {}


def _save_stages(data: dict) -> None:
    STAGES_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGES_PATH.write_text(json.dumps(data, indent=1, sort_keys=True))


def set_stage(lead_id: str, stage: str, note: str = "") -> bool:
    if stage not in STAGES:
        return False
    data = _load_stages()
    entry = data.get(lead_id, {})
    entry["stage"] = stage
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry["moved_at"] = now
    # Stamped once, the first time a lead leaves "New" by any route -- marked
    # Replied, booked straight from a phone call, or promoted. `moved_at` moves
    # every time the stage changes, so it cannot answer "how fast did we get
    # back to them"; this can.
    #
    # It records when Larry TOLD the app, not when he actually replied. A reply
    # sent Tuesday and logged Friday reads as three days. The file is small and
    # hand-editable when that gap matters.
    if stage != "New" and not entry.get("replied_at"):
        entry["replied_at"] = now
    if stage == "New":
        # Moving a lead back to New means it was never really answered.
        entry.pop("replied_at", None)
    if note:
        entry["note"] = note
    data[lead_id] = entry
    _save_stages(data)
    return True


def set_snooze(lead_id: str, until: str, reason: str = "") -> tuple[bool, str]:
    """Hold a lead until a date. (ok, message). An empty date clears the hold.

    Stored as a bare YYYY-MM-DD local date, not a timestamp: "we're waiting for
    them to get back from Mississippi" has a day, not an hour, and a timestamp
    invites a timezone bug for no gain.
    """
    until = (until or "").strip()
    reason = " ".join((reason or "").split())[:200]

    data = _load_stages()
    entry = data.get(lead_id, {})

    if not until:
        entry.pop("snoozed_until", None)
        entry.pop("snooze_reason", None)
        if entry:
            data[lead_id] = entry
        else:
            data.pop(lead_id, None)
        _save_stages(data)
        return True, "Hold cleared."

    if not SNOOZE_DATE.match(until):
        return False, "A hold date must look like 2026-09-14."
    try:
        target = date.fromisoformat(until)
    except ValueError:
        return False, f"{until!r} is not a real date."
    # A hold in the past is almost certainly a mis-typed year, and it would
    # silently do nothing -- the lead would go straight back to overdue while
    # the screen showed a hold. Refuse it instead.
    if target < date.today():
        return False, (f"{until} has already passed, so that hold would do "
                       "nothing. Pick a date from today onward.")

    entry["snoozed_until"] = until
    if reason:
        entry["snooze_reason"] = reason
    else:
        entry.pop("snooze_reason", None)
    data[lead_id] = entry
    _save_stages(data)
    return True, f"Holding until {target.strftime('%a %b %-d')}."


def snooze_state(local: dict, today: date | None = None) -> tuple[bool, str, str]:
    """(holding_now, until, reason) for a stage entry."""
    until = (local.get("snoozed_until") or "").strip()
    if not until or not SNOOZE_DATE.match(until):
        return False, "", ""
    try:
        target = date.fromisoformat(until)
    except ValueError:
        return False, "", ""
    # Inclusive: a hold "until Monday" covers Monday. It lapses on its own the
    # next morning -- nothing has to clear it, which is the point.
    holding = (today or date.today()) <= target
    return holding, until, (local.get("snooze_reason") or "")


def set_fields(lead_id: str, org_name: str | None = None,
               org_size: str | None = None, org_type: str | None = None) -> None:
    """Hand-entered org details, for leads that predate the Forms mapping.

    Jessica and Sherry submitted before the customer metafields existed, so
    their company and size live only in the notification emails. Typed in here
    they behave exactly like captured ones.

    `None` means "not supplied, leave alone"; an empty string means "clear it".
    That distinction matters: this used to test `if org_name:`, so submitting
    the form with the boxes emptied was silently ignored and the old value
    stayed. You could set a field but never correct a mistake by clearing it --
    and the screen showed a successful save either way. The form always posts
    both boxes, so an empty box genuinely does mean clear.
    """
    data = _load_stages()
    entry = data.get(lead_id, {})
    for field, value in (("org_name", org_name), ("org_size", org_size),
                         ("org_type", org_type)):
        if value is None:
            continue
        value = value.strip()
        if value:
            entry[field] = value
        else:
            entry.pop(field, None)
    if entry:
        data[lead_id] = entry
    else:
        # An entry with nothing left in it is noise in the file.
        data.pop(lead_id, None)
    _save_stages(data)


# ------------------------------------------------------------------- merge --

def _parse(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def days_open(lead: dict, now: datetime | None = None) -> int | None:
    moment = _parse(lead.get("created_at", ""))
    if not moment:
        return None
    return ((now or datetime.now(timezone.utc)) - moment).days


def reply_gap(lead: dict, local: dict) -> float | None:
    """Hours between the form submission and the first reply, or None."""
    created = _parse(lead.get("created_at", ""))
    replied = _parse(local.get("replied_at", ""))
    if not created or not replied:
        return None
    return max(0.0, (replied - created).total_seconds() / 3600.0)


def reply_label(hours: float | None) -> str:
    """'in 40 min' / 'in 6h' / 'in 2 days'. Empty when unknown."""
    if hours is None:
        return ""
    if hours < 1:
        return f"in {max(1, int(hours * 60))} min"
    if hours < 48:
        return f"in {int(round(hours))}h"
    return f"in {int(round(hours / 24))} days"


SIZE_DIGITS = re.compile(r"\d[\d,]*")


# The tier one-liners were written in church language and did not pass through
# the three-voice map, so a school got "separate looks per campus and per
# MINISTRY" in the one paragraph that is supposed to prove a person read their
# form. Two thirds of the pipeline is not a church. Templated per voice now.
# `group` reads as a compound ("... room for team and program sub-logos");
# `unit` has to stay singular because it follows "per" ("per campus and per
# team"). Using one noun for both produced "per campus and per team and
# program", which is why they are separate keys.
TIER_VOICE = {
    "church":    {"group": "ministry",         "unit": "ministry", "site": "campus"},
    "school":    {"group": "team and program", "unit": "team",     "site": "campus"},
    "nonprofit": {"group": "program",          "unit": "program",  "site": "location"},
}


def likely_tier(org_size, org_type: str = "") -> tuple[str, str]:
    """(tier, one-liner) from the size field, or ('', '') when unusable.

    Published bands. Returns empty rather than guessing when the value has no
    number in it -- the reply email's tier paragraph is the thing that proves a
    person read the form, and a wrong tier is worse than no tier.

    `org_type` selects the vocabulary. It defaults to the church wording, which
    is what every caller got before this argument existed, so an unconverted
    caller reads exactly as it used to rather than silently changing voice.
    """
    if org_size in (None, ""):
        return "", ""
    match = SIZE_DIGITS.search(str(org_size))
    if not match:
        return "", ""
    voice = TIER_VOICE.get(normalise_org_type(org_type) or "church",
                           TIER_VOICE["church"])
    size = int(match.group(0).replace(",", ""))
    if size < 125:
        # No org-specific noun in this one -- it is already neutral.
        return "Starter", ("a lean lineup built around the few pieces people "
                           "actually ask for.")
    if size <= 400:
        return "Growth", (f"a full lineup with room for {voice['group']} "
                          "sub-logos and seasonal drops.")
    return "Multi-Campus", (f"separate looks per {voice['site']} and per "
                            f"{voice['unit']}, under one store.")


CHURCH_WORDS = ("church", "ministry", "ministries", "chapel", "fellowship",
                "assembly", "tabernacle", "parish", "congregation", "worship")
SCHOOL_WORDS = ("school", "academy", "prep", "high", "elementary", "district",
                "college", "christian schools")


ORG_TYPES = ["church", "school", "nonprofit"]

TYPE_ALIASES = {
    "church": "church", "school": "school",
    "non-profit": "nonprofit", "nonprofit": "nonprofit",
    "non profit": "nonprofit", "charity": "nonprofit",
}


def normalise_org_type(value: str) -> str:
    """A form answer -> one of ORG_TYPES, or "" if it is not one of them.

    The metafield constrains the customer to three choices, but the strings
    they send ("Non-Profit") are not the keys the document vocabulary uses
    ("nonprofit"). Mapping here means the form's wording can change without
    touching the templates.
    """
    return TYPE_ALIASES.get(" ".join((value or "").split()).casefold(), "")


def guess_org_type(org_name: str) -> str:
    """church / school / nonprofit, inferred from the name.

    The form does not capture organisation type, and two thirds of the pipeline
    is not a church. This is a stopgap the setup notes call out explicitly;
    adding one radio row to the form retires it.
    """
    lowered = (org_name or "").lower()
    if any(word in lowered for word in CHURCH_WORDS):
        return "church"
    if any(word in lowered for word in SCHOOL_WORDS):
        return "school"
    return "nonprofit"


def list_leads(refresh: bool = False, include_closed: bool = True) -> tuple[list[dict], str]:
    """Every lead, merged with local stage, newest first."""
    snap, source = snapshot(refresh)
    if snap is None:
        return [], source

    stages = _load_stages()
    mine = own_addresses()
    now = datetime.now(timezone.utc)
    today = date.today()
    partners_by_email = {
        (record.get("poc_email") or "").strip().lower(): record
        for record in store.list_partners()
        if record.get("poc_email")
    }

    rows = []
    for lead in snap.get("leads", []):
        email = lead["email"].lower()
        note = (lead.get("note") or "").lower()
        if email in mine or any(address in note for address in mine):
            continue

        lead.setdefault("key", short_id(lead["id"]))
        local = stages.get(lead["id"], {})
        # The company IS the organisation, so its name is authoritative and
        # the local override only exists for the two leads captured before
        # this module read companies at all.
        org_name = lead.get("org_name") or local.get("org_name") or ""
        org_size = lead.get("org_size") or local.get("org_size") or ""

        # Precedence matters: a value Larry set by hand beats the customer's
        # answer, which beats a guess from the organisation name. The guess is
        # the one that put "Haven of Hope" down as a non-profit, so it is
        # labelled as a guess wherever it is shown.
        org_type = normalise_org_type(local.get("org_type") or "")
        org_type_source = "set" if org_type else ""
        if not org_type:
            org_type = normalise_org_type(lead.get("org_type") or "")
            org_type_source = "customer" if org_type else ""
        if not org_type and org_name:
            org_type = guess_org_type(org_name)
            org_type_source = "guess"

        # After the precedence chain, not before it -- the one-liner's
        # vocabulary depends on the resolved type.
        tier, one_liner = likely_tier(org_size, org_type)

        partner = partners_by_email.get(email)
        stage = local.get("stage") or ("Won" if partner else "New")
        age = days_open(lead, now)
        gap = reply_gap(lead, local)
        # A lead promoted before this field existed, or one whose stage was set
        # by the partner match rather than by hand, is answered but has no
        # stamp. Treat it as answered -- never as ignored.
        answered = bool(local.get("replied_at")) or stage not in AWAITING_REPLY_STAGES
        holding, hold_until, hold_reason = snooze_state(local, today)

        rows.append({
            **lead,
            "org_name": org_name,
            "org_size": org_size,
            "org_type": org_type,
            "org_type_source": org_type_source,
            "likely_tier": tier,
            "tier_one_liner": one_liner,
            "stage": stage,
            "moved_at": local.get("moved_at", ""),
            "note": local.get("note", "") or lead.get("note", ""),
            "days_open": age,
            "replied_at": local.get("replied_at", ""),
            "answered": answered,
            "reply_hours": gap,
            "reply_label": reply_label(gap),
            # Kept the promise: answered inside the window it commits him to.
            "on_time": bool(gap is not None and gap <= STALE_DAYS * 24),
            "snoozed_until": hold_until,
            "snooze_reason": hold_reason,
            "holding": holding,
            "hold_label": (date.fromisoformat(hold_until).strftime("%a %b %-d")
                           if holding else ""),
            # The promise the auto-reply makes on his behalf, every time. Only
            # an unanswered lead can break it -- see AWAITING_REPLY_STAGES --
            # and a deliberate hold is not a broken promise.
            "overdue": bool(age is not None and age >= STALE_DAYS
                            and not answered and not holding),
            "partner_id": store.partner_id(partner) if partner else "",
            "prompt": lead.get("prompt") or "",
            "logo_ref": lead.get("logo_ref") or "",
            "missing": [label for label, value in
                        (("company name", org_name), ("size", org_size))
                        if not value],
        })

    counts: dict[str, int] = {}
    for row in rows:
        key = (row["org_name"] or "").strip().casefold()
        if key:
            counts[key] = counts.get(key, 0) + 1
    for row in rows:
        key = (row["org_name"] or "").strip().casefold()
        row["duplicate_org"] = bool(key and counts.get(key, 0) > 1)

    if not include_closed:
        rows = [r for r in rows if r["stage"] in OPEN_STAGES]
    rows.sort(key=lambda r: r["created_at"], reverse=True)
    return rows, source


def find_lead(key: str, rows: list[dict] | None = None) -> dict | None:
    if rows is None:
        rows, _ = list_leads()
    return next((r for r in rows if r.get("key") == key), None)


def summary(rows: list[dict]) -> dict:
    answered = [r for r in rows if r.get("reply_hours") is not None]
    return {
        "total": len(rows),
        "open": sum(1 for r in rows if r["stage"] in OPEN_STAGES),
        # Waiting on Larry right now. This is the number that should be zero at
        # the end of a day; "open" includes leads where the ball is with them.
        "awaiting": sum(1 for r in rows if not r["answered"]),
        # Held on purpose. Still awaiting a reply, deliberately not overdue.
        "holding": sum(1 for r in rows if r.get("holding")),
        "overdue": sum(1 for r in rows if r["overdue"]),
        "on_time": sum(1 for r in answered if r["on_time"]),
        "answered": len(answered),
        "won": sum(1 for r in rows if r["stage"] == "Won"),
        "duplicates": sum(1 for r in rows if r.get("duplicate_org")),
        "needs_details": sum(1 for r in rows if r["missing"]),
        "with_logo": sum(1 for r in rows if r.get("logo_ref")),
        "by_stage": {stage: sum(1 for r in rows if r["stage"] == stage)
                     for stage in STAGES},
    }


# ----------------------------------------------------------------- promote --

def promote(lead: dict) -> tuple[dict | None, str]:
    """Create a partner record from a lead. (record, message).

    Refuses without a company name, because `store.partner_id()` derives the
    record id -- and the asset folder, and every generated filename -- from
    org_name. A partner created as "Jessica Rice" would have to be renamed
    later, and renaming migrates folders and rewrites filenames.
    """
    org_name = (lead.get("org_name") or "").strip()
    if not org_name:
        return None, ("This lead has no company name yet, and the partner id "
                      "is derived from it. Add the organisation name first.")

    if store.load(store.partner_id({"org_name": org_name})):
        return None, f"A partner record for {org_name!r} already exists."

    record = store.new_record()
    record.update({
        "org_name": org_name,
        # The lead already resolved this with the right precedence -- the
        # customer's own answer beats a guess from the name.
        "org_type": lead.get("org_type") or guess_org_type(org_name),
        "poc_name": lead.get("name") or "",
        "poc_email": lead.get("email") or "",
        "poc_phone": lead.get("phone") or "",
        # The logo they attached to the form. Dropping it here meant a partner
        # who HAD sent artwork arrived with none: no branded QR, no postcard
        # art, no sampled palette, and readiness() reporting them incomplete
        # while the file sat in Shopify Files the whole time.
        "logo_ref": lead.get("logo_ref") or "",
        # Deliberately NOT filled: plan, fees, margin_pct, signers, dates.
        # Those are negotiated on the call. Seeding them from a tier guess is
        # exactly the placeholder-terms problem store.readiness() exists to
        # catch -- the record must arrive visibly incomplete.
    })
    saved = store.save(record)
    set_stage(lead["id"], "Won", note=f"Promoted to partner {store.partner_id(saved)}")
    return saved, (f"Created partner record for {org_name}. "
                   "Fees, margin and signers are still blank — fill them in "
                   "from the call before generating anything.")
