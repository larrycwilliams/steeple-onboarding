"""Open a reviewed draft in Apple Mail, with the package already attached.

Deliberately a draft and never a send. A signed partner is a relationship
moment; the last look before it goes out is the point, not an obstacle. The
app fills in the recipient, the subject, the HTML body and the four
customer-facing files, and then hands the window over.

Apple Mail rather than the Gmail API: Larry sends these from Mail on the Mac,
and AppleScript needs no OAuth, no client secret and no token to expire in six
months. The cost is one macOS Automation permission prompt the first time.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
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
            ["osascript", "-e", 'id of app "Mail"'],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"registered as {result.stdout.strip()}"
    except Exception:
        pass
    return ""


def available() -> bool:
    return bool(shutil.which("osascript")) and bool(_found_at())


def create_draft(subject: str, recipient: str, html: str,
                 attachments: list[str] | None = None) -> dict:
    """Open a Mail draft. Returns {ok, error}; never raises."""
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
