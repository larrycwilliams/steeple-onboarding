"""Who is using this app, and what they are allowed to do.

Until now the answer to the first question was nobody-knows and the answer to
the second was everything. Tailscale membership was the whole access model:
being on the tailnet granted the morning pass, the Settings page, the Shopify
credentials and the button that deletes a partner, in equal measure. That was
a reasonable place to stop while exactly one person used it, and it stops
being reasonable the day a second person does.

**Identity comes from the network, not from a password.** Tailscale already
knows who owns every machine on the tailnet -- it had to, to let them talk --
so the app asks it rather than inventing a login of its own. `tailscale whois`
on the connecting IP returns the account the machine is signed in as. No
passwords to reset, no session secret to protect (the one in app.py is
hardcoded and would have to change first), no user table to keep in step with
reality, and nothing for somebody to write on a sticky note.

The cost is one subprocess per new IP, which is why the answers are cached;
and a dependency on the CLI being installed on the hub, which doc 23 requires
anyway.

**Roles are two and no more.** `owner` and `operator`. The owner-only list is
not invented here -- it is the "Ask Larry, do not decide" section of the
operator runbook, which Larry wrote when he was thinking about training
somebody rather than about permissions. A list written for that reason is a
better list than one written for this one.

**Enforcement is off until it is needed.** With one person on the tailnet a
refusal can only ever be a mistake, so the default is to resolve and record
identity while refusing nothing. Turning it on is one field in one file.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
from pathlib import Path

from .store import PARTNERS

# Who is who. Lives with the data rather than in the repo: it changes when
# somebody is hired or leaves, not when the code changes, and `partners/` is
# what the nightly backup copies. Being outside git also means an email
# address list is not in a GitHub history forever.
PEOPLE_PATH = PARTNERS / "_people.json"

ROLES = ("owner", "operator")

# launchd gives a process a minimal PATH, so `tailscale` alone frequently is
# not found under the service even though it works in a login shell. Candidates
# rather than PATH, in the order they are worth trying.
CLI_CANDIDATES = (
    "/usr/local/bin/tailscale",
    "/opt/homebrew/bin/tailscale",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
)

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _env() -> dict:
    """The environment the Tailscale CLI needs, which launchd does not supply.

    The macOS CLI is a shim that talks to the GUI app, and it finds it through
    the user's own directories. Run with a stripped environment it prints

        The Tailscale GUI failed to start: ... (Tailscale.CLIError error 3.)

    and then **exits 0**, which is how an outright failure reached the screen
    looking like a successful query that happened to know nothing.

    launchd gives a service PATH and little else -- no HOME on this hub -- so
    the value is filled in from the password database when it is missing,
    which is what expanduser does with no HOME set.
    """
    env = dict(os.environ)
    env.setdefault("HOME", os.path.expanduser("~"))
    env.setdefault("TMPDIR", "/tmp")
    return env


def _hostport(ip: str) -> str:
    """The ip:port pair whois wants, correct for IPv6 as well as IPv4.

    Not the cause of any bug so far -- the first identity failure looked like
    it might be, and was not; the address was IPv4 and correct. Hardened
    anyway, because Safari does prefer IPv6 when both exist, and
    `fd7a:...:6c22:443` is a string of eight colons with nothing to mark where
    the address ends and the port begins. IPv6 host and port wants brackets.

    A zone suffix (`%en0`) is stripped for a related reason: it identifies an
    interface on THIS machine, which means nothing to the machine being asked
    about.
    """
    ip = (ip or "").strip().split("%")[0]
    return f"[{ip}]:443" if ":" in ip else f"{ip}:443"

# A whois answer is good for this long. Machine ownership changes about never;
# this is only here so a page with twelve cards does not fork twelve processes.
CACHE_SECONDS = 600

_cache: dict[str, tuple[float, dict]] = {}
_cli_cache: list[str | None] = []


def cli() -> str | None:
    """The Tailscale binary, or None if this machine has no CLI."""
    if _cli_cache:
        return _cli_cache[0]
    found = None
    for candidate in CLI_CANDIDATES:
        if Path(candidate).exists():
            found = candidate
            break
    else:
        found = shutil.which("tailscale")
    _cli_cache.append(found)
    return found


def _now() -> float:
    return _dt.datetime.now().timestamp()


def whois(ip: str) -> dict:
    """Ask Tailscale who owns the machine at this address.

    Returns login, display name and machine name, plus `error` when the
    question could not be answered -- which is a different thing from being
    answered "nobody", and the caller has to tell them apart.
    """
    ip = (ip or "").strip()
    if not ip:
        return {"login": "", "name": "", "machine": "", "error": "no address"}

    hit = _cache.get(ip)
    if hit and _now() - hit[0] < CACHE_SECONDS:
        return hit[1]

    binary = cli()
    if not binary:
        answer = {"login": "", "name": "", "machine": "",
                  "error": "the Tailscale command is not installed here"}
        _cache[ip] = (_now(), answer)
        return answer

    try:
        # whois wants an ip:port pair. The port is not used to identify
        # anything -- any port answers for the machine -- so a fixed one keeps
        # the cache key simple.
        result = subprocess.run([binary, "whois", _hostport(ip)],
                                capture_output=True, text=True, timeout=5,
                                env=_env())
    except (OSError, subprocess.SubprocessError) as exc:
        answer = {"login": "", "name": "", "machine": "", "error": str(exc)}
        _cache[ip] = (_now(), answer)
        return answer

    if result.returncode != 0:
        answer = {"login": "", "name": "", "machine": "",
                  "error": (result.stderr or "not on this tailnet").strip()}
        _cache[ip] = (_now(), answer)
        return answer

    answer = _parse_whois(result.stdout)
    if not answer["login"] and not answer["machine"] and result.stderr.strip():
        # The CLI exits 0 on at least one real failure, so a clean return code
        # is not evidence of anything. Anything on stderr with nothing usable
        # on stdout is a failure whatever the process claimed.
        answer["error"] = result.stderr.strip()[:200]
        _cache[ip] = (_now(), answer)
        return answer
    if not answer["login"] and not answer["machine"]:
        # Ran, exited cleanly, told us nothing. Every other path here fills in
        # `error`, and this one used to not -- which is how a failure reached
        # the screen with no reason attached and cost an evening. A diagnostic
        # field that is empty on the one failure nobody predicted is not a
        # diagnostic field.
        answer["error"] = (f"asked about {_hostport(ip)} and got no answer back"
                           + (f": {result.stdout.strip()[:120]}" if result.stdout.strip() else ""))
    _cache[ip] = (_now(), answer)
    return answer


def _parse_whois(text: str) -> dict:
    """Pull the fields out of whois output.

    The output is two indented blocks -- the machine, then the user -- of
    `Name:`-style lines. `Name:` therefore appears twice and means different
    things, so the parser tracks which block it is in rather than matching
    field names alone. Anything it does not recognise is ignored; an unknown
    line is not an error.
    """
    machine = user = ""
    login = ""
    block = ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("machine:"):
            block = "machine"
            continue
        if low.startswith("user:"):
            block = "user"
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key in ("login name", "loginname") and value:
            login = value
        elif key == "name" and value:
            if block == "user":
                user = value
            elif block == "machine":
                machine = value
        elif key == "display name" and value and not user:
            user = value
    # Not every version prints "Login Name". The common shape puts the account
    # in the user block's plain `Name:`, and some put it on the machine. A
    # login is recognisable -- it has an @ in it -- so this never guesses at a
    # value that is not one.
    if not login and "@" in user:
        login = user
    if not login and "@" in machine:
        login = machine
    return {"login": login, "name": user, "machine": machine, "error": ""}


def config() -> dict:
    """The people file, or the shape of one when it does not exist yet."""
    blank = {"enforce": False, "people": {}}
    if not PEOPLE_PATH.exists():
        return blank
    try:
        data = json.loads(PEOPLE_PATH.read_text("utf8"))
    except (json.JSONDecodeError, OSError):
        # An unreadable people file must not lock anybody out of anything.
        # Failing open here is the safe direction: the alternative is an app
        # nobody can use because a JSON comma is wrong.
        return blank
    people = data.get("people")
    if not isinstance(people, dict):
        people = {}
    return {"enforce": bool(data.get("enforce")),
            "people": {str(k).strip().lower(): v for k, v in people.items()}}


def _save(data: dict) -> bool:
    PEOPLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PEOPLE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), "utf8")
    tmp.replace(PEOPLE_PATH)
    return True


def add_person(login: str, name: str = "", role: str = "operator") -> tuple[bool, str]:
    """Put somebody in the people file, creating it if this is the first one."""
    login = (login or "").strip().lower()
    if "@" not in login:
        return False, ("That is not a Tailscale login — it should be the email "
                       "address the machine is signed in as.")
    if role not in ROLES:
        return False, f"{role!r} is not a role. Use owner or operator."
    data = config()
    data["people"][login] = {"name": (name or "").strip() or login, "role": role}
    _save(data)
    return True, f"{login} added as {role}."


def remove_person(login: str) -> tuple[bool, str]:
    login = (login or "").strip().lower()
    data = config()
    if login not in data["people"]:
        return False, f"{login} is not in the people file."
    owners = [k for k, v in data["people"].items()
              if str(v.get("role", "")).lower() == "owner"]
    if owners == [login]:
        # Removing the last owner while enforcement is on locks everybody out
        # of Settings, which is the page you would need to fix it from.
        return False, ("That is the only owner. Add another owner first, or "
                       "turn enforcement off.")
    data["people"].pop(login)
    _save(data)
    return True, f"{login} removed."


def set_enforce(on: bool) -> tuple[bool, str]:
    data = config()
    if on and not any(str(v.get("role", "")).lower() == "owner"
                      for v in data["people"].values()):
        return False, ("Nobody is an owner yet, so turning this on would refuse "
                       "everyone. Add yourself as owner first.")
    data["enforce"] = bool(on)
    _save(data)
    return True, ("Roles are enforced now." if on else
                  "Roles are recorded but no longer enforced.")


def who(remote_addr: str) -> dict:
    """Everything the app needs about the person making this request."""
    settings = config()
    enforced = settings["enforce"]
    people = settings["people"]

    if (remote_addr or "") in LOOPBACK:
        # Someone at the hub's own keyboard, or the app calling itself. Both
        # already imply shell access to the machine holding every secret in
        # the system, so there is nothing left for a role to protect.
        return _identity("", "on the hub", "", "owner", True, "local", enforced, "")

    found = whois(remote_addr)
    login = (found["login"] or "").lower()

    if not login:
        # Unresolved. With enforcement off this changes nothing; with it on,
        # owner-only actions are refused, because "I could not tell who you
        # are" is not a reason to hand over the Shopify credentials.
        return _identity("", found["machine"] or "an unrecognised machine", "",
                         "operator", False, "unknown", enforced, found["error"])

    entry = people.get(login)
    if entry is None:
        # Known to Tailscale, unknown to the people file. Before anyone has
        # written that file, everyone on the tailnet is the owner -- which is
        # exactly today's behaviour, kept until somebody decides otherwise.
        role = "operator" if people else "owner"
        return _identity(login, found["name"], found["machine"], role,
                         bool(people) is False, "tailnet", enforced, "")

    role = str(entry.get("role") or "operator").lower()
    if role not in ROLES:
        role = "operator"
    name = str(entry.get("name") or found["name"] or login)
    return _identity(login, name, found["machine"], role, True, "people", enforced, "")


def _identity(login, name, machine, role, known, source, enforced, error) -> dict:
    return {"login": login, "name": name, "machine": machine, "role": role,
            "known": known, "source": source, "enforced": enforced,
            "error": error, "is_owner": role == "owner",
            # What gets written onto a record. A login where there is one, a
            # machine name where there is not, and never a blank pretending to
            # be a person.
            "stamp": login or (name if source == "local" else ""),
            "stamp_name": name or login}


def may(identity: dict, need: str = "owner") -> bool:
    """Is this person allowed to do a thing of this kind?"""
    if not identity.get("enforced"):
        return True
    if need == "owner":
        return bool(identity.get("is_owner"))
    return True


def refusal(identity: dict) -> str:
    """Why they were refused, in a sentence an operator can act on."""
    if identity.get("source") == "unknown":
        detail = identity.get("error") or "this machine is not on the tailnet"
        return ("That one is owner-only, and the app could not work out who you "
                f"are — {detail}. Ask Larry.")
    return ("That one is owner-only — Settings, the Shopify connection, and "
            "deleting or renaming a partner. Ask Larry.")
