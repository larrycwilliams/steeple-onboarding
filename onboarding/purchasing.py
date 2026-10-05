"""The Purchasing tab: run `ssorder` from the app.

Why this exists (doc 51): buying blanks from S&S Activewear was a terminal
step, and doc 46 already showed what happens to terminal steps -- they get
gone around. This puts the proposal, the test order, the order itself and
"the blanks arrived" on one page.

It covers two stages of an order and no more:

    Sourcing          open, paid, in-house lines with no blanks bought yet
    Awaiting product  blanks bought -- placed here, or marked as bought by
                      hand -- and not yet in the shop

Shape mirrors onboarding/clone.py:

    refresh()        the proposal. Reads Shopify and S&S, orders nothing.
    review(option)   `place` WITHOUT --commit: every guard runs and the exact
                     payload comes back, but nothing is sent.
    place(...)       starts the real order in the background.
    status()         what that background order is doing; records the result.

Decisions:

* ssorder is SUBPROCESSED, on this app's interpreter. It is stdlib-only, so
  no venv of its own. Every command is run with --json: human lines to
  stderr, one document to stdout, exit 0 only on success.
* THE APP DECIDES NOTHING ABOUT MONEY. Which option is recommended, whether a
  proposal is too old, whether a line was already bought, whether the address
  is still a placeholder -- all of that is ssorder's, and it is asked again at
  every step. The checks here are the ones only the app can make: that what
  is being placed is what was reviewed on this page, and that somebody typed
  yes.
* The order runs DETACHED, like the clone. It is one or two HTTP calls and
  would fit inside a request, but a gunicorn worker that dies between S&S
  accepting the order and the ledger being written is the one failure that
  costs real money, so the order does not live inside a worker.
* The page is drawn from a SNAPSHOT of the last proposal. Loading it never
  calls Shopify or S&S; only the Refresh button does. (One exception: the
  first look after a background order finishes re-plans once, so the lines
  just ordered move out of Sourcing.)
* State lives in cache/purchasing/, ONE FILE PER WRITER. The snapshot is only
  ever written whole, by refresh(); the review, the test result, the running
  job and the last result each have their own file. Two gunicorn workers
  therefore never read-change-write the same file, which is how an earlier
  draft lost a running order's record to a refresh that started before it.
  None of these files is the record: the ledger and the saved S&S answers in
  ~/Dev/ssorder/state/ are.
* WHICH CARD is chosen per order, never defaulted, when ssorder's config.json
  lists more than one way to pay (Larry, 2026-10-05: "pick every time"). The
  choice is made before Review, shown in the review box, and is part of the
  payload fingerprint -- so the card that pays is the card that was read.
* A review belongs to one proposal, identified by PO number AND build time.
  PO numbers are only as fine as a minute and a re-plan reuses the name, so
  the build time is what stops a review of one proposal being spent on
  another. It is passed to ssorder as --expect-created, which checks it again
  against the file it is about to send.

Nothing here raises. A non-zero exit is always reported as a failure.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSORDER_DIR = Path(os.environ.get("SSORDER_DIR", Path.home() / "Dev" / "ssorder"))
SCRIPT = SSORDER_DIR / "ssorder.py"

STATE = ROOT / "cache" / "purchasing"


def _file(name: str) -> Path:
    """A state file. Resolved at call time, so STATE is the only thing to redirect."""
    return STATE / name


PLAN_TIMEOUT = 120          # Shopify pages plus one S&S call per style
CALL_TIMEOUT = 90           # a test order, a dry run, a cancel
LEDGER_TIMEOUT = 150        # a Shopify read, plus ssorder's own 90 s wait for the ledger lock
HISTORY_KEEP = 30
START_GRACE_SECONDS = 20    # how long a job may exist before its process has a pid
MAX_RUN_SECONDS = 300       # two POSTs at ssorder's 30 s timeout is the real ceiling
CANCEL_WINDOW_MINUTES = 10  # S&S's own limit for cancelling by API

OPTION_KEYS = ("will_call", "ship", "split")          # what can be placed
OPTION_ORDER = ("will_call", "ship", "split", "hold")  # what is shown


# ------------------------------------------------------------------ helpers

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


def _parse(stamp: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _seconds_since(stamp: str) -> float | None:
    when = _parse(stamp)
    return None if when is None else (_now() - when).total_seconds()


def _minutes_since(stamp: str) -> int | None:
    seconds = _seconds_since(stamp)
    return None if seconds is None else int(seconds // 60)


def local_time(stamp: str) -> str:
    """'Oct 5, 9:14 AM' on the hub's clock. '' for anything unparseable."""
    when = _parse(stamp)
    if when is None:
        return ""
    when = when.astimezone()
    hour = when.strftime("%I").lstrip("0") or "12"
    return f"{when.strftime('%b')} {when.day}, {hour}:{when.strftime('%M %p')}"


def _read(name: str) -> dict:
    """A state file as a dict. {} when it is missing, unreadable, or not a dict."""
    try:
        data = json.loads(_file(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(name: str, data) -> bool:
    """Whole-file replace through a temp name no other process shares, so a
    reader never sees half a file and two writers never trip over one temp.
    Returns False instead of raising: a state file is never worth a 500."""
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        path = _file(name)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


@contextlib.contextmanager
def _flock(name: str):
    """Hold an flock on a state file for the length of the block. Blocking; the
    kernel releases it if this process dies, so it cannot be left behind."""
    fd = None
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        fd = os.open(_file(name), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)
    except OSError:
        pass                              # no lock is better than no page
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)


def _drop(*names: str) -> None:
    for name in names:
        try:
            _file(name).unlink()
        except OSError:
            pass


def load() -> dict:
    return _read("snapshot.json")


def history() -> list:
    try:
        rows = json.loads(_file("placed.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def setup_problems() -> list[str]:
    """What stops the tab working at all, checked without running anything."""
    problems = []
    if not SCRIPT.exists():
        return [f"ssorder is not installed at {SSORDER_DIR}. Unzip the package there "
                "(or set SSORDER_DIR) and reload."]
    if not (SSORDER_DIR / "config.json").exists():
        problems.append(f"{SSORDER_DIR / 'config.json'} is missing. Copy config.example.json "
                        "to config.json and fill in ship_to.")
    if not (SSORDER_DIR / ".env").exists():
        problems.append(f"{SSORDER_DIR / '.env'} is missing. It holds SS_ACCOUNT_NUMBER and "
                        "SS_API_KEY — copy .env.example and fill it in.")
    return problems


# ------------------------------------------------------------------ transport

def _env() -> dict:
    env = os.environ.copy()
    # launchd starts the app with no HOME. ssorder expands "~/Dev/..." for the
    # store's .env, so give it one rather than rely on the pwd fallback.
    env.setdefault("HOME", str(Path.home()))
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # ssorder talks HTTPS through urllib, which trusts whatever CA file this
    # Python was built to look for. Point it at the bundle the app already
    # ships with, so a missing system store reads as nothing worse than this.
    if not env.get("SSL_CERT_FILE"):
        try:
            import certifi
            env["SSL_CERT_FILE"] = certifi.where()
        except Exception:                                     # noqa: BLE001
            pass
    return env


def _argv(*args: str) -> list[str]:
    return [sys.executable, str(SCRIPT), *args, "--json"]


def _document(stdout: str) -> dict | None:
    try:
        doc = json.loads(stdout) if stdout.strip() else None
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def _interpret(code: int | None, stdout: str, stderr: str) -> dict:
    doc = _document(stdout)
    if doc is None:
        tail = "\n".join((stderr or stdout or "").strip().splitlines()[-8:])
        return {"ok": False, "code": code, "doc": None,
                "error": f"ssorder exited {code} without a result." + (f"\n{tail}" if tail else "")}
    # Both must agree. A zero exit with ok:false, or ok:true with a non-zero
    # exit, is a failure -- an ambiguous run never looks like success.
    ok = code == 0 and bool(doc.get("ok"))
    return {"ok": ok, "code": code, "doc": doc,
            "error": "" if ok else (doc.get("error") or f"ssorder exited {code}.")}


def _run(args: list[str], timeout: int) -> dict:
    """Run ssorder and return {ok, code, doc, error}. Never raises."""
    if not SCRIPT.exists():
        return {"ok": False, "code": None, "doc": None,
                "error": f"ssorder not found at {SCRIPT}."}
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              env=_env(), cwd=str(SSORDER_DIR))
    except subprocess.TimeoutExpired:
        return {"ok": False, "code": None, "doc": None,
                "error": f"ssorder did not finish within {timeout}s."}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "code": None, "doc": None,
                "error": f"Could not start ssorder: {exc}"}
    return _interpret(proc.returncode, proc.stdout, proc.stderr)


# ------------------------------------------------------------------ the proposal

def refresh() -> dict:
    """Build a fresh proposal. Reads Shopify and S&S; orders nothing.

    The snapshot is written whole and by nothing else. A new proposal has a
    new build time, so a review or a test of the old one stops matching and
    can no longer be shown or placed -- nothing has to be deleted for that.
    """
    # One refresh at a time, start to finish. Without this a plan that began
    # BEFORE an order could finish AFTER the order's own re-plan and put the
    # lines just bought back on screen as "to buy". Queued behind the lock,
    # the later request always plans last and so always sees the most.
    with _flock("refresh.lock"):
        old = load()
        problems = setup_problems()
        if problems:
            result = {"ok": False, "doc": None, "error": " ".join(problems)}
        else:
            result = _run(_argv("plan"), PLAN_TIMEOUT)
        doc = result["doc"] or {}
        snap = {
            "at": _stamp(),
            "ok": result["ok"],
            "error": result["error"],
            "proposal": doc.get("proposal") or {},
            "setup": doc.get("setup") or {},
            "saved": bool(doc.get("saved")),
        }
        if not result["ok"]:
            # Keep the last good picture on screen underneath the error, marked as
            # not orderable, rather than blanking the page because the network blinked.
            snap["proposal"] = old.get("proposal") or {}
            snap["setup"] = old.get("setup") or {}
            snap["saved"] = False
        _write("snapshot.json", snap)
        return snap


def _ident(snap: dict) -> tuple[str, str]:
    """(PO number, build time): what a review or a test is a review or a test OF."""
    prop = snap.get("proposal") or {}
    return prop.get("po", ""), prop.get("created", "")


def _matches(record: dict, snap: dict) -> bool:
    po, created = _ident(snap)
    return bool(po and created) and record.get("po") == po and record.get("created") == created


def _placeable(snap: dict, option: str) -> str:
    """'' when this option of this snapshot may be sent to ssorder, else why not."""
    prop = snap.get("proposal") or {}
    if not snap.get("ok") or not snap.get("saved") or not prop.get("lines"):
        return "There is no current proposal. Refresh first."
    if option not in OPTION_KEYS:
        return "Choose Will Call, Ship or Split."
    opt = (prop.get("options") or {}).get(option) or {}
    if not opt.get("available"):
        return f"“{option}” is not available on this proposal."
    limit = (snap.get("setup") or {}).get("proposal_max_age_minutes")
    age = _minutes_since(prop.get("created", ""))
    if limit is not None and age is not None and age > int(limit):
        return (f"This proposal is {age} minutes old (limit {limit}). Stock moves — "
                "refresh, then review again.")
    return ""


def test(option: str) -> dict:
    """Ask S&S for real totals. S&S creates a test order and cancels it."""
    snap = load()
    why = _placeable(snap, option)
    if why:
        return {"ok": False, "error": why}
    po, created = _ident(snap)          # fixed before the call; never re-read after it
    result = _run(_argv("test", po, "--option", option, "--expect-created", created, "--yes"),
                  CALL_TIMEOUT)
    _write("test.json", {"at": _stamp(), "po": po, "created": created, "option": option,
                         "ok": result["ok"], "error": result["error"],
                         "parts": (result["doc"] or {}).get("parts") or []})
    return {"ok": result["ok"], "error": result["error"]}


def cards(snap: dict) -> list:
    """The saved cards ssorder says an order may be paid with: [{"id", "label"}]."""
    listed = (snap.get("setup") or {}).get("payment_profiles") or []
    return [{"id": str(c.get("id", "")), "label": str(c.get("label", ""))}
            for c in listed if isinstance(c, dict) and c.get("id") not in (None, "")]


def review(option: str, payment: str = "") -> dict:
    """The dry run: every one of ssorder's guards, and the exact payload."""
    snap = load()
    why = _placeable(snap, option)
    payment = (payment or "").strip()
    choices = cards(snap)
    if not why and choices and payment not in {c["id"] for c in choices}:
        why = "Choose which card pays for this order."
    if why:
        _drop("review.json")
        return {"ok": False, "error": why}
    po, created = _ident(snap)          # fixed before the call; never re-read after it
    args = ["place", po, "--option", option, "--expect-created", created]
    if payment:
        args += ["--payment", payment]
    result = _run(_argv(*args), CALL_TIMEOUT)
    doc = result["doc"] or {}
    ok = bool(result["ok"] and doc.get("dry_run") and not doc.get("sent")
              and doc.get("po") == po and doc.get("option") == option
              # A card that was picked must be the card ssorder says it will use.
              # (With one card set the old way nothing is picked, and ssorder names it.)
              and (not payment or str(doc.get("payment") or "") == payment))
    if not result["ok"] and "choose which card" in (result["error"] or ""):
        # config.json gained cards since the page last looked at it.
        result["error"] += " Refresh the proposal so the page can offer them."
    _write("review.json", {
        "at": _stamp(), "po": po, "created": created, "option": option,
        "ok": ok, "error": result["error"],
        "label": doc.get("label", ""), "pieces": doc.get("pieces"),
        "subtotal": doc.get("subtotal"), "freight": doc.get("freight"),
        "payment_profile": bool(doc.get("payment_profile")),
        "payment": payment, "payment_label": doc.get("payment_label") or "",
        "ship_to": doc.get("ship_to") or {},
        "payloads": doc.get("payloads") or [],
        # ssorder's fingerprint of exactly these payloads -- address and payment
        # profile included. Handed back at Place, where ssorder recomputes it.
        "payload_sha": doc.get("payload_sha") or "",
    })
    return {"ok": ok and bool(doc.get("payload_sha")), "error": result["error"]}


def clear_review() -> None:
    _drop("review.json")


# ------------------------------------------------------------------ the order

def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def place(po: str, option: str, typed: str) -> dict:
    """Start the real order in the background. Returns {ok, error}.

    Refused unless it is exactly what the page showed: the proposal that is
    current (same PO, same build time), the option that was reviewed, and yes
    typed. ssorder is then told the build time too, and checks it against the
    proposal file it is about to send.
    """
    if status()["state"] == "running":
        return {"ok": False, "error": "An order is already being placed."}
    snap = load()
    why = _placeable(snap, option)
    if why:
        return {"ok": False, "error": why}
    rev = _read("review.json")
    if not rev.get("ok") or not rev.get("payload_sha"):
        return {"ok": False, "error": "Review the order first."}
    snap_po, created = _ident(snap)
    if po != snap_po or not _matches(rev, snap) or rev.get("option") != option:
        return {"ok": False, "error": "The proposal changed since it was reviewed. "
                                      "Review again, then place."}
    if (typed or "").strip().lower() != "yes":
        return {"ok": False, "error": "Type yes to place the order. Nothing was sent."}

    try:
        STATE.mkdir(parents=True, exist_ok=True)
        # A lock with no job behind it, older than a start takes, was left by a
        # worker killed in the instant between the two. Nothing is running.
        if (not _read("place.job") and
                time.time() - _file("place.lock").stat().st_mtime > START_GRACE_SECONDS):
            _drop("place.lock")
    except OSError:
        pass                                   # no lock file: the usual case
    try:
        fd = os.open(_file("place.lock"), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, _stamp().encode())
        os.close(fd)
    except FileExistsError:
        return {"ok": False, "error": "An order is already starting."}
    except OSError as exc:
        return {"ok": False, "error": f"Could not start the order: {exc}"}

    # From here the lock is ours. The job file goes down BEFORE the process is
    # started, so there is no moment at which an order is running and nothing
    # on disk says so.
    job = {"pid": None, "started": _stamp(), "po": po, "created": created, "option": option,
           "label": rev.get("label", ""), "subtotal": rev.get("subtotal"),
           "pieces": rev.get("pieces"), "payment_label": rev.get("payment_label", "")}
    _drop("place.out", "place.log", "place.last", "review.json")
    if not _write("place.job", job):
        _drop("place.lock")
        return {"ok": False, "error": "Could not record the order as started, so it was "
                                      "not started. Nothing was sent."}
    # The card goes exactly as it was reviewed. It is inside the fingerprint as
    # well, so a different one here would be refused by ssorder, not charged.
    pay = ["--payment", str(rev["payment"])] if rev.get("payment") else []
    argv = _argv("place", po, "--option", option, "--expect-created", created, *pay,
                 "--expect-payloads", rev["payload_sha"], "--commit", "--yes")
    # /bin/sh backgrounds the order and exits at once, and start_new_session
    # puts it in a session of its own: neither a gunicorn worker being recycled
    # nor launchd restarting the app (which signals the app's whole process
    # group) can kill it between S&S accepting the order and the ledger saying so.
    shell = (" ".join(shlex.quote(a) for a in argv) +
             f" > {shlex.quote(str(_file('place.out')))} 2> {shlex.quote(str(_file('place.log')))}"
             " & echo $!")
    try:
        proc = subprocess.run(["/bin/sh", "-c", shell], capture_output=True, text=True,
                              timeout=15, env=_env(), cwd=str(SSORDER_DIR),
                              start_new_session=True)
    except OSError as exc:                     # /bin/sh itself would not run: nothing started
        _drop("place.job", "place.lock")
        return {"ok": False, "error": f"Could not start the order: {exc}"}
    except Exception:                                         # noqa: BLE001
        # The launcher was started but did not report back in time. The order
        # may well be running, so the job stands and status() will find out.
        return {"ok": True, "pid": None}
    try:
        pid = int(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": True, "pid": None}       # started, pid unreadable: same thing
    _write("place.job", {**job, "pid": pid})   # if this fails the job still stands, pid-less
    return {"ok": True, "pid": pid}


def _running(job: dict, progress: list | None = None) -> dict:
    return {"state": "running", "started": job.get("started"), "po": job.get("po"),
            "progress": progress or []}


def status() -> dict:
    """{state: idle|running|done|failed, ...}. Records a finished order once."""
    job = _read("place.job")
    if not job:
        last = _read("place.last")
        if last:
            return {"state": "done" if last.get("ok") else "failed", **last}
        return {"state": "idle"}

    try:
        stdout = _file("place.out").read_text(errors="replace")
    except OSError:
        stdout = ""
    try:
        stderr = _file("place.log").read_text(errors="replace")
    except OSError:
        stderr = ""
    # ssorder prints its document as the very last thing it does, so a complete
    # document means it has finished -- whatever a (possibly reused) pid says.
    parsed = _document(stdout)
    if parsed is None:
        age = _seconds_since(job.get("started", "")) or 0
        pid = job.get("pid")
        # With no pid on record there is no asking whether it is alive. If the
        # shell got as far as opening place.out the order did start, so wait
        # out the ceiling rather than call a running order dead at 20 seconds.
        waiting = (_pid_alive(pid) if pid else
                   age < START_GRACE_SECONDS or _file("place.out").exists())
        if waiting and age < MAX_RUN_SECONDS:
            return _running(job, [l.strip() for l in stderr.splitlines() if l.strip()][-6:])
        # No document and no live process (or one that has run far too long):
        # it died mid-run, and something may already have been sent.

    # Finished. Two requests can notice that in the same moment (the page
    # polling, and somebody loading it); only the one that creates the claim
    # records the result, so the history gets one row.
    try:
        os.close(os.open(_file("place.claim"), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        if not _read("place.job"):                 # the other request has finished recording
            return status()
        try:
            fresh = (time.time() - _file("place.claim").stat().st_mtime) < 60
            if not fresh:                          # its owner died mid-record: take it over
                os.utime(_file("place.claim"))
        except OSError:
            fresh = False
        if fresh:
            return _running(job, ["Recording the result…"])
    except OSError:
        pass                                       # cannot claim; record anyway

    died = parsed is None
    result = _interpret(0 if (not died and parsed.get("ok")) else 1, stdout, stderr)
    doc = result["doc"] or {}
    record = {
        "finished": _stamp(), "started": job.get("started"),
        "po": job.get("po"), "option": job.get("option"), "label": job.get("label", ""),
        "subtotal": job.get("subtotal"), "pieces": job.get("pieces"),
        "payment_label": job.get("payment_label", ""),
        "ok": result["ok"], "error": "" if result["ok"] else result["error"],
        "sent": bool(doc.get("sent")), "partial": bool(doc.get("partial")),
        "placed": doc.get("placed") or [],
        # died: no document at all. unknown: it is not known whether S&S has the order.
        "died": died, "unknown": died or bool(doc.get("unknown")),
    }
    _write("place.last", record)
    rows = history()
    if not (rows and rows[-1].get("po") == record["po"]
            and rows[-1].get("started") == record["started"]):   # never the same order twice
        _write("placed.json", (rows + [record])[-HISTORY_KEEP:])
    _drop("place.job", "place.lock", "place.claim")
    # The lines just ordered are still in the proposal on screen. Re-plan so
    # they move to "awaiting product"; if that fails the old picture stays up
    # with its error, and place.last still says what happened.
    refresh()
    return {"state": "done" if record["ok"] else "failed", **record}


def dismiss_last() -> None:
    _drop("place.last")


def cancel(order_number: str) -> dict:
    """Cancel at S&S (their API allows it for ten minutes) and release the lines."""
    order_number = (order_number or "").strip()
    if not order_number.isalnum():
        return {"ok": False, "error": "That is not an S&S order number."}
    result = _run(_argv("cancel", order_number, "--commit", "--yes"), CALL_TIMEOUT)
    doc = result["doc"] or {}
    if result["ok"]:
        refresh()
    return {"ok": result["ok"], "error": result["error"],
            "released": doc.get("released", 0), "still_held": doc.get("still_held", 0),
            "other_orders": doc.get("other_orders") or []}


# ------------------------------------------------------------------ the ledger

def _keys(keys) -> list[str]:
    return [k for k in dict.fromkeys(str(k).strip() for k in keys or []) if k]


def _ledger(command: str, keys) -> dict:
    """Run one ledger edit on exact lines, then re-plan so the page agrees with it."""
    keys = _keys(keys)
    if not keys:
        return {"ok": False, "error": "Tick at least one line.", "changed": 0}
    args = [command]
    for key in keys:
        args += ["--key", key]
    result = _run(_argv(*args, "--commit"), LEDGER_TIMEOUT)
    changed = len((result["doc"] or {}).get("changed") or [])
    stale = ""
    if result["ok"] and changed and not refresh()["ok"]:
        stale = ("Saved, but the page could not be refreshed afterwards, so it still shows "
                 "the old picture.")
    return {"ok": result["ok"], "error": result["error"], "changed": changed, "stale": stale}


def mark(keys, undo: bool = False) -> dict:
    """Blanks for these lines were bought some other way (or, undo, were not)."""
    return _ledger("unmark" if undo else "mark", keys)


def receive(keys, undo: bool = False) -> dict:
    """The blanks for these lines are in the shop (or, undo, are not after all)."""
    return _ledger("unreceive" if undo else "receive", keys)


# ------------------------------------------------------------------ the page

def _groups(tracked: list, placed_by_po: dict) -> list:
    """Awaiting lines, grouped the way they will arrive: one box per S&S order."""
    groups: dict[str, dict] = {}
    for row in tracked:
        ss_order = str(row.get("ss_order") or "")
        by_hand = row.get("status") == "sourced" and not ss_order
        ident = "hand" if by_hand else f"{row.get('po', '')}|{ss_order}"
        g = groups.setdefault(ident, {
            "by_hand": by_hand, "po": str(row.get("po") or ""), "ss_order": ss_order,
            "at": row.get("at", ""), "lines": [], "pieces": 0,
            "detail": placed_by_po.get(row.get("po", "")) or {},
        })
        g["lines"].append(row)
        try:
            g["pieces"] += int(row.get("qty") or 0)
        except (TypeError, ValueError):
            pass
        if not g["at"] or str(row.get("at", "")) < g["at"]:
            g["at"] = str(row.get("at", ""))
    out = sorted(groups.values(), key=lambda g: (g["by_hand"], g["at"]))
    for g in out:
        g["when"] = local_time(g["at"])
        age = _minutes_since(g["at"])
        g["cancellable"] = (not g["by_hand"] and bool(g["ss_order"]) and "," not in g["ss_order"]
                            and age is not None and age < CANCEL_WINDOW_MINUTES)
        g["split"] = "," in g["ss_order"]
        g["no_number"] = not g["by_hand"] and not g["ss_order"]
    return out


def view() -> dict:
    """Everything the template needs. Reads files only; calls nothing remote
    (but see status(): the first look after an order finishes re-plans once)."""
    job = status()                       # records a finished background order first
    snap = load()
    prop = snap.get("proposal") or {}
    setup = snap.get("setup") or {}
    tracked = [t for t in prop.get("tracked") or [] if isinstance(t, dict)]
    wh = setup.get("pickup_warehouse") or "OH"

    age = _minutes_since(prop.get("created", "")) if prop.get("created") else None
    limit = setup.get("proposal_max_age_minutes")
    stale = bool(prop.get("lines")) and (
        not snap.get("ok") or not snap.get("saved") or
        (limit is not None and age is not None and age > int(limit)))

    options = []
    for key in OPTION_ORDER:
        opt = (prop.get("options") or {}).get(key)
        if opt:
            options.append({"key": key, "recommended": key == prop.get("recommended"),
                            "placeable": key in OPTION_KEYS and bool(opt.get("available")),
                            **{k: opt.get(k) for k in ("label", "available", "freight", "note")}})
    labels = {o["key"]: o["label"] for o in options}

    rev = _read("review.json")
    review_now = rev if _matches(rev, snap) and not stale else {}
    tst = _read("test.json")
    test_now = ({**tst, "when": local_time(tst.get("at", "")),
                 "label": labels.get(tst.get("option"), tst.get("option", ""))}
                if _matches(tst, snap) else {})

    placed_by_po = {}
    for record in history():
        for part in record.get("placed") or []:
            if isinstance(part, dict):
                placed_by_po[part.get("po_number", "")] = {
                    "orders": [o for o in part.get("orders") or [] if isinstance(o, dict)]}

    awaiting = [t for t in tracked if t.get("status") != "received"]
    received = [{**t, "when": local_time(t.get("received_at", ""))}
                for t in tracked if t.get("status") == "received"]

    stores = setup.get("stores") or []
    recommended = prop.get("recommended")
    return {
        "snap": snap, "prop": prop, "setup": setup, "wh": wh, "po": prop.get("po", ""),
        "problems": setup_problems(),
        "refreshed": local_time(snap.get("at", "")),
        "proposal_time": local_time(prop.get("created", "")),
        "age": age, "limit": limit, "stale": stale,
        "options": options,
        "recommended_label": labels.get(recommended, ""),
        # Pre-pick only what ssorder recommends. On a Hold nothing is picked:
        # the rule is "no automatic pick", and a pre-ticked radio is a pick.
        "default_option": recommended if recommended in OPTION_KEYS else "",
        "can_order": any(o["placeable"] for o in options),
        "review": review_now, "test": test_now, "job": job,
        "cards": cards(snap),
        "groups": _groups(awaiting, placed_by_po),
        "received": received,
        "counts": prop.get("counts") or {},
        # Order links only when there is one store: a second store's orders
        # live under a different admin domain than the app's own.
        "shop_domain": os.environ.get("SHOPIFY_STORE", "") if len(stores) == 1 else "",
        "ssorder_dir": str(SSORDER_DIR),
        "cancel_window": CANCEL_WINDOW_MINUTES,
        "stock_cap": 500,
    }
