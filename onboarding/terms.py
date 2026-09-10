"""Commercial terms, read from one file.

Pricing, the giveback and the contract term used to live in three places at
once -- per-partner json, hardcoded schema defaults, and prose typed into each
document. They disagreed: the schema defaulted margin_pct to 10 and both fees
to 0, so any partner created without editing those fields silently got terms
nobody had agreed to. This module is the single reader for terms.json.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TERMS_PATH = ROOT / "terms.json"

_FALLBACK = {
    "plans": {"Starter": {"setup_fee": 299, "monthly_fee": 39, "margin_pct": 30}},
    "giveback": {"pct": 30, "basis": "margin"},
    "term": {"cadence": "month to month", "notice_days": 90},
}


def load() -> dict:
    try:
        return json.loads(TERMS_PATH.read_text())
    except Exception:
        return dict(_FALLBACK)


def plans() -> dict:
    return load().get("plans", {})


def plan(name: str | None) -> dict:
    """Terms for one plan. Unknown or missing plan falls back to the first."""
    table = plans()
    if name and name in table:
        return dict(table[name])
    return dict(next(iter(table.values()))) if table else {}


def defaults_for(plan_name: str | None) -> dict:
    """The commercial fields a new partner on this plan should start with."""
    p = plan(plan_name)
    out = {}
    for key in ("setup_fee", "monthly_fee", "margin_pct"):
        if key in p:
            out[key] = str(p[key])
    return out


def context() -> dict:
    """Flat merge keys so documents and templates can render current terms
    without any of them holding their own copy of the numbers."""
    t = load()
    gb, tm = t.get("giveback", {}), t.get("term", {})
    ctx = {
        "terms_updated": t.get("updated", ""),
        "giveback_pct": gb.get("pct", ""),
        "giveback_basis": gb.get("basis", ""),
        "giveback_spoken": gb.get("spoken", ""),
        "giveback_example": gb.get("example", ""),
        "term_cadence": tm.get("cadence", ""),
        "term_notice_days": tm.get("notice_days", ""),
        "term_rationale": tm.get("rationale", ""),
        "launch_weeks": t.get("launch", {}).get("weeks", ""),
        "payout_frequency_default": t.get("payout", {}).get("frequency", ""),
    }
    for name, p in t.get("plans", {}).items():
        key = name.lower().replace("-", "_").replace(" ", "_")
        ctx[f"plan_{key}_setup"] = p.get("setup_fee", "")
        ctx[f"plan_{key}_monthly"] = p.get("monthly_fee", "")
        ctx[f"plan_{key}_margin"] = p.get("margin_pct", "")
    return ctx


def mismatches(records: list[dict]) -> list[dict]:
    """Partners whose stored commercial fields differ from their plan's terms.
    Reported, never auto-corrected -- a negotiated exception is legitimate and
    only the owner knows which is which."""
    out = []
    for r in records:
        want = plan(r.get("plan"))
        diffs = []
        for key, label in (("setup_fee", "setup"), ("monthly_fee", "monthly"), ("margin_pct", "margin")):
            if key not in want:
                continue
            have = str(r.get(key, "")).replace("%", "").replace("$", "").strip()
            exp = str(want[key])
            if have in ("", "None"):
                diffs.append(f"{label}: blank (plan says {exp})")
            else:
                try:
                    same = abs(float(have) - float(exp)) < 0.005
                except ValueError:
                    same = have == exp
                if not same:
                    diffs.append(f"{label}: {have} vs plan {exp}")
        if diffs:
            out.append({"id": r.get("id"), "org": r.get("org_name"), "plan": r.get("plan"), "diffs": diffs})
    return out
