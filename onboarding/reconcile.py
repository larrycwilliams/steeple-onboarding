"""Check that each partner's record, assets and output folder agree.

The split naming is deliberate: assets live under the slug (partner_id) and the
output folder keeps readable capitalisation (name_token), stored on the record
as output_folder. Nothing verified that the three actually lined up, which is
how one partner's folder ended up named for a different church and holding that
church's launch kit. This reports; it does not rename behind your back.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import store, terms

SKIP_DIRS = {"_to_delete", "_backup", "_history", "_superseded"}


def _dirs(base: Path) -> set[str]:
    if not base.exists():
        return set()
    return {p.name for p in base.iterdir() if p.is_dir() and p.name not in SKIP_DIRS}


def audit() -> dict:
    records = store.list_partners()
    by_id = {store.partner_id(r): r for r in records}

    rows, orphan_assets, orphan_output = [], [], []
    asset_dirs, output_dirs = _dirs(store.ASSETS), _dirs(store.OUTPUT)
    claimed_assets, claimed_output = set(), set()

    for pid, r in sorted(by_id.items()):
        folder = r.get("output_folder") or store.name_token(r)
        claimed_assets.add(pid)
        claimed_output.add(folder)
        issues = []
        if pid not in asset_dirs:
            issues.append("no assets folder")
        if folder not in output_dirs:
            issues.append("no output folder")
        if r.get("output_folder") and r["output_folder"] != store.name_token(r):
            issues.append(f"output_folder '{r['output_folder']}' no longer matches the org name")
        rows.append({
            "id": pid, "org": r.get("org_name", ""), "plan": r.get("plan", ""),
            "asset_dir": pid, "output_dir": folder,
            "assets": len(list((store.ASSETS / pid).rglob("*"))) if pid in asset_dirs else 0,
            "files": len([p for p in (store.OUTPUT / folder).glob("*") if p.is_file()]) if folder in output_dirs else 0,
            "status": r.get("status", ""),
            "incomplete": _incomplete(store.OUTPUT / folder),
            "issues": issues,
        })

    orphan_assets = sorted(asset_dirs - claimed_assets - {"fonts", "email"})
    orphan_output = sorted(output_dirs - claimed_output)
    return {
        "partners": rows,
        "orphan_assets": orphan_assets,
        "orphan_output": orphan_output,
        "term_mismatches": terms.mismatches(records),
        "ok": not (orphan_assets or orphan_output or any(r["issues"] for r in rows)),
    }


def _incomplete(folder: Path) -> list[str]:
    m = folder / "manifest.json"
    if not m.exists():
        return []
    try:
        return json.loads(m.read_text()).get("incomplete") or []
    except Exception:
        return []


def relativise_manifests(apply: bool = False) -> list[str]:
    """manifest.json stores absolute paths that include the session directory
    they were generated in, so every reference breaks on the next run. Rewrite
    them relative to the partner's own folder."""
    touched = []
    if not store.OUTPUT.exists():
        return touched
    for m in store.OUTPUT.glob("*/manifest.json"):
        try:
            data = json.loads(m.read_text())
        except Exception:
            continue
        files = data.get("files")
        if not isinstance(files, dict):
            continue
        changed = False
        for label, val in list(files.items()):
            if isinstance(val, str) and ("/" in val or "\\" in val):
                name = Path(val).name
                if name != val:
                    files[label] = name
                    changed = True
        if changed:
            touched.append(m.parent.name)
            if apply:
                data["files"] = files
                m.write_text(json.dumps(data, indent=2))
    return touched
