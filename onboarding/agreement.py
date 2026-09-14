"""Where deals go quiet: the signed agreement.

Step 10 of the operator runbook is "stop until the signed agreement comes
back", and until now nothing watched that stop. A partner sitting unsigned for
five weeks looked exactly like a partner signed yesterday -- the record carries
no trace of either -- so the only thing standing between a stalled deal and a
forgotten one was somebody remembering to wonder.

Three states, deliberately no more:

    not sent   nothing has gone out, or nothing has been recorded
    waiting    it is out and unsigned, with an age and eventually a red flag
    returned   signed, back, done

**The sent date is usually not typed in.** The agreement travels as the
attachment on the welcome email, so if that email is ticked as sent (see
onboarding/sent.py) this reads the date from there rather than asking for it
twice. That is an inference, so it is labelled as one on screen -- "sent with
the welcome email on 14 Sep" -- and anyone who sent the agreement some other
way can set the date explicitly, which then wins.

Stored beside the tick and for the same reasons:

    partners/_agreement/<pid>.json

A folder under partners/, so store.list_partners() (which globs
partners/*.json) cannot mistake it for a partner, and so the nightly backup
covers it. Never on the partner record, which store.save() rewrites in full
every time the organisation name changes.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from . import sent as sent_log
from .store import PARTNERS

AGREEMENTS = PARTNERS / "_agreement"

# How long an unsigned agreement is allowed to sit before the screen turns red.
#
# Seven days, and the number is a judgement rather than a discovery: a church
# board that meets on Sunday needs one weekend to get a signature, and a week
# is long enough that chasing does not feel like nagging while being short
# enough that a stall is caught in the month it happened. One line to change.
CHASE_AFTER_DAYS = 7

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _path(pid: str) -> Path | None:
    ref = _UNSAFE.sub("-", str(pid or "").strip()).strip("-")
    return AGREEMENTS / f"{ref}.json" if ref else None


def _blank(pid: str) -> dict:
    return {"pid": pid, "sent_at": "", "sent_note": "", "returned_at": "",
            "returned_note": "", "chase_on": "", "chase_reason": "", "log": []}


def _read(pid: str) -> dict:
    path = _path(pid)
    if path is None or not path.exists():
        return _blank(pid)
    try:
        data = json.loads(path.read_text("utf8"))
    except (json.JSONDecodeError, OSError):
        return _blank(pid)
    blank = _blank(pid)
    for key, value in blank.items():
        data.setdefault(key, value)
    return data


def _write(pid: str, data: dict) -> bool:
    path = _path(pid)
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf8")
    tmp.replace(path)
    return True


def _date(value: str) -> _dt.date | None:
    try:
        return _dt.date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _label(value: str) -> str:
    day = _date(value)
    return day.strftime("%-d %b %Y") if day else ""


def _check_day(value: str, field: str, future_ok: bool = False) -> tuple[_dt.date | None, str]:
    day = _date(value)
    if day is None:
        return None, f"{value!r} is not a date this understands (YYYY-MM-DD)."
    if not future_ok and day > _dt.date.today():
        return None, f"That {field} is in the future."
    return day, ""


def state(pid: str) -> dict:
    """Everything the screens need about one partner's agreement."""
    data = _read(pid)
    today = _dt.date.today()

    # The welcome email carries the agreement, so its tick dates this too --
    # unless somebody has said otherwise, in which case they are right and this
    # is not.
    explicit = data.get("sent_at") or ""
    tick = sent_log.status("welcome", pid)
    sent_at = explicit or (tick["at"] if tick["sent"] else "")
    source = "set" if explicit else ("welcome" if sent_at else "")

    returned_at = data.get("returned_at") or ""
    sent_day, returned_day = _date(sent_at), _date(returned_at)

    if returned_at:
        stage = "returned"
    elif sent_at:
        stage = "waiting"
    else:
        stage = "not_sent"

    days = (today - sent_day).days if (sent_day and stage == "waiting") else 0
    chase_day = _date(data.get("chase_on") or "")
    holding = bool(chase_day and chase_day > today and stage == "waiting")
    # A chase date does what Hold until does on the pipeline: it does not stop
    # the clock, it stops the red. The age keeps counting, because how long
    # this has really been out is the number that matters when it finally
    # comes up.
    overdue = stage == "waiting" and days >= CHASE_AFTER_DAYS and not holding

    return {
        "pid": pid,
        "stage": stage,
        "sent": bool(sent_at),
        "sent_at": sent_at,
        "sent_date": sent_at[:10],
        "sent_label": _label(sent_at),
        "sent_source": source,
        "sent_note": data.get("sent_note") or "",
        "returned": bool(returned_at),
        "returned_at": returned_at,
        "returned_label": _label(returned_at),
        "returned_note": data.get("returned_note") or "",
        "days": days,
        "overdue": overdue,
        "holding": holding,
        "chase_on": data.get("chase_on") or "",
        "chase_label": _label(data.get("chase_on") or ""),
        "chase_reason": data.get("chase_reason") or "",
        "turnaround": ((returned_day - sent_day).days
                       if (sent_day and returned_day) else None),
        "log": data.get("log") or [],
    }


def _stamp(data: dict, action: str, when: str, note: str) -> None:
    data["log"].append({"at": _dt.datetime.now().replace(microsecond=0).isoformat(),
                        "action": action, "for": when, "note": note})


def mark_sent(pid: str, when: str = "", note: str = "") -> tuple[bool, str]:
    """Record that the agreement went out, when it did not go with the welcome.

    Posted, handed over at a meeting, sent from somebody's own Mail: all
    perfectly normal, and none of them leave a trace anywhere else.
    """
    day, problem = _check_day(when or _dt.date.today().isoformat(), "date")
    if problem:
        return False, problem
    data = _read(pid)
    data["sent_at"] = day.isoformat()
    data["sent_note"] = (note or "").strip()
    _stamp(data, "sent", data["sent_at"], data["sent_note"])
    if not _write(pid, data):
        return False, "Could not write the agreement record."
    return True, f"Agreement recorded as sent {_label(data['sent_at'])}."


def mark_returned(pid: str, when: str = "", note: str = "") -> tuple[bool, str]:
    """The signed copy is back. This is the mark that unblocks everything after."""
    day, problem = _check_day(when or _dt.date.today().isoformat(), "date")
    if problem:
        return False, problem

    current = state(pid)
    sent_day = _date(current["sent_at"])
    if sent_day and day < sent_day:
        return False, (f"That is before the agreement went out "
                       f"({current['sent_label']}).")

    data = _read(pid)
    if not current["sent"]:
        # Not a refusal -- a signed copy can come back that nobody recorded
        # sending -- but leaving the send blank would make the record say a
        # thing was signed that was never issued. Stamp both, same day, and
        # say so in the note rather than inventing an earlier date.
        data["sent_at"] = day.isoformat()
        data["sent_note"] = "recorded when the signed copy came back"
        _stamp(data, "sent", data["sent_at"], data["sent_note"])
    elif current["sent_source"] == "welcome":
        # The date was being read from the welcome-email tick. Freeze it here,
        # so clearing that tick later cannot silently re-date a signed deal.
        data["sent_at"] = current["sent_at"]
        data["sent_note"] = data.get("sent_note") or "sent with the welcome email"

    data["returned_at"] = day.isoformat()
    data["returned_note"] = (note or "").strip()
    data["chase_on"] = ""
    data["chase_reason"] = ""
    _stamp(data, "returned", data["returned_at"], data["returned_note"])
    if not _write(pid, data):
        return False, "Could not write the agreement record."
    return True, f"Signed agreement recorded, {_label(data['returned_at'])}."


def clear(pid: str, field: str, note: str = "") -> tuple[bool, str]:
    """Take back a sent or returned mark. The log keeps what it said."""
    if field not in ("sent", "returned"):
        return False, f"{field!r} is not something this tracks."
    data = _read(pid)
    key = f"{field}_at"
    if not data.get(key):
        return False, f"Nothing was recorded as {field}."
    _stamp(data, f"cleared {field}", data[key], (note or "").strip())
    data[key] = ""
    data[f"{field}_note"] = ""
    if not _write(pid, data):
        return False, "Could not write the agreement record."
    return True, f"The {field} mark is cleared."


def set_chase(pid: str, when: str, reason: str = "") -> tuple[bool, str]:
    """Hold the red until a date, the way the pipeline holds a lead.

    Their board meets on the 20th; chasing on the 12th is noise. The age keeps
    counting either way -- this suppresses the flag, not the fact.
    """
    data = _read(pid)
    if not (when or "").strip():
        data["chase_on"] = ""
        data["chase_reason"] = ""
        _stamp(data, "cleared chase", "", "")
        _write(pid, data)
        return True, "Chase date cleared."
    day, problem = _check_day(when, "date", future_ok=True)
    if problem:
        return False, problem
    data["chase_on"] = day.isoformat()
    data["chase_reason"] = (reason or "").strip()
    _stamp(data, "chase", data["chase_on"], data["chase_reason"])
    if not _write(pid, data):
        return False, "Could not write the agreement record."
    return True, f"Holding the chase until {_label(data['chase_on'])}."


def outstanding(partners: list[dict]) -> list[dict]:
    """Every partner still waiting on a signature, oldest first.

    Sorted by age rather than by name on purpose: the list is a queue, and the
    one at the top is the one that has been waiting longest.
    """
    rows = []
    for record in partners:
        current = state(record.get("id") or "")
        if current["stage"] != "waiting":
            continue
        current["org_name"] = record.get("org_name") or record.get("id")
        rows.append(current)
    rows.sort(key=lambda row: (-row["days"], row["org_name"]))
    return rows
