#!/usr/bin/env python3
"""Opens a Mail draft on THIS Mac, on behalf of the app running on another one.

The problem it solves, in one sentence: `mail_draft.create_draft` runs
osascript inside the server process, so the draft window opens on whichever Mac
is running the app -- the hub -- and never on the laptop you are actually
sitting at. See claude/ops/29-where-mail-drafts-open.md.

The browser is the missing link. The page is served by the hub, but the browser
rendering it is on *this* Mac, so it can fetch the message from the hub and
hand it to a service listening here. That service is this file. The message
crosses the network once as data and becomes a real, editable Mail draft
locally -- not an .eml you have to Send Again.

Deliberately stdlib only and one file. It has to install on a laptop that runs
none of the app's dependencies: no Flask, no venv, no repo. Doc 23 is explicit
that the Air and the Neo "run nothing", and this is the smallest thing that can
be true while still being useful.

Run it:      python3 draft_helper.py
Install it:  ./install_draft_helper.command   (launchd, starts at login)
Check it:    curl http://127.0.0.1:5055/ping
"""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.parse
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("SS_DRAFT_PORT", "5055"))

# Loopback only, and not configurable. Binding anything else would put a
# "create a window on this Mac" endpoint on the network, and this service has
# no authentication -- the browser's same-origin rules and the loopback
# interface are the whole security model.
HOST = "127.0.0.1"

# Which pages may drive this. Any page in any tab can reach a loopback port, so
# without this an unrelated site could pop Mail windows. The worst case is
# noise rather than a leak -- the helper only ever opens a visible draft and
# never sends -- but the check costs nothing.
#
# The rule is about WHICH MACHINE, not which port. An early version allowlisted
# `http://100.119.56.54:5000` literally, which would have silently stopped
# working the day the app moved to port 5050 to get out of AirPlay's way -- an
# open decision in doc 23 -- and would have failed with nothing but a hidden
# button to show for it. So: loopback, or an address inside Tailscale's CGNAT
# range, on any port.
#
# 100.64.0.0/10 is the tailnet and nothing else. It is not routable on the
# public internet, so no web page can be served from it except Larry's own
# machines -- which is exactly the trust boundary doc 05 already chose when it
# decided the network would be this app's access control.
TAILNET = ipaddress.ip_network("100.64.0.0/10")
LOOPBACK_NAMES = {"localhost", "127.0.0.1", "::1"}


def origin_allowed(origin: str) -> bool:
    if not origin:
        return True                # a same-origin or non-CORS caller, e.g. curl
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if host in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host) in TAILNET
    except ValueError:
        return False               # a name, not an address -- not ours

MAX_BYTES = 64 * 1024 * 1024        # a statement with its PDF is ~100KB
LOG = Path.home() / "Library" / "Logs" / "steeple-draft-helper.log"

# Byte-for-byte the script mail_draft.py already uses on the hub, and it is
# duplicated on purpose: this file has to stand alone on a Mac with no repo.
# If one changes, change both -- the comments there explain every line of it.
#
#   - the body is passed as a FILE, because a 200KB HTML body with an embedded
#     letterhead is far past what osascript will take on a command line
#   - attachments are added AFTER the body is set, or Mail puts them above the
#     letterhead
#   - visible:true, and no send. Ever.
SCRIPT = '''
on run argv
    set theSubject to item 1 of argv
    set theRecipient to item 2 of argv
    set bodyPath to item 3 of argv
    set theBody to (read (POSIX file bodyPath) as «class utf8»)

    set attachmentPaths to {}
    if (count of argv) > 3 then
        repeat with i from 4 to (count of argv)
            set end of attachmentPaths to (item i of argv)
        end repeat
    end if

    tell application "Mail"
        set newMessage to make new outgoing message with properties ¬
            {subject:theSubject, html content:theBody, visible:true}
        tell newMessage
            if theRecipient is not "" then
                make new to recipient at end of to recipients ¬
                    with properties {address:theRecipient}
            end if
            repeat with p in attachmentPaths
                try
                    make new attachment with properties ¬
                        {file name:(POSIX file (p as text))} ¬
                        at after the last paragraph of content
                end try
            end repeat
        end tell
        activate
    end tell
    return "ok"
end run
'''


def host_label() -> str:
    name = socket.gethostname().strip()
    for suffix in (".local", ".lan", ".home"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
    return name or "this Mac"


def log(message: str) -> None:
    line = f"{__import__('datetime').datetime.now().isoformat(timespec='seconds')}  {message}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass                       # logging must never be the thing that fails


# ------------------------------------------------------------------ message --

def unpack(raw: bytes, workdir: Path) -> dict:
    """Pull a message apart into what the AppleScript needs.

    The .eml is the transport, not the destination: Mail opens an .eml as a
    *received* message that cannot be edited without Send Again, which is the
    friction this whole helper exists to remove. So the message is taken apart
    here and rebuilt as a genuine outgoing draft.
    """
    message = BytesParser(policy=policy.default).parsebytes(raw)

    html = ""
    body = message.get_body(preferencelist=("html",))
    if body is not None:
        html = body.get_content()
    else:
        plain = message.get_body(preferencelist=("plain",))
        if plain is not None:
            # Escaped, not injected: a plain-text body dropped into an HTML
            # field would have any stray angle bracket read as markup.
            import html as html_module
            html = f"<pre>{html_module.escape(plain.get_content())}</pre>"

    # The letterhead travels as an inline `cid:` part (see build_eml). Mail's
    # composer will not resolve a cid against parts we are not attaching, so
    # each one goes back to a data: URI -- which is what the templates use on
    # the AppleScript path anyway, and what Mail's composer does render.
    for part in message.walk():
        cid = (part.get("Content-ID") or "").strip("<>")
        if not cid or not (part.get_content_maintype() == "image"):
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        import base64
        encoded = base64.b64encode(payload).decode("ascii")
        html = html.replace(
            f"cid:{cid}", f"data:{part.get_content_type()};base64,{encoded}")

    attachments = []
    folder = workdir / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    for part in message.iter_attachments():
        name = part.get_filename()
        payload = part.get_payload(decode=True)
        if not name or payload is None:
            continue
        # Basename only. The filename comes off the wire, and it is about to
        # become a path.
        safe = Path(name).name or "attachment"
        path = folder / safe
        path.write_bytes(payload)
        attachments.append(str(path))

    return {
        "subject": message.get("Subject", "") or "",
        "to": message.get("To", "") or "",
        "html": html,
        "attachments": attachments,
    }


def open_draft(parts: dict) -> dict:
    """Hand it to Mail. Returns {ok, error}; never raises."""
    if not shutil.which("osascript"):
        return {"ok": False, "error": "osascript not found — is this a Mac?"}

    workdir = Path(tempfile.mkdtemp(prefix="ss-draft-"))
    try:
        body = workdir / "body.html"
        body.write_text(parts["html"], encoding="utf8")
        script = workdir / "draft.applescript"
        script.write_text(SCRIPT, encoding="utf8")

        args = ["osascript", str(script), parts["subject"], parts["to"],
                str(body)] + parts["attachments"]
        result = subprocess.run(args, capture_output=True, text=True, timeout=90)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            message = detail[-1] if detail else f"exit {result.returncode}"
            if "-1743" in message or "not allowed" in message.lower():
                message = ("macOS blocked the request. Approve the Automation "
                           "prompt, or allow this helper to control Mail under "
                           "System Settings → Privacy & Security → Automation.")
            return {"ok": False, "error": message}
        return {"ok": True, "error": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Mail did not respond in 90s — it may be "
                                      "showing a dialog."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        # The attachments live under workdir and Mail has already read them by
        # the time osascript returns.
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------- http --

class Handler(BaseHTTPRequestHandler):
    server_version = "SteepleDraftHelper/1.0"

    def log_message(self, fmt, *args):      # quieter than the default
        pass

    def _cors(self, origin: str) -> None:
        if origin and origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            # Chrome's Private Network Access: a page on one origin reaching a
            # loopback address is a "private network request" and is refused
            # without this. Safari does not ask for it; sending it is harmless.
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def _json(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf8")
        self.send_response(code)
        self._cors(self.headers.get("Origin", ""))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors(self.headers.get("Origin", ""))
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") != "/ping":
            return self._json(404, {"ok": False, "error": "not found"})
        # The page uses this to decide which button to show, so it answers
        # with the name of the Mac it will open a draft on -- the screen can
        # then say "Open draft in Mail on Larrys-Air" rather than promising
        # something vague.
        self._json(200, {"ok": True, "host": host_label(),
                         "mail": bool(shutil.which("osascript"))})

    def do_POST(self):
        if self.path.rstrip("/") != "/draft":
            return self._json(404, {"ok": False, "error": "not found"})

        origin = self.headers.get("Origin", "")
        if not origin_allowed(origin):
            log(f"refused a draft from {origin}")
            return self._json(403, {"ok": False,
                                    "error": f"origin not allowed: {origin}"})

        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BYTES:
            return self._json(413, {"ok": False, "error": "bad message size"})

        raw = self.rfile.read(length)
        workdir = Path(tempfile.mkdtemp(prefix="ss-eml-"))
        try:
            parts = unpack(raw, workdir)
            result = open_draft(parts)
        except Exception as exc:
            log(f"failed to unpack: {exc}")
            result = {"ok": False, "error": f"could not read the message: {exc}"}
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        if result["ok"]:
            log(f"opened a draft to {parts.get('to','')} "
                f"({len(parts.get('attachments', []))} attachment(s))")
        else:
            log(f"draft failed: {result['error']}")
        self._json(200 if result["ok"] else 500, result)


def main() -> int:
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        # Almost always "already running", which is not a failure worth a
        # crash loop in launchd.
        log(f"could not bind {HOST}:{PORT} — {exc}")
        return 0 if exc.errno in (48, 98) else 1
    log(f"listening on http://{HOST}:{PORT} as {host_label()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
