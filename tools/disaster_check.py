#!/usr/bin/env python3
"""What would we actually lose if the hub died this minute?

Not a backup script. A reckoning: for everything in the app folder it asks two
questions -- is it in git, and is it in the nightly backup -- and prints what
the answer "no" to both adds up to.

Written because the answer was being assumed. `output/` was described as
"probably regenerable" and `assets/` had not been thought about at all, and
both of those are the kind of belief that is only tested on the worst day.

Run it on the hub:

    ~/.venvs/steeple-onboarding-312/bin/python tools/disaster_check.py

Read-only. It writes nothing, changes nothing, and can be run any time.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Where the nightly backup puts its mirror. Read from the script itself rather
# than hardcoded here, so this cannot quietly disagree with what actually runs.
BACKUP_SCRIPT = Path.home() / "bin" / "steeple-backup.sh"

# Things that are neither precious nor interesting.
SKIP = {".git", "__pycache__", ".DS_Store", ".venv", "venv"}


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:,.0f} {unit}" if unit != "GB" else f"{size:.1f} GB"
        size /= 1024
    return f"{size:.1f} GB"


def measure(path: Path) -> tuple[int, int, float]:
    """Files, bytes, and the newest modification time under a path."""
    if path.is_file():
        stat = path.stat()
        return 1, stat.st_size, stat.st_mtime
    files = total = 0
    newest = 0.0
    for folder, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if d not in SKIP]
        for name in filenames:
            if name in SKIP:
                continue
            try:
                stat = (Path(folder) / name).stat()
            except OSError:
                continue
            files += 1
            total += stat.st_size
            newest = max(newest, stat.st_mtime)
    return files, total, newest


def tracked_by_git() -> set[str]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return set()
    return {line.split("/", 1)[0] for line in out.stdout.splitlines() if line}


def backup_destination() -> Path | None:
    """The mirror directory the nightly script actually writes to."""
    if not BACKUP_SCRIPT.exists():
        return None
    text = BACKUP_SCRIPT.read_text("utf8", errors="replace")
    # Look for an assignment naming a destination, then expand ~ and $HOME.
    for match in re.finditer(r'^\s*(?:DEST|DESTINATION|BACKUP_DIR|TARGET)=(.+)$',
                             text, re.MULTILINE):
        raw = match.group(1).strip().strip('"').strip("'")
        raw = raw.replace("$HOME", str(Path.home())).replace("${HOME}", str(Path.home()))
        return Path(os.path.expanduser(raw))
    return None


def backed_up_names(mirror: Path | None) -> set[str]:
    if not mirror:
        return set()
    for candidate in (mirror / "current", mirror):
        if candidate.is_dir():
            return {p.name for p in candidate.iterdir() if p.name not in SKIP}
    return set()


def main() -> int:
    git = tracked_by_git()
    mirror = backup_destination()
    backed = backed_up_names(mirror)

    print(f"App folder : {ROOT}")
    print(f"Backup     : {mirror if mirror else 'NOT FOUND — could not read ' + str(BACKUP_SCRIPT)}")
    if mirror and not mirror.exists():
        print("             the path in the script does not exist on this Mac")
    print()

    rows = []
    for entry in sorted(ROOT.iterdir(), key=lambda p: p.name.lower()):
        if entry.name in SKIP:
            continue
        files, size, newest = measure(entry)
        if files == 0 and entry.is_dir():
            continue
        in_git = entry.name in git
        in_backup = entry.name in backed
        rows.append((entry.name, files, size, newest, in_git, in_backup))

    width = max(len(r[0]) for r in rows) + 2
    print(f"{'':<{width}}{'FILES':>8}{'SIZE':>12}   {'GIT':<5}{'BACKUP':<8}VERDICT")
    print("-" * (width + 46))

    at_risk = []
    for name, files, size, newest, in_git, in_backup in rows:
        if in_git:
            verdict = "safe — in git"
        elif in_backup:
            verdict = "safe — in the nightly backup"
        else:
            verdict = "*** LOST ***"
            at_risk.append((name, files, size, newest))
        print(f"{name:<{width}}{files:>8,}{human(size):>12}   "
              f"{'yes' if in_git else 'no':<5}{'yes' if in_backup else 'no':<8}{verdict}")

    print()
    if not at_risk:
        print("Nothing in this folder is unprotected.")
    else:
        total_files = sum(r[1] for r in at_risk)
        total_size = sum(r[2] for r in at_risk)
        print(f"A dead hub loses {total_files:,} files, {human(total_size)}:")
        now = dt.datetime.now().timestamp()
        for name, files, size, newest in at_risk:
            age = (now - newest) / 86400 if newest else 0
            print(f"  {name:<14} {files:>7,} files  {human(size):>10}  "
                  f"newest {age:,.0f} days old")

    # The one thing that is not a file in this folder.
    env = ROOT / ".env"
    print()
    if env.exists():
        print(".env is present and deliberately excluded from both git and the backup.")
        print("     Recovery needs it from somewhere else — record where in doc 35.")
    else:
        print(".env is NOT present. The app cannot reach Shopify without it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
