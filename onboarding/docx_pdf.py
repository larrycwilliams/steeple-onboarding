"""Convert a rendered .docx to PDF using whatever the Mac already has.

The Launch Week Kit is a designed document -- ten tables, a hundred-odd
shaded cells, five images, gold rules, page breaks. The Service Agreement PDF
is hand-built with ReportLab because it has to carry AcroForm fields, and no
converter can add those. The kit needs no fields, so re-drawing it by hand
would buy nothing and cost the one thing that matters: it would drift from the
.docx the moment the source document is restyled.

So this converts rather than re-renders. Whatever comes out is what Word
shows, because it is the same file.

Engines, in order:

1. **LibreOffice**, headless. Best fidelity, no GUI, no dialogs, scriptable.
   Runs against a throwaway user profile so it cannot collide with a
   LibreOffice window the user already has open -- sharing the default
   profile is the classic way this hangs forever.
2. **Microsoft Word**, driven by AppleScript. Perfect fidelity when Office is
   installed. Fragile enough to be second: the first run raises a macOS
   Automation permission prompt, and a modal dialog inside Word will block it
   until the timeout.

Pages is deliberately *not* used. Its .docx import re-flows the layout, so it
would produce a PDF that quietly disagrees with the .docx beside it -- the
exact failure this module exists to avoid.

Everything degrades safely: no engine means no PDF and a message saying so,
never a bad PDF.
"""
from __future__ import annotations

import atexit
import shutil
import subprocess
import tempfile
from pathlib import Path

TIMEOUT = 180
# A single document should never take three minutes. Word gets a shorter leash
# than LibreOffice so one stalled conversion cannot hold up a whole batch.
WORD_TIMEOUT = 90

SOFFICE_CANDIDATES = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    str(Path.home() / "Applications/LibreOffice.app/Contents/MacOS/soffice"),
    "/usr/local/bin/soffice",
    "/opt/homebrew/bin/soffice",
)

WORD_CANDIDATES = (
    "/Applications/Microsoft Word.app",
    str(Path.home() / "Applications/Microsoft Word.app"),
)

INSTALL_HINT = (
    "No .docx-to-PDF converter found. Install LibreOffice once and every "
    "later generate picks it up:  brew install --cask libreoffice"
)


def _soffice() -> str | None:
    found = shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in SOFFICE_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _word() -> str | None:
    if not shutil.which("osascript"):
        return None
    for candidate in WORD_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def available_engine() -> str | None:
    """'libreoffice', 'word', or None."""
    if _soffice():
        return "libreoffice"
    if _word():
        return "word"
    return None


def _convert_libreoffice(binary: str, docx_path: Path, out_path: Path) -> None:
    # A private profile per run. Without -env:UserInstallation a headless
    # call shares the profile of any LibreOffice window that is already open,
    # and then silently waits for a lock that is never released.
    workdir = Path(tempfile.mkdtemp(prefix="ss-pdf-"))
    profile = workdir / "profile"
    try:
        subprocess.run(
            [
                binary,
                "--headless",
                "--norestore",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to", "pdf",
                "--outdir", str(workdir),
                str(docx_path),
            ],
            check=True,
            timeout=TIMEOUT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        produced = workdir / (docx_path.stem + ".pdf")
        if not produced.exists():
            candidates = list(workdir.glob("*.pdf"))
            if not candidates:
                raise RuntimeError("LibreOffice reported success but wrote no PDF")
            produced = candidates[0]
        shutil.move(str(produced), str(out_path))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# Word is left running between conversions on purpose. The first version quit
# it after every document, which meant a batch of eight paid eight full app
# launches -- and every launch is another chance for Word to come up with a
# start screen, a sign-in banner or a recovery pane, any of which blocks the
# script until it times out. That is exactly how a regenerate stalled after
# the first partner. It is quit once, at process exit, and only if this
# process was what started it.
WORD_SCRIPT = '''
on run argv
    set inPath to item 1 of argv
    set outPath to item 2 of argv
    tell application "Microsoft Word"
        set display alerts to none
        set theDoc to open file name inPath with read only
        save as theDoc file name outPath file format format PDF
        close theDoc saving no
    end tell
end run
'''

_word_started_here: bool | None = None


def _word_is_running() -> bool:
    try:
        result = subprocess.run(
            ["osascript", "-e", 'application "Microsoft Word" is running'],
            capture_output=True, text=True, timeout=20, check=True,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def _quit_word() -> None:
    try:
        subprocess.run(
            ["osascript", "-e",
             'tell application "Microsoft Word" to quit saving no'],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def _convert_word(docx_path: Path, out_path: Path) -> None:
    global _word_started_here
    if _word_started_here is None:
        _word_started_here = not _word_is_running()
        if _word_started_here:
            atexit.register(_quit_word)

    # Word wants HFS-style paths from AppleScript, not POSIX ones.
    def hfs(path: Path) -> str:
        return subprocess.run(
            ["osascript", "-e",
             f'POSIX file "{path}" as text'],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.strip()

    script = Path(tempfile.mkdtemp(prefix="ss-pdf-")) / "convert.applescript"
    script.write_text(WORD_SCRIPT)
    try:
        subprocess.run(
            ["osascript", str(script), hfs(docx_path), hfs(out_path)],
            check=True, timeout=WORD_TIMEOUT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if not out_path.exists():
            raise RuntimeError("Word reported success but wrote no PDF")
    finally:
        shutil.rmtree(script.parent, ignore_errors=True)


def convert(docx_path: str | Path, out_path: str | Path) -> dict:
    """Convert to PDF. Returns {ok, engine, path, error} and never raises."""
    docx_path = Path(docx_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not docx_path.exists():
        return {"ok": False, "engine": None, "path": "",
                "error": f"{docx_path.name} does not exist"}

    engine = available_engine()
    if engine is None:
        return {"ok": False, "engine": None, "path": "", "error": INSTALL_HINT}

    try:
        if engine == "libreoffice":
            _convert_libreoffice(_soffice(), docx_path, out_path)
        else:
            _convert_word(docx_path, out_path)
    except subprocess.TimeoutExpired:
        limit = WORD_TIMEOUT if engine == "word" else TIMEOUT
        return {"ok": False, "engine": engine, "path": "",
                "error": f"{engine} did not finish within {limit}s — check for "
                         "a dialog or permission prompt on screen. Installing "
                         "LibreOffice avoids this entirely."}
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode("utf8", "replace").strip()[:200]
        return {"ok": False, "engine": engine, "path": "",
                "error": f"{engine} failed: {detail or exc}"}
    except Exception as exc:
        return {"ok": False, "engine": engine, "path": "", "error": str(exc)}

    if not out_path.exists() or out_path.stat().st_size == 0:
        return {"ok": False, "engine": engine, "path": "",
                "error": f"{engine} produced an empty file"}

    return {"ok": True, "engine": engine, "path": str(out_path), "error": ""}
