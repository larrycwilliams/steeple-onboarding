"""Did it actually go out? The one thing this app cannot work out by itself.

Everything outbound here is a draft. The welcome email and the quarterly
statement both open in Mail and stop, because the last look before something
reaches a partner is the whole point of the design. That leaves a gap nothing
else closes: the app knows a draft was *opened*, and has no idea whether the
person in front of it pressed send, closed the window, or went to lunch.

So the tick is manual, and it asserts exactly one thing -- a person is saying
this went out. Nothing sets it automatically. A tick set by a button press
would be the app guessing again, and a wrong "sent" is worse than no record:
it is the one direction a dashboard must never lie in. Someone chasing a
partner who never got their statement is a bad quarter; someone *not* chasing
because the app claimed it was sent is a lost one.

Where it lives, and why not on the partner record:

    partners/_sent/<kind>/<ref>.json

`store.save()` recomputes a partner's id from the organisation name and
migrates the folder when that name changes, so anything kept on the record
gets rewritten on every save and travels with a rename. This is an event --
it happened on a date, to one version of one document -- and events do not
belong in a record that is edited in place.

`partners/` is what the nightly backup copies, so a folder underneath it is
covered for free. `store.list_partners()` globs `partners/*.json`, which a
folder cannot match, so this stays invisible to the partner list for the same
reason `_history/` does.

Nothing is ever deleted. Clearing a tick appends the clearing to the log and
leaves the file behind. With no login there is no "who" to record, so the
least this can do is never lose the "when".
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path

from .store import PARTNERS

SENT = PARTNERS / "_sent"

# Kinds are enumerated on purpose. A typo'd kind would silently create a
# third folder that nothing ever reads, and the tick would look like it had
# simply failed to save.
KINDS = {
    "welcome": "Welcome email",
    "statement": "Quarterly statement",
}

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def statement_ref(pid: str, quarter_slug: str) -> str:
    """One tick per partner per quarter -- Q2 sent is not Q3 sent."""
    return f"{pid}__{quarter_slug}"


def _safe(value: str) -> str:
    return _UNSAFE.sub("-", str(value or "").strip()).strip("-")


def _path(kind: str, ref: str) -> Path | None:
    if kind not in KINDS:
        return None
    ref = _safe(ref)
    if not ref:
        return None
    return SENT / kind / f"{ref}.json"


def _now() -> _dt.datetime:
    return _dt.datetime.now().replace(microsecond=0)


def _label(stamp: str, precise: bool = True) -> str:
    """A backdated tick says the day and nothing more.

    The clock time is only known when the tick is set at the moment of
    sending. Printing "at 12:00 pm" on a tick somebody set the next morning
    would be the app inventing a detail, which is the habit this whole module
    exists to break.
    """
    try:
        when = _dt.datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return stamp or ""
    if not precise:
        return when.strftime("%-d %b %Y")
    return when.strftime("%-d %b %Y at %-I:%M %p").replace("AM", "am").replace("PM", "pm")


def _blank(kind: str, ref: str) -> dict:
    return {"kind": kind, "ref": ref, "sent_at": "", "precise": True,
            "note": "", "by": "", "by_name": "", "log": []}


def _read(kind: str, ref: str) -> dict:
    path = _path(kind, ref)
    if path is None or not path.exists():
        return _blank(kind, ref)
    try:
        data = json.loads(path.read_text("utf8"))
    except (json.JSONDecodeError, OSError):
        # A tick is a convenience, never a gate. An unreadable file reads as
        # "not sent", which is the answer that makes someone go and look.
        return _blank(kind, ref)
    data.setdefault("log", [])
    data.setdefault("sent_at", "")
    data.setdefault("precise", True)
    data.setdefault("note", "")
    # Records written before the app knew who anybody was. They stay honest
    # about that rather than being backfilled with a guess.
    data.setdefault("by", "")
    data.setdefault("by_name", "")
    return data


def _write(kind: str, ref: str, data: dict) -> bool:
    path = _path(kind, ref)
    if path is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), "utf8")
    tmp.replace(path)
    return True


def status(kind: str, ref: str) -> dict:
    """What the screens render: sent or not, when, and what was said about it."""
    data = _read(kind, ref)
    stamp = data.get("sent_at") or ""
    precise = bool(data.get("precise", True))
    return {
        "sent": bool(stamp),
        "at": stamp,
        "precise": precise,
        "label": _label(stamp, precise),
        "date": stamp[:10],
        "note": data.get("note") or "",
        "by": data.get("by") or "",
        "by_name": data.get("by_name") or "",
        "log": data.get("log") or [],
        "kind": kind,
        "ref": ref,
    }


def mark(kind: str, ref: str, note: str = "", when: str = "",
         by: str = "", by_name: str = "") -> tuple[bool, str]:
    """Record that this went out. `when` is a date (YYYY-MM-DD) if not today.

    Backdating is allowed because the tick usually gets set the morning after
    the send, and a tick that can only say "today" would be a second small
    lie sitting on top of the one this is here to remove.
    """
    if kind not in KINDS:
        return False, f"{kind!r} is not something this tracks."
    stamp = _now().isoformat()
    precise = True
    if when:
        try:
            day = _dt.date.fromisoformat(when.strip())
        except ValueError:
            return False, f"{when!r} is not a date this understands (YYYY-MM-DD)."
        if day > _dt.date.today():
            return False, "That date is in the future, so it has not been sent yet."
        if day == _dt.date.today():
            # The form seeds today, so the ordinary case arrives here with a
            # date rather than without one. Treat it as now: the clock time is
            # genuinely known, and throwing it away would make every same-day
            # tick look like a reconstruction.
            day = None
        if day is not None:
            # Midday rather than midnight, so that sorting puts it inside its
            # own day rather than on the boundary. The time is never shown for
            # a backdated tick -- see _label.
            stamp = _dt.datetime.combine(day, _dt.time(12, 0)).isoformat()
            precise = False

    data = _read(kind, ref)
    already = data.get("sent_at") or ""
    data["sent_at"] = stamp
    data["precise"] = precise
    data["note"] = (note or "").strip()
    data["by"] = by or ""
    data["by_name"] = by_name or by or ""
    data["log"].append({"at": _now().isoformat(), "action": "sent",
                        "for": stamp, "note": (note or "").strip(),
                        "by": by or ""})
    if not _write(kind, ref, data):
        return False, "Could not write the sent record."
    label = _label(stamp, precise)
    if already:
        return True, f"{KINDS[kind]} re-marked as sent {label}."
    return True, f"{KINDS[kind]} marked as sent {label}."


def clear(kind: str, ref: str, note: str = "", by: str = "") -> tuple[bool, str]:
    """Take the tick back. The file and its log stay."""
    if kind not in KINDS:
        return False, f"{kind!r} is not something this tracks."
    data = _read(kind, ref)
    if not data.get("sent_at"):
        return False, "That was not marked as sent."
    data["log"].append({"at": _now().isoformat(), "action": "cleared",
                        "was": data["sent_at"], "note": (note or "").strip(),
                        "by": by or ""})
    data["sent_at"] = ""
    data["note"] = ""
    data["by"] = ""
    data["by_name"] = ""
    if not _write(kind, ref, data):
        return False, "Could not write the sent record."
    return True, f"{KINDS[kind]} is no longer marked as sent."


def key(ref: str) -> str:
    """The ref as it is stored, for matching a tick back to a row."""
    return _safe(ref)
