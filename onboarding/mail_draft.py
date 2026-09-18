"""Open a reviewed draft in Apple Mail, with the package already attached.

Deliberately a draft and never a send. A signed partner is a relationship
moment; the last look before it goes out is the point, not an obstacle. The
app fills in the recipient, the subject, the HTML body and the four
customer-facing files, and then hands the window over.

Apple Mail rather than the Gmail API: Larry sends these from Mail on the Mac,
and AppleScript needs no OAuth, no client secret and no token to expire in six
months. The cost is one macOS Automation permission prompt the first time.

**`create_draft` opens Mail on the machine running this app, which is the iMac
hub -- not the Mac whose browser clicked the button.** The AppleScript runs in
the server process; a browser on the Neo cannot reach Mail on the Neo. That is
not a bug and cannot be fixed from the web page, but it surprised everyone the
first time, so `build_eml` exists as the answer: a complete message as a file,
downloaded to whichever Mac you are actually sitting at, opened there.

An .eml opens in Apple Mail as a *received* message, not an editable draft.
**Message -> Send Again (Shift-Cmd-D)** turns it into a sendable copy with the
recipient, subject, body and attachments intact. Every screen offering the
download says so, because a file that opens read-only with no explanation
reads as broken.
"""
from __future__ import annotations

import mimetypes
import re
import shutil
import socket
import subprocess
import tempfile
from base64 import b64decode
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path

TIMEOUT = 90

# Mail has not lived in /Applications since macOS Catalina moved the bundled
# apps onto the read-only system volume. Checking only that path reported
# "Apple Mail was not found on this Mac" on a Mac that has it. Ask
# LaunchServices instead of guessing, and keep the path list only as a fast
# path that avoids spawning a process.
MAIL_PATHS = (
    Path("/System/Applications/Mail.app"),
    Path("/Applications/Mail.app"),
    Path.home() / "Applications" / "Mail.app",
)

# argv: subject, recipient, body-file, then zero or more attachment paths.
#
# The body is passed as a *file* rather than an argument. A 200KB HTML body
# with an embedded logo is far past what is comfortable to hand to osascript
# on a command line, and `do shell script "cat ..."` to read it back has its
# own output ceiling. Reading the file as utf8 inside AppleScript avoids both.
#
# Attachments go in after the body is set. Adding them before means Mail
# places them at the top of the message, above the letterhead.
# Mail is addressed by BUNDLE ID, not by name. On macOS 27 the name "Mail"
# resolves to Mail.app/Contents/PlugIns/MailQuickLookExtension.appex -- a
# QuickLook plugin with no scripting dictionary -- so every Mail class fails to
# resolve and AppleScript reports a *syntax* error (-2741, "Expected class name
# but found identifier") that reads like a typo in this file. `application id
# "com.apple.mail"` cannot collide, and behaves identically on older macOS.
# Diagnosed 2026-09-17; see claude/ops/43.
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

    tell application id "com.apple.mail"
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


def _found_at() -> str:
    """Where Mail is, or '' — for the preflight report."""
    for path in MAIL_PATHS:
        try:
            if path.exists():
                return str(path)
        except OSError:
            continue

    # Not in the usual places: it may be relocated, or the folder may be one
    # this process cannot stat. LaunchServices knows regardless.
    if not shutil.which("osascript"):
        return ""
    try:
        result = subprocess.run(
            ["osascript", "-e", 'id of app id "com.apple.mail"'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"registered as {result.stdout.strip()}"
    except Exception:
        pass
    return ""


def available() -> bool:
    return bool(shutil.which("osascript")) and bool(_found_at())


# The draft-helper version this app expects on the Macs talking to it. Bump it
# in the same commit as tools/draft_helper.py's own HELPER_VERSION -- they are
# two copies of one number, because the helper is stdlib-only and cannot import
# from this package.
EXPECTED_HELPER_VERSION = 3


def host_label() -> str:
    """The Mac this process is running on, as a person would name it.

    Just the first label of the hostname. macOS appends whatever the network
    hands it -- `.local` on Bonjour, `.localdomain` behind a router that serves
    no search domain, sometimes a real domain -- and "TWC-iMac.localdomain" on
    a button reads like a fault rather than a machine. Stripping a fixed list of
    suffixes was the first attempt and it missed `.localdomain` on the day it
    shipped; taking the first label cannot miss.
    """
    name = socket.gethostname().strip().split(".")[0]
    return name or "this Mac"


# ------------------------------------------------------------------ probe --
#
# Compiled, never run. A Mac that cannot build a draft should say so *before*
# the button is pressed, not hand back a temp path and an error code at the end
# of a discovery call. Same principle as the plate decision in doc 39 and the
# build duration in doc 38.
PROBE_SCRIPT = 'tell application id "com.apple.mail" to make new outgoing message'
_PROBE_CACHE: "tuple[bool, str] | None" = None


def scripting_ok() -> "tuple[bool, str]":
    """(ok, reason) -- can this Mac compile a Mail draft? Opens nothing."""
    global _PROBE_CACHE
    if _PROBE_CACHE is not None:
        return _PROBE_CACHE
    if not shutil.which("osacompile"):
        _PROBE_CACHE = (True, "")          # cannot check; never block on it
        return _PROBE_CACHE
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "probe.applescript"
            src.write_text(PROBE_SCRIPT, encoding="utf8")
            result = subprocess.run(
                ["osacompile", "-o", str(Path(tmp) / "probe.scpt"), str(src)],
                capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            _PROBE_CACHE = (True, "")
        else:
            detail = (result.stderr or "").strip().splitlines()
            _PROBE_CACHE = (False, detail[-1] if detail
                            else f"osacompile exit {result.returncode}")
    except Exception:
        _PROBE_CACHE = (True, "")          # a broken probe must not block Mail
    return _PROBE_CACHE


PROBE_FAILED = ("This Mac's Mail will not accept a scripted draft -- {reason}. "
                "Use Download .eml instead, then Message -> Send Again in Mail.")


def create_draft(subject: str, recipient: str, html: str,
                 attachments: list[str] | None = None) -> dict:
    """Open a Mail draft. Returns {ok, error}; never raises."""
    ok, why = scripting_ok()
    if not ok:
        return {"ok": False, "error": PROBE_FAILED.format(reason=why)}

    if not available():
        return {"ok": False,
                "error": "Apple Mail could not be found on this Mac, so a "
                         "draft cannot be opened. Use Download .html instead."}

    workdir = Path(tempfile.mkdtemp(prefix="ss-mail-"))
    try:
        body = workdir / "body.html"
        body.write_text(html, encoding="utf8")
        script = workdir / "draft.applescript"
        script.write_text(SCRIPT, encoding="utf8")

        args = ["osascript", str(script), subject, recipient or "", str(body)]
        for path in attachments or []:
            if Path(path).exists():
                args.append(str(Path(path).resolve()))

        result = subprocess.run(args, capture_output=True, text=True,
                                timeout=TIMEOUT)
        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            message = detail[-1] if detail else f"exit {result.returncode}"
            if "-1743" in message or "not allowed" in message.lower():
                message = ("macOS blocked the request. Approve the Automation "
                           "prompt, or allow Terminal to control Mail under "
                           "System Settings → Privacy & Security → Automation.")
            return {"ok": False, "error": message}
        return {"ok": True, "error": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False,
                "error": f"Mail did not respond within {TIMEOUT}s — it may be "
                         "showing a dialog."}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    finally:
        # Leave the body file alone until Mail has read it, then clean up.
        shutil.rmtree(workdir, ignore_errors=True)


# ------------------------------------------------------------ .eml download --

# The house email templates embed the letterhead as a data: URI, because that
# is what survives the AppleScript path into Mail's composer. Inside a real
# message file it has to become a proper inline part instead: mail clients
# routinely refuse to render data: URIs, and a statement that arrives without
# its letterhead looks like a phishing attempt rather than an invoice.
DATA_URI = re.compile(
    r'src="data:(image/[a-z+]+);base64,([A-Za-z0-9+/=\s]+)"', re.IGNORECASE)


def _inline_images(html: str) -> tuple[str, list[tuple[str, str, bytes]]]:
    """(html with cid: references, [(cid, mime, bytes)])."""
    found: list[tuple[str, str, bytes]] = []

    def swap(match: re.Match) -> str:
        mime = match.group(1).lower()
        try:
            raw = b64decode("".join(match.group(2).split()))
        except Exception:
            return match.group(0)      # leave it alone rather than break the body
        cid = f"img{len(found) + 1}@steepleandstitch"
        found.append((cid, mime, raw))
        return f'src="cid:{cid}"'

    return DATA_URI.sub(swap, html), found


def build_eml(subject: str, recipient: str, html: str, text: str = "",
              attachments: list[str] | None = None,
              sender: str = "") -> bytes:
    """A complete message as bytes, for downloading and opening in Mail.

    Machine-independent by design -- this is the path that works when the app
    is running on one Mac and you are sitting at another. Never raises on a
    missing attachment; a message that arrives without its PDF is recoverable,
    a 500 at payout time is not, and the caller has already checked the file it
    just wrote.
    """
    body, images = _inline_images(html)

    message = EmailMessage()
    message["Subject"] = subject
    if recipient:
        message["To"] = recipient
    if sender:
        message["From"] = sender
    message["Date"] = formatdate(localtime=True)
    # Outlook reads this as "this is a draft". Apple Mail ignores it, which is
    # why the screens tell you to use Send Again instead.
    message["X-Unsent"] = "1"

    message.set_content(text or "This message is best viewed as HTML.")
    message.add_alternative(body, subtype="html")

    html_part = message.get_payload()[-1]
    for cid, mime, raw in images:
        maintype, _, subtype = mime.partition("/")
        html_part.add_related(raw, maintype=maintype, subtype=subtype or "png",
                              cid=f"<{cid}>", disposition="inline")

    for path in attachments or []:
        item = Path(path)
        if not item.exists():
            continue
        guess = mimetypes.guess_type(item.name)[0] or "application/octet-stream"
        maintype, _, subtype = guess.partition("/")
        message.add_attachment(item.read_bytes(), maintype=maintype,
                               subtype=subtype or "octet-stream",
                               filename=item.name)

    return message.as_bytes()
