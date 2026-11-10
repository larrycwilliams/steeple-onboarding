"""The program scorecard (doc 69, phase 3): one card per church or school.

Every partner's program on one screen -- the deal, the money, the paperwork
and the store -- with whatever needs Larry's attention said in words, red
first. It answers "how is each program doing?" the way Home answers "what
needs me today?".

Nothing here is new data. Each fact comes from the module that already owns
it, so the scorecard can never disagree with the page that fixes the problem:

    plan and fees        the partner record; terms.mismatches() vs terms.json
    fees billed / paid   dashboard.fees()          (the Dashboard's billing panel)
    agreement            agreement.state()         (the agreement panel)
    launch               the record's launch_date
    store                the Shopify catalog, through dashboard.vendor_map()
    sales and giveback   dashboard.rollup()        (the same maths as statements)
    statement sent       sent.status("statement")  (the Statements tick)
    open orders          the Shopify order cache

Reads saved files only, like Home. A missing number is a dash, never $0.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from . import agreement, dashboard, sent, shopify_sales, statement, store, terms

# A closed quarter's statement is expected within this many days of its end.
STATEMENT_DUE_DAYS = 30
DONE_FULFILMENT = {"FULFILLED", "RESTOCKED"}
RANK = {"red": 0, "amber": 1, "ok": 2}


def _today() -> date:
    return date.today()


def _date(value) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _day(value) -> str:
    """'Oct 1, 2026' for a stored YYYY-MM-DD, or the text as given."""
    d = _date(value)
    return d.strftime("%b %-d, %Y") if d else (value or "—")


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"${value:,.0f}" if abs(value) >= 1000 else f"${value:,.2f}"


def _fee(value) -> float | None:
    try:
        text = str(value or "").replace("$", "").replace(",", "").strip()
        return float(text) if text else None
    except ValueError:
        return None


def _real(records: list[dict]) -> list[dict]:
    """Partners, minus the regression-check seed record (doc 01)."""
    return [r for r in records if not r.get("_seed_check")]


def _catalog_counts(snap: dict, records: list[dict]) -> dict[str, dict]:
    """{pid: {"live", "draft", "no_cost"}} through the SAME vendor mapping as the
    Dashboard, so a product is never counted for two partners."""
    mapping, _ = dashboard.vendor_map(records)
    out: dict[str, dict] = {}
    for product in snap.get("catalog") or []:
        record = mapping.get(dashboard.vendor_key(product.get("vendor", "")))
        if record is None:
            continue
        row = out.setdefault(store.partner_id(record), {"live": 0, "draft": 0, "no_cost": 0})
        status = (product.get("status") or "").upper()
        if status == dashboard.CATALOG_STATUS:
            row["live"] += 1
            if product.get("unit_cost") is None:
                row["no_cost"] += 1
        elif status == "DRAFT":
            row["draft"] += 1
    return out


def _open_orders(snap: dict, records: list[dict]) -> dict[str, int]:
    mapping, _ = dashboard.vendor_map(records)
    has_field = any("fulfillment" in o for o in snap.get("orders") or [])
    out: dict[str, int] = {}
    if not has_field:
        return out
    for order in snap.get("orders") or []:
        if order.get("test") or order.get("cancelled"):
            continue
        if (order.get("fulfillment") or "").upper() in DONE_FULFILMENT:
            continue
        pids = set()
        for line in order.get("lines") or []:
            record = mapping.get(dashboard.vendor_key(line.get("vendor", "")))
            if record:
                pids.add(store.partner_id(record))
        for pid in pids:
            out[pid] = out.get(pid, 0) + 1
    return out


def build(now: datetime | None = None) -> dict:
    """Everything /programs draws."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone().date() if now.tzinfo else _today()
    records = _real(store.list_partners())
    snap = shopify_sales.load_cache() or {}
    have_shopify = bool(snap)

    rollup = dashboard.rollup(snap, records) if have_shopify else {"partners": []}
    this_q = rollup.get("quarter") or dashboard.current_quarter()
    last_q = statement.last_complete_quarter(now)
    last_q_end = statement.bounds(last_q)[1]
    statement_due = last_q_end + timedelta(days=STATEMENT_DUE_DAYS)

    sales_by_pid = {p["partner_id"]: p for p in rollup.get("partners") or [] if p.get("partner_id")}
    fees_by_pid = {r["pid"]: r for r in dashboard.fees(records)["rows"]}
    by_name = {(r.get("org_name") or "").casefold(): store.partner_id(r) for r in records}
    mismatch_by_pid = {}
    for m in terms.mismatches(records):
        pid = m.get("id") or by_name.get((m.get("org") or "").casefold(), "")
        if pid:
            mismatch_by_pid[pid] = m
    catalog = _catalog_counts(snap, records) if have_shopify else {}
    open_orders = _open_orders(snap, records) if have_shopify else {}

    cards = []
    for record in records:
        pid = store.partner_id(record)
        flags: list[tuple[str, str]] = []          # (severity, words)

        # The deal
        plan = record.get("plan") or "—"
        setup, monthly = _fee(record.get("setup_fee")), _fee(record.get("monthly_fee"))
        margin = dashboard.negotiated_pct(record)
        mm = mismatch_by_pid.get(pid)
        if mm:
            flags.append(("amber", "Terms differ from the " + plan + " plan: " + "; ".join(mm.get("diffs") or [])
                          + ". Fine if negotiated — check it is."))
        if margin is None:
            flags.append(("amber", "No giveback rate on file, so no giveback can be worked out."))

        # Fees billed and paid
        fee = fees_by_pid.get(pid, {})
        launch = _date(record.get("launch_date"))
        launched = bool(launch and launch <= today)
        if fee.get("months_behind"):
            n = fee["months_behind"]
            flags.append(("red", f"Monthly fee {n} month{'s' if n != 1 else ''} behind "
                                 f"(paid through {_day(fee.get('paid_through'))})."))
        elif launched and (monthly or 0) > 0 and not fee.get("billing"):
            flags.append(("amber", f"Live but the {_money(monthly)}/mo fee is not being billed yet."))
        if fee.get("startup_sent") and not fee.get("startup_paid"):
            flags.append(("amber", f"Setup invoice sent {_day(fee['startup_sent'])}, not marked paid."))

        # Agreement
        ag = agreement.state(pid)
        if ag["overdue"]:
            flags.append(("red", f"Agreement out {ag['days']} days — chase."))
        elif ag["stage"] == "not_sent" and launched:
            flags.append(("amber", "Live with no agreement recorded as sent."))

        # Store
        cat = catalog.get(pid, {"live": 0, "draft": 0, "no_cost": 0})
        if have_shopify:
            if launched and cat["live"] == 0:
                flags.append(("red", "Launched, but nothing is live in the store"
                                     + (f" ({cat['draft']} in draft)." if cat["draft"] else ".")))
            elif not launched and cat["draft"] and not cat["live"]:
                flags.append(("ok", f"{cat['draft']} products staged in draft."))
            if cat["no_cost"]:
                flags.append(("amber", f"{cat['no_cost']} live product{'s' if cat['no_cost'] != 1 else ''} "
                                       "with no cost — their giveback cannot be worked out."))
        if not launch:
            flags.append(("amber", "No launch date on the record."))

        # Sales, giveback, statement
        sales = sales_by_pid.get(pid) or {}
        q_now = next((q for q in sales.get("quarters") or [] if q["quarter"] == this_q), None)
        q_last = next((q for q in sales.get("quarters") or [] if q["quarter"] == last_q), None)
        ref = sent.statement_ref(pid, statement.slug(last_q))
        st = sent.status("statement", ref)
        statement_needed = bool(q_last and q_last.get("revenue"))
        if statement_needed and not st["sent"]:
            late = today > statement_due
            flags.append(("red" if late else "amber",
                          f"{last_q} statement not sent"
                          + (f" — due by {statement_due:%b %-d}." if not late else
                             f" — was due {statement_due:%b %-d}.")))
        if q_now and q_now.get("uncosted_revenue"):
            flags.append(("amber", f"{_money(q_now['uncosted_revenue'])} of {this_q} sales has no cost yet."))

        worst = min((s for s, _ in flags), key=lambda s: RANK[s], default="ok")
        cards.append({
            "pid": pid,
            "name": record.get("org_name") or pid,
            "short": record.get("short_name") or "",
            "org_type": (record.get("org_type") or "").capitalize(),
            "plan": plan,
            "setup": _money(setup), "monthly": _money(monthly) + ("/mo" if monthly is not None else ""),
            "margin": f"{margin:g}%" if margin is not None else "—",
            "launch": launch.strftime("%b %-d, %Y") if launch else "—",
            "launch_state": ("live" if launched else "upcoming" if launch else "unset"),
            "days_to_launch": (launch - today).days if launch and not launched else None,
            "billing": {
                "setup": ("paid " + _day(fee["startup_paid"]) if fee.get("startup_paid") else
                          "invoiced " + _day(fee["startup_sent"]) if fee.get("startup_sent") else "not invoiced"),
                "monthly": ("paid through " + _day(fee["paid_through"]) if fee.get("paid_through") else
                            "billing since " + _day(fee["recurring_started"]) if fee.get("recurring_started") else
                            "not billing"),
                "behind": fee.get("months_behind") or 0,
            },
            "agreement": {"stage": ag["stage"], "label": {
                "returned": "Signed " + (ag["returned_label"] or ""),
                "waiting": f"Out {ag['days']} days",
                "not_sent": "Not sent"}.get(ag["stage"], ag["stage"]), "overdue": ag["overdue"]},
            "store": cat if have_shopify else None,
            "open_orders": open_orders.get(pid, 0) if have_shopify else None,
            "this_q": {"label": this_q,
                       "revenue": _money(q_now["revenue"]) if q_now else ("$0.00" if have_shopify else "—"),
                       "giveback": (_money(q_now["payout"]) if q_now and q_now.get("payout") is not None
                                    else ("—" if margin is None or not have_shopify else "$0.00"))},
            "last_q": {"label": last_q,
                       "revenue": _money(q_last["revenue"]) if q_last else ("$0.00" if have_shopify else "—"),
                       "giveback": (_money(q_last["payout"]) if q_last and q_last.get("payout") is not None
                                    else ("—" if margin is None or not have_shopify else "$0.00")),
                       "statement": ("sent " + _day(st["date"]) if st["sent"] else
                                     "not needed" if not statement_needed else "not sent"),
                       "statement_needed": statement_needed,
                       "statement_url": f"/partner/{pid}/statement/{statement.slug(last_q)}"},
            "flags": [{"level": s, "text": t} for s, t in sorted(flags, key=lambda f: RANK[f[0]])],
            "state": worst,
        })

    cards.sort(key=lambda c: (RANK[c["state"]], c["name"].casefold()))
    fees = dashboard.fees(records)
    giveback_now = sum(next((q["payout"] for q in (sales_by_pid.get(c["pid"]) or {}).get("quarters") or []
                             if q["quarter"] == this_q and q.get("payout") is not None), 0) for c in cards)
    return {
        "cards": cards,
        "red": sum(1 for c in cards if c["state"] == "red"),
        "amber": sum(1 for c in cards if c["state"] == "amber"),
        "total": len(cards),
        "live": sum(1 for c in cards if c["launch_state"] == "live"),
        "mrr_billed": _money(fees["mrr_billed"]),
        "mrr_current": _money(fees["mrr_current"]),
        "giveback_now": _money(giveback_now) if have_shopify else "—",
        "this_q": this_q, "last_q": last_q,
        "statements_open": sum(1 for c in cards if c["last_q"]["statement_needed"]
                               and not c["last_q"]["statement"].startswith("sent")),
        "have_shopify": have_shopify,
        "orders_at": (snap.get("pulled_at") or "")[:16].replace("T", " "),
    }
