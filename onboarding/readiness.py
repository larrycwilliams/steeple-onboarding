"""Is this partner actually ready to launch? One answer, from every source.

The whole of 15 September was one failure wearing different clothes. Mail
drafting on a Mac nobody was sitting at. A four-and-a-half minute build behind
a blank screen. A logo plate decided silently and wrongly. A storefront that
had drifted from its record. In every case the app knew the answer and never
said it, so the first thing to notice was a printed postcard, a live call, or
a QR code that would not scan.

The checks themselves already existed, scattered: `store.readiness` for the
record, `store.logo_problem` for the artwork, `agreement.state`, `sent.status`,
`package.load_manifest`, and the Shopify comparison that lived only in
`tools/storefront_audit.py`. Five things to remember to run, in five places,
which in practice means nobody runs any of them.

This gathers them. `tools/storefront_audit.py` now reads from here too, so the
tool and the screen cannot drift into disagreeing about what "ready" means --
which would be this same bug again, one level up.

ONE round trip for every partner, not one each. The Shopify part is a single
aliased query plus one redirect fetch; nine partners cost the same as one. A
board that took ten seconds to paint would be a board nobody opens.

Nothing here writes, and nothing here raises. A partner whose Shopify lookup
fails reports that as a finding rather than taking the page down with it.
"""
from __future__ import annotations

import re

from . import agreement, package, sent as sent_log, store
from .shopify_pull import _load_env, configured
from .storefront import _gql

OK, WARN, FAIL = "ok", "warn", "fail"

# The order they appear on the screen. Roughly the order they happen in real
# life, so a half-built partner reads as a progress bar rather than a list.
SECTIONS = ["Record", "Artwork", "Agreement", "Package", "Storefront"]

_ALIAS = re.compile(r"[^a-z0-9]+")


def _alias(pid: str) -> str:
    return "c_" + _ALIAS.sub("_", pid.lower()).strip("_")


def _finding(section: str, level: str, check: str, detail: str,
             fix: str = "") -> dict:
    return {"section": section, "level": level, "check": check,
            "detail": detail, "fix": fix}


# ---------------------------------------------------------------- Shopify

def _live_query(records: list[dict]) -> str:
    parts = []
    for record in records:
        handle = (record.get("collection_handle") or "").strip().lower()
        if not handle:
            continue
        parts.append(
            f'  {_alias(store.partner_id(record))}: collectionByHandle('
            f'handle: "{handle}") {{ id title productsCount {{ count }} '
            f'ruleSet {{ rules {{ column relation condition }} }} '
            f'resourcePublicationsV2(first: 20) {{ edges {{ node '
            f'{{ isPublished publication {{ id }} }} }} }} }}')
    if not parts:
        return ""
    parts.append('  redirects: urlRedirects(first: 250) '
                 '{ edges { node { id path target } } }')
    return "query {\n" + "\n".join(parts) + "\n}"


def live(records: list[dict]) -> dict:
    """{pid: collection|None, "_redirects": [...], "_error": str}. Never raises."""
    _load_env()
    if not configured():
        return {"_error": "Shopify is not connected. Settings > Shopify.",
                "_redirects": []}
    query = _live_query(records)
    if not query:
        return {"_error": "", "_redirects": []}
    result = _gql(query)
    if not result["ok"]:
        return {"_error": result["error"], "_redirects": []}
    data = result.get("data") or {}
    out: dict = {"_error": "",
                 "_redirects": [e["node"] for e in
                                ((data.get("redirects") or {}).get("edges") or [])]}
    for record in records:
        pid = store.partner_id(record)
        out[pid] = data.get(_alias(pid))
    return out


# ------------------------------------------------------------- the checks

def _record_findings(record: dict) -> list[dict]:
    missing = store.readiness(record)
    if missing:
        return [_finding("Record", FAIL, "fields",
                         f"{len(missing)} still unset: " + ", ".join(missing),
                         "Open the partner form.")]
    return [_finding("Record", OK, "fields", "every required field is set")]


def _artwork_findings(record: dict) -> list[dict]:
    problem = store.logo_problem(record)
    if problem:
        return [_finding("Artwork", FAIL, "logo", problem,
                         "Re-upload on the partner form. A PDF is fine.")]
    logo = store.resolve_logo(record)
    if not logo:
        return [_finding("Artwork", FAIL, "logo", "no logo on the record",
                         "Ask the partner for their mark; a PDF is fine.")]

    out = [_finding("Artwork", OK, "logo", record.get("logo_path", "").split("/")[-1]
                    or "on file")]
    manifest = package.load_manifest(record) or {}
    check = manifest.get("logo_check") or {}
    share, worst = check.get("share"), check.get("worst_region")
    if share is None:
        out.append(_finding("Artwork", WARN, "on the card",
                            "not measured yet — generate the package",
                            "Press Generate on the partner record."))
    elif check.get("plated"):
        out.append(_finding(
            "Artwork", WARN, "on the card",
            f"{share:.0%} of the mark reads"
            + (f", faintest patch {worst:.0%}" if worst is not None else "")
            + " — set on a plate so the rest still shows",
            "A reversed (white) version from the partner reads ~100% and "
            "needs no plate. Better looking, and one line in an email."))
    else:
        out.append(_finding("Artwork", OK, "on the card",
                            f"{share:.0%} of the mark reads, no plate needed"))
    return out


def _agreement_findings(record: dict) -> list[dict]:
    pid = store.partner_id(record)
    state = agreement.state(pid)
    welcome = sent_log.status("welcome", pid)

    out = []
    if welcome["sent"]:
        out.append(_finding("Agreement", OK, "welcome email",
                            f"sent {welcome['label']}"))
    else:
        out.append(_finding("Agreement", WARN, "welcome email", "not sent",
                            "Partner record → Welcome email."))

    if state["returned"]:
        turn = state["turnaround"]
        out.append(_finding("Agreement", OK, "signed",
                            f"returned {state['returned_label']}"
                            + (f", {turn} day turnaround" if turn is not None else "")))
    elif state["overdue"]:
        out.append(_finding(
            "Agreement", FAIL, "signed",
            f"out {state['days']} days, nothing back "
            f"(chase after {agreement.CHASE_AFTER_DAYS})",
            "Chase it, or set a hold date if waiting is the right call."))
    elif state["sent"]:
        detail = f"out {state['days']} days"
        if state["holding"]:
            detail += f", holding until {state['chase_label']}"
        out.append(_finding("Agreement", WARN, "signed", detail))
    else:
        out.append(_finding("Agreement", WARN, "signed", "not sent yet"))
    return out


def _package_findings(record: dict) -> list[dict]:
    manifest = package.load_manifest(record)
    if manifest is None:
        return [_finding("Package", FAIL, "generated", "never built",
                         "Press Generate on the partner record.")]
    incomplete = manifest.get("incomplete") or []
    out = [_finding("Package", OK, "generated",
                    f"{len(manifest.get('files') or {})} files")]
    if incomplete:
        out.append(_finding(
            "Package", FAIL, "built from", f"{len(incomplete)} field"
            f"{'' if len(incomplete) == 1 else 's'} were unset when it was "
            f"built: {', '.join(incomplete)}",
            "Fill them in and generate again — these documents go to a church."))
    if manifest.get("logo_problem"):
        out.append(_finding("Package", FAIL, "artwork",
                            "built with no partner mark on it",
                            "Fix the logo, then generate again."))
    qr = manifest.get("qr_check") or {}
    if qr.get("verified") is False:
        out.append(_finding("Package", FAIL, "branded QR",
                            "did not decode when it was tested",
                            "Use the plain QR, or re-generate."))
    return out


def _storefront_findings(record: dict, collection, redirects) -> list[dict]:
    handle = (record.get("collection_handle") or "").strip().lower()
    vendor = (record.get("vendor") or "").strip()
    slug = (record.get("redirect_slug") or "").strip().lower()
    stored_gid = (record.get("collection_gid") or "").strip()

    if not handle:
        return [_finding("Storefront", FAIL, "collection",
                         "no collection handle on the record")]
    if collection is None:
        return [_finding("Storefront", FAIL, "collection",
                         f"nothing live at /collections/{handle}",
                         "Partner record → Create storefront.")]

    out = [_finding("Storefront", OK, "collection", collection["title"])]

    rules = ((collection.get("ruleSet") or {}).get("rules")) or []
    vendor_rules = [r for r in rules
                    if r["column"] == "VENDOR" and r["relation"] == "EQUALS"]
    if not vendor_rules:
        out.append(_finding(
            "Storefront", FAIL, "vendor rule",
            "none — a manual collection" if not rules else
            "has rules, none of them VENDOR EQUALS",
            "Sales attribution falls back to guessing the owner from the "
            "name. That put $2,973.76 on the wrong partner once already."))
    elif vendor and vendor_rules[0]["condition"] != vendor:
        out.append(_finding(
            "Storefront", FAIL, "vendor rule",
            f"matches {vendor_rules[0]['condition']!r}, record says {vendor!r}",
            "Exact string. Singular and plural are different partners."))
    else:
        out.append(_finding("Storefront", OK, "vendor rule",
                            f"VENDOR EQUALS {vendor_rules[0]['condition']!r}"))

    count = (collection.get("productsCount") or {}).get("count", 0)
    if count:
        out.append(_finding("Storefront", OK, "products", str(count)))
    else:
        out.append(_finding("Storefront", FAIL, "products",
                            "0 — the QR resolves to an empty page",
                            "Do not hand out postcards yet."))

    pubs = [e["node"] for e in
            ((collection.get("resourcePublicationsV2") or {}).get("edges") or [])]
    if any(p.get("isPublished") for p in pubs):
        live_count = sum(1 for p in pubs if p.get("isPublished"))
        out.append(_finding("Storefront", OK, "published",
                            f"{live_count} channel{'' if live_count == 1 else 's'}"))
    else:
        out.append(_finding("Storefront", FAIL, "published",
                            "on no sales channel — the page 404s"))

    want_path = f"/go/{slug}" if slug else ""
    want_target = f"/collections/{handle}"
    if not slug:
        out.append(_finding("Storefront", WARN, "redirect",
                            "no slug on the record; the QR points straight at "
                            "the collection"))
    else:
        forward = [r for r in redirects if r["path"] == want_path]
        backwards = [r for r in redirects if r["target"] == want_path]
        if forward and forward[0]["target"] == want_target:
            out.append(_finding("Storefront", OK, "redirect",
                                f"{want_path} → {want_target}"))
        elif forward:
            out.append(_finding("Storefront", FAIL, "redirect",
                                f"{want_path} → {forward[0]['target']}",
                                f"Should point at {want_target}."))
        elif backwards:
            out.append(_finding(
                "Storefront", FAIL, "redirect",
                f"BACKWARDS: {backwards[0]['path']} → {backwards[0]['target']}",
                "This breaks the collection's own URL too. It cannot be "
                "edited — Shopify refuses with \"Target can't redirect to "
                "another redirect\". Delete it and create it the other way."))
        else:
            out.append(_finding("Storefront", FAIL, "redirect",
                                f"{want_path} does not exist",
                                "Every QR code printed with it is dead."))

    if not stored_gid:
        out.append(_finding("Storefront", WARN, "record ↔ live",
                            "collection_gid is empty",
                            "Built by hand rather than with Create storefront?"))
    elif stored_gid != collection["id"]:
        out.append(_finding("Storefront", FAIL, "record ↔ live",
                            f"record says {stored_gid}, live is {collection['id']}"))
    else:
        out.append(_finding("Storefront", OK, "record ↔ live", "agree"))
    return out


# ------------------------------------------------------------------ public

def assess(record: dict, snapshot: dict | None = None) -> dict:
    """Every check for one partner. `snapshot` is one `live()` result."""
    snapshot = snapshot if snapshot is not None else live([record])
    pid = store.partner_id(record)

    findings = []
    findings += _record_findings(record)
    findings += _artwork_findings(record)
    findings += _agreement_findings(record)
    findings += _package_findings(record)
    if snapshot.get("_error"):
        findings.append(_finding("Storefront", WARN, "shopify",
                                 snapshot["_error"]))
    else:
        findings += _storefront_findings(record, snapshot.get(pid),
                                         snapshot.get("_redirects") or [])

    counts = {level: sum(1 for f in findings if f["level"] == level)
              for level in (OK, WARN, FAIL)}
    return {
        "pid": pid,
        "name": record.get("org_name") or pid,
        "findings": findings,
        "counts": counts,
        # "Ready" means nothing is FAIL. A warning is something to know, not
        # something that stops a launch -- a plated logo is not wrong, it is
        # just not the best available artwork.
        "ready": counts[FAIL] == 0,
        "level": FAIL if counts[FAIL] else (WARN if counts[WARN] else OK),
    }


def assess_all(records: list[dict] | None = None) -> list[dict]:
    records = records if records is not None else store.list_partners()
    snapshot = live(records)
    return [assess(record, snapshot) for record in records]
