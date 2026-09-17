"""The lead-to-live path as data, and what each partner's next step is.

Named workflow rather than path because app.py uses `path` as a local variable
in two routes, and a module that a function can shadow is a trap waiting for
someone in a hurry.

[[claude/ops/17-lead-to-live-store]] has described this path since 8 September
and nothing in the app has ever known about it. The nav grew a tab per tool --
Partners, Dashboard, Readiness, Statements, Pipeline, Discovery, Traveler,
Tools -- which is a toolbox, not an order of work. Anyone who has not done it
half a dozen times has to remember the sequence themselves, and the one stage
with the most tooling behind it (the Traveler, thirteen steps) is reachable
only by already knowing to click it.

Two things live here.

PHASES is the path, grouped for a human: five phases over doc 17's fourteen
stages. It drives the home page, so the map and the doc cannot drift without
somebody noticing.

next_step() answers "what does THIS partner need now" -- derived from
onboarding.readiness, never from a stored position. That is the whole design
decision. A wizard remembers which button you pressed; this reads what is
actually true. After a week in which a postcard went to print against an empty
collection and a signed-looking agreement carried last week's line-up, a
progress marker that can disagree with reality is not worth having.

It does not ENFORCE the order. Steps get skipped for good reasons, and an app
that refuses to let you generate a package because the logo is not in yet is
an app you fight.
"""
from __future__ import annotations

from . import readiness, store

# Ordered. The first rule that matches is the partner's next step.
#
# Each rule: (section, check, level, label, endpoint, blocking)
# `level` None means "any finding at all for that check".
# `blocking` marks a step that is waiting on somebody else -- it gets no
# button, because there is nothing for the operator to press.
RULES = [
    ("Record", "fields", "fail", "Fill in the record", "edit_partner", False),
    ("Artwork", "logo", "fail", "Add a usable logo", "edit_partner", False),
    ("Package", "generated", "fail", "Generate the package", "generate", False),
    ("Package", "built from", "fail", "Generate again — it was built from blanks",
     "generate", False),
    ("Package", "artwork", "fail", "Generate again — it has no partner mark on it",
     "generate", False),
    ("Agreement", "welcome email", "warn", "Send the welcome email",
     "partner_email", False),
    ("Agreement", "signed", "fail", "Chase the signed agreement", "edit_partner", False),
    ("Agreement", "signed", "warn", "Waiting on the signed agreement", "", True),
    ("Storefront", "collection", "fail", "Create the storefront",
     "partner_storefront", False),
    ("Storefront", "vendor rule", "fail", "Fix the collection's vendor rule",
     "readiness_page", False),
    ("Storefront", "redirect", "fail", "Fix the QR redirect", "readiness_page", False),
    ("Storefront", "published", "fail", "Publish the collection",
     "partner_storefront", False),
    ("Storefront", "products", "fail", "Build the products", "traveler_index", False),
]

# Endpoints that take a partner id. The rest are whole-app screens -- the
# Traveler and Readiness are not per-partner, and pretending otherwise in the
# template produced a conditional nobody would want to edit later.
PID_ROUTES = {"edit_partner", "generate", "partner_email", "partner_storefront",
              "partner_files"}


DONE = {
    "label": "Ready to launch",
    "detail": "Every check passes. Send the Launch Week Kit and the postcards "
              "are safe to print.",
    "endpoint": "partner_files",
    "blocking": False,
    "done": True,
}


def next_step(entry: dict) -> dict:
    """The one thing this partner needs next. `entry` is a readiness assessment.

    Stage 9 of doc 17 -- "nothing else starts until the signed agreement is
    back" -- is expressed by where the agreement rules sit in RULES, above
    every storefront rule. The order of this list IS the policy.
    """
    findings = entry.get("findings") or []
    for section, check, level, label, endpoint, blocking in RULES:
        for finding in findings:
            if finding["section"] != section or finding["check"] != check:
                continue
            if level and finding["level"] != level:
                continue
            return {
                "label": label,
                "detail": finding["detail"],
                "fix": finding.get("fix", ""),
                "endpoint": endpoint,
                "pid_route": endpoint in PID_ROUTES,
                "blocking": blocking,
                "done": False,
            }
    return dict(DONE, pid_route=DONE["endpoint"] in PID_ROUTES)


# The path itself. `stages` are doc 17's numbers, kept so the two can be read
# against each other.
PHASES = [
    {
        "key": "lead",
        "name": "Lead",
        "stages": "1–4",
        "blurb": "A form submission lands, you answer it inside one business "
                 "day, you have the call, and you send the written "
                 "recommendation they take to their board.",
        "links": [
            ("Pipeline", "pipeline_page", "every open lead and the reply clock"),
            ("Discovery", "discovery_index", "the twelve questions, and walk-ins"),
        ],
    },
    {
        "key": "agreement",
        "name": "Agreement",
        "stages": "5–9",
        "blurb": "Promote the lead to a partner, fill the record, generate the "
                 "package, send the welcome email — then wait. Nothing past "
                 "here starts until the signed agreement is back.",
        "links": [
            ("New partner", "new_partner", "for a walk-in with no lead behind it"),
            ("Partners", "index", "every record, and what each one is missing"),
        ],
    },
    {
        "key": "store",
        "name": "Store",
        "stages": "10–11",
        "blurb": "The collection, its VENDOR EQUALS rule, and the /go/ redirect "
                 "the printed QR codes point at. The rule is the single most "
                 "expensive thing on this path to get wrong.",
        "links": [
            ("Readiness", "readiness_page", "record against what is live in Shopify"),
            ("Redirect CSV", "redirects", "every partner's redirect, for a bulk import"),
        ],
    },
    {
        "key": "products",
        "name": "Products",
        "stages": "12",
        "blurb": "The Traveler: thirteen steps per product pair, repeated. This "
                 "is the stage that actually consumes the two weeks.",
        "links": [
            ("Traveler", "traveler_index", "start a run, or pick up a half-finished one"),
        ],
    },
    {
        "key": "live",
        "name": "Live, then ongoing",
        "stages": "13–14",
        "blurb": "Collection live, kit sent, and only now are the postcards safe "
                 "to print. After that it is the weekly audit and the quarterly "
                 "payout.",
        "links": [
            ("Dashboard", "dashboard_page", "sales by collection"),
            ("Statements", "statements_page", "quarterly payouts"),
            ("Tools", "tools_page", "the documents and the runbook"),
        ],
    },
]


def board(records: list[dict] | None = None) -> dict:
    """Everything the home page shows. One Shopify round trip for all partners."""
    records = records if records is not None else store.list_partners()
    assessed = readiness.assess_all(records)
    for entry in assessed:
        entry["next"] = next_step(entry)
    blocked = [e for e in assessed if not e["next"]["done"] and not e["next"]["blocking"]]
    waiting = [e for e in assessed if e["next"]["blocking"]]
    ready = [e for e in assessed if e["next"]["done"]]
    return {
        "partners": assessed,
        "needing_you": blocked,
        "waiting": waiting,
        "ready": ready,
    }
