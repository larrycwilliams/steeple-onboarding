"""Partner records on disk. One JSON per organization, plus their assets."""
from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from pathlib import Path

from .schema import default_record, slugify

ROOT = Path(__file__).resolve().parents[1]
PARTNERS = ROOT / "partners"
OUTPUT = ROOT / "output"
ASSETS = ROOT / "assets"


def _paths():
    for path in (PARTNERS, OUTPUT, ASSETS):
        path.mkdir(parents=True, exist_ok=True)


# Fields that must carry a real value before a partner-facing document is
# worth printing. The fee entries matter most: default_record seeds them at
# 0 / 0 / 10%, which renders as a perfectly formatted agreement offering a
# free store on a 10% margin. Nothing errors -- it just quietly states the
# wrong commercial terms over a signature line.
REQUIRED_BEFORE_SEND = [
    ("poc_name", "Primary contact name"),
    ("poc_email", "Primary contact email"),
    ("address_line1", "Street address"),
    ("city", "City"),
    ("zip", "ZIP"),
    ("setup_fee", "Setup fee"),
    ("monthly_fee", "Monthly fee"),
    ("margin_pct", "Client margin"),
    ("client_signer", "Client signer"),
    ("client_signer_title", "Client signer title"),
    ("launch_date", "Launch date"),
]

# A fee of zero is never a negotiated term here -- it is the seed value.
PLACEHOLDER_VALUES = {
    "setup_fee": {"", "0", "0.00", "$0"},
    "monthly_fee": {"", "0", "0.00", "$0"},
}


def readiness(record: dict) -> list[str]:
    """Labels of the fields still holding a blank or a seed value."""
    missing = []
    for key, label in REQUIRED_BEFORE_SEND:
        value = str(record.get(key) or "").strip()
        blanks = PLACEHOLDER_VALUES.get(key, {""})
        if value in blanks:
            missing.append(label)
    if not resolve_logo(record):
        missing.append("Logo file")
    return missing


HISTORY = PARTNERS / "_history"
HISTORY_KEEP = 30


def _snapshot(pid: str, path: Path) -> None:
    """Keep the previous contents of a record before overwriting it.

    Partner records have no version history, and on 2026-09-05 a regression
    script silently overwrote a live partner -- the contacts, the address and
    the negotiated terms -- with seed values. It was recoverable only because
    the old values happened to be quoted in a chat transcript. That is luck,
    not a process.

    Cheap insurance: a few KB of JSON per save, in the same iCloud folder as
    everything else, so it follows the Macs around like the rest of the work.
    """
    if not path.exists():
        return
    folder = HISTORY / pid
    folder.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    try:
        shutil.copy2(path, folder / f"{stamp}.json")
    except OSError:
        return  # history is best-effort and must never block a save

    # Trim oldest first, so the folder cannot grow without bound.
    versions = sorted(folder.glob("*.json"))
    for stale in versions[:-HISTORY_KEEP]:
        try:
            stale.unlink()
        except OSError:
            pass


def history(pid: str) -> list[dict]:
    """Saved versions of one partner, newest first."""
    folder = HISTORY / pid
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.glob("*.json"), reverse=True):
        stamp = path.stem
        try:
            record = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        out.append({
            "stamp": stamp,
            "when": stamp.replace("_", " ").replace("-", "/"),
            "path": str(path),
            "org_name": record.get("org_name", ""),
            "record": record,
        })
    return out


def restore(pid: str, stamp: str) -> dict | None:
    """Put a saved version back. The current one is snapshotted first."""
    source = HISTORY / pid / f"{stamp}.json"
    if not source.exists():
        return None
    record = json.loads(source.read_text())
    return save(record)


def resolve_logo(record: dict) -> str:
    """Return a logo path that actually exists on this machine, or "".

    Records store an absolute path, which is fine until the folder moves --
    a different Mac, a different iCloud account, a renamed parent folder.
    Nothing errors when it goes stale: ``qr.make_qr`` and the postcards both
    test ``Path(logo).exists()`` and quietly skip the branded artwork, so the
    package builds "successfully" without the partner's mark on any of it.

    So don't trust the stored string. Fall back to the partner's own assets
    folder, first by the same filename, then by whatever image is in there.
    """
    stored = (record.get("logo_path") or "").strip()
    if stored and Path(stored).exists():
        return stored

    folder = ASSETS / partner_id(record)
    if not folder.is_dir():
        return ""

    if stored:
        same_name = folder / Path(stored).name
        if same_name.exists():
            return str(same_name)

    images = sorted(
        path for path in folder.iterdir()
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    )
    return str(images[0]) if images else ""


def partner_id(record: dict) -> str:
    """Stable id derived from the organization name.

    Kept in step with the folder name so the record, the assets and the output
    folder all carry the same customer identity.
    """
    return slugify(name_token(record))


def record_path(pid: str) -> Path:
    return PARTNERS / f"{pid}.json"


def list_partners() -> list[dict]:
    _paths()
    out = []
    for path in sorted(PARTNERS.glob("*.json")):  # _history/ is a folder, not matched
        try:
            out.append(json.loads(path.read_text()))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda r: (r.get("org_name") or "").lower())


def load(pid: str) -> dict | None:
    path = record_path(pid)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save(record: dict) -> dict:
    """Write the record, migrating its id and folders if the name changed.

    The id used to be frozen at creation, so renaming a partner left its
    output landing in the previous partner's folder. The id now follows the
    organization name and everything on disk moves with it.
    """
    _paths()
    previous_id = record.get("id")
    previous_folder = record.get("output_folder")

    pid = partner_id(record)
    folder = name_token(record)

    if previous_id and previous_id != pid:
        _migrate(record, previous_id, pid, previous_folder, folder)

    record["id"] = pid
    record["output_folder"] = folder
    _snapshot(pid, record_path(pid))
    now = _dt.datetime.now().isoformat(timespec="seconds")
    record.setdefault("created_at", now)
    record["updated_at"] = now
    record_path(pid).write_text(json.dumps(record, indent=2, ensure_ascii=False))
    return record


def save_as_new(record: dict) -> dict:
    """Fork the record into a separate partner, leaving the original intact."""
    fresh = dict(record)
    fresh.pop("id", None)
    fresh.pop("created_at", None)
    fresh.pop("output_folder", None)

    pid = partner_id(fresh)
    if record_path(pid).exists():
        suffix = 2
        while record_path(f"{pid}-{suffix}").exists():
            suffix += 1
        pid = f"{pid}-{suffix}"

    # A forked partner gets its own copy of the logo so deleting one partner
    # can never pull the artwork out from under another.
    logo = record.get("logo_path")
    fresh["id"] = pid
    if logo and Path(logo).exists():
        fresh["logo_path"] = store_logo(pid, logo)

    fresh["output_folder"] = name_token(fresh)
    now = _dt.datetime.now().isoformat(timespec="seconds")
    fresh["created_at"] = now
    fresh["updated_at"] = now
    record_path(pid).write_text(json.dumps(fresh, indent=2, ensure_ascii=False))
    return fresh


def _migrate(record: dict, old_id: str, new_id: str,
             old_folder: str | None, new_folder: str) -> None:
    """Move a renamed partner's record, assets and output to the new name."""
    old_record = record_path(old_id)
    if old_record.exists():
        # Deleting can fail on a read-only or permission-restricted volume.
        # Park the old record out of the way instead so the rename still
        # completes and the partner list doesn't show a duplicate.
        try:
            old_record.unlink()
        except OSError:
            parked = PARTNERS / f"_superseded_{old_id}.json.bak"
            try:
                old_record.rename(parked)
            except OSError:
                pass

    old_assets = ASSETS / old_id
    new_assets = ASSETS / new_id
    if old_assets.exists() and not new_assets.exists():
        shutil.move(str(old_assets), str(new_assets))
        logo = record.get("logo_path")
        if logo and str(old_assets) in logo:
            record["logo_path"] = logo.replace(str(old_assets), str(new_assets))

    old_out = OUTPUT / (old_folder or old_id)
    new_out = OUTPUT / new_folder
    if old_out.exists() and not new_out.exists() and old_out != new_out:
        shutil.move(str(old_out), str(new_out))
        _rename_outputs(new_out, old_folder or old_id, new_folder)


def _rename_outputs(folder: Path, old_token: str, new_token: str) -> None:
    """Rename already-generated files so none carry the old customer name.

    Without this a renamed partner keeps a folder full of documents named for
    who they used to be, which is exactly the confusion the naming convention
    exists to prevent.
    """
    if old_token == new_token:
        return
    for path in folder.iterdir():
        if not path.is_file() or old_token not in path.name:
            continue
        target = folder / path.name.replace(old_token, new_token)
        if not target.exists():
            path.rename(target)


def delete(pid: str) -> bool:
    path = record_path(pid)
    if path.exists():
        path.unlink()
        return True
    return False


def new_record() -> dict:
    return default_record()


def asset_dir(pid: str) -> Path:
    path = ASSETS / pid
    path.mkdir(parents=True, exist_ok=True)
    return path


def name_token(record: dict) -> str:
    """The customer's name as it appears in file and folder names.

    Full organization name, hyphenated, original capitalization kept, so a
    Finder window full of partners is readable at a glance. Falls back to the
    short name only if there is no organization name at all.
    """
    raw = (record.get("org_name") or record.get("org_short") or "Partner").strip()
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", raw)
    return re.sub(r"-{2,}", "-", cleaned).strip("-") or "Partner"


def output_dir(record_or_pid) -> Path:
    """Output folder, named for the customer."""
    if isinstance(record_or_pid, dict):
        folder = name_token(record_or_pid)
    else:
        folder = str(record_or_pid)
    path = OUTPUT / folder
    path.mkdir(parents=True, exist_ok=True)
    return path


def store_logo(pid: str, src: Path | str, filename: str | None = None) -> str:
    """Copy an uploaded logo into the partner's asset folder. Returns the path."""
    src = Path(src)
    dest = asset_dir(pid) / (filename or src.name)
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    return str(dest)


def output_filename(record: dict, descriptor: str, ext: str) -> str:
    """Convention: YYYY-MM-DD_Customer-Name_Descriptor_v01.ext"""
    date = _dt.date.today().isoformat()
    return f"{date}_{name_token(record)}_{descriptor}_v01.{ext}"


def make_namer(record: dict):
    """Return namer(descriptor, ext) -> filename, bound to this partner.

    Passed into the QR and postcard generators so every artefact -- documents,
    codes, print files, CSVs -- lands on one naming convention instead of each
    module inventing its own.
    """
    def namer(descriptor: str, ext: str) -> str:
        return output_filename(record, descriptor, ext)

    return namer
