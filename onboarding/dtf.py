"""DTF transfers (doc 63): which print files an order line needs, and the
order list for the DTF supplier.

The source of truth is designs.json in the Print Library, written by the DTF
Assignment tool (20-Steeple-Stitch/_claude-tools/dtf-sorter). It maps each
in-house product to a design code and, per print, the library file, placement,
ink version, width, and which colours / size groups get it.

Who uses this module:
  * the Purchasing tab -- the "DTF transfers" section (view, mark, receive,
    order sheet, files ZIP);
  * slipprint -- the DTF line under each item on the packing slip. slipprint
    loads THIS FILE BY PATH, so it must stay standard-library only and must
    not import anything from the onboarding package at module level;
  * tools/backfill_designs.py -- writes custom.dtf_design onto the products.

The ledger of transfers already ordered lives beside ssorder's own ledger
(~/Dev/ssorder/state/dtf_ledger.json), so the backup that covers ssorder's
state covers this too. Keys are "<order-line key>|<library file>": one line
that needs a front and a back is two entries, ordered and received apart.

Nothing here raises to its caller.
"""
from __future__ import annotations

import contextlib
import csv
import fcntl
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ICLOUD = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
LIB = Path(os.environ.get("DTF_LIBRARY") or ICLOUD / "20-Steeple-Stitch" / "28-Print-Library")
SSORDER_DIR = Path(os.environ.get("SSORDER_DIR", Path.home() / "Dev" / "ssorder"))
LEDGER = SSORDER_DIR / "state" / "dtf_ledger.json"
KEEP_DAYS = 120          # ledger entries for lines long since fulfilled are pruned after this

PLACEMENT_NAMES = {"FF": "Full front", "LC": "Left chest", "RC": "Right chest", "FB": "Full back",
                   "BN": "Back neck", "LS": "Left sleeve", "RS": "Right sleeve", "HT": "Hat front",
                   "PT": "Patch", "MG": "Magnet / decal", "OT": "Other"}
INK_NAMES = {"DK": "dark-garment", "LT": "light-garment", "TN": "tonal", "FC": "full colour"}

CACHE_COPY = SSORDER_DIR / "state" / "designs-cache.json"

_cache: dict = {"mtime": None, "data": {}, "source": "", "error": ""}


# ------------------------------------------------------------------ designs
def designs_path() -> Path:
    return LIB / "designs.json"


def load_designs() -> dict:
    """designs.json, re-read only when it changes. {} if there is none anywhere.

    The Print Library is in iCloud Drive, and macOS may refuse a background job
    (slipprint runs from launchd) access to it. So whenever the library copy is
    read, a copy is kept beside ssorder's state, and a caller that cannot reach
    iCloud falls back to that copy. load_status() says which one was used."""
    p = designs_path()
    try:
        mtime = p.stat().st_mtime
        if _cache["mtime"] != mtime or _cache["source"] != "library":
            data = json.loads(p.read_text(encoding="utf-8"))
            _cache.update(mtime=mtime, data=data, source="library", error="")
            try:
                if not CACHE_COPY.exists() or CACHE_COPY.read_text(encoding="utf-8") != json.dumps(data):
                    CACHE_COPY.parent.mkdir(parents=True, exist_ok=True)
                    tmp = CACHE_COPY.with_name(CACHE_COPY.name + ".tmp")
                    tmp.write_text(json.dumps(data), encoding="utf-8")
                    os.replace(tmp, CACHE_COPY)
            except OSError:
                pass
        return _cache["data"] or {}
    except (OSError, ValueError) as e:
        err = f"{type(e).__name__}: {e}"
    try:
        mtime = CACHE_COPY.stat().st_mtime
        if _cache["mtime"] != mtime or _cache["source"] != "cache":
            _cache.update(mtime=mtime, data=json.loads(CACHE_COPY.read_text(encoding="utf-8")), source="cache")
        _cache["error"] = err
        return _cache["data"] or {}
    except (OSError, ValueError):
        _cache.update(mtime=None, data={}, source="", error=err)
        return {}


def load_status() -> dict:
    """{source: 'library' | 'cache' | '', error}: where the last load_designs() got its data."""
    return {"source": _cache["source"], "error": _cache["error"]}


def size_class(size: str) -> str:
    """Shopify size -> 'adult' | 'youth' | 'toddler' (YM is youth, 3T and 5/6 are toddler)."""
    s = (size or "").strip().upper()
    if s.startswith("Y"):
        return "youth"
    if re.fullmatch(r"\d+T", s) or s == "5/6":
        return "toddler"
    return "adult"


def _norm(s) -> str:
    return " ".join(str(s or "").split()).casefold().replace("gray", "grey")


def _num(gid) -> str:
    return str(gid or "").rsplit("/", 1)[-1]


def transfers_for(product_id, color: str = "", size: str = "", designs: dict | None = None) -> list[dict]:
    """The transfers one unit of this variant needs: [{code, file, placement, ink, width_in}].

    A print limited to some colours is skipped for the others; one limited to a
    size group is skipped outside it. A line with no colour or size (hats,
    one-colour listings) gets every print. Unknown product -> []."""
    d = designs if designs is not None else load_designs()
    prod = (d.get("products") or {}).get(_num(product_id))
    if not prod or (prod.get("method") or "DTF") != "DTF":
        return []                      # unknown, or printed some other way (embroidery, patch ...)
    out = []
    for p in prod.get("prints") or []:
        if not p.get("file"):
            continue
        cols = [_norm(c) for c in p.get("colors") or []]
        if cols and color and _norm(color) not in cols:
            continue
        group = p.get("sizes") or "all"
        if group != "all" and size and size_class(size) != group:
            continue
        out.append({"code": prod.get("code", ""), "file": p["file"], "placement": p.get("placement") or "",
                    "ink": p.get("ink") or "", "width_in": p.get("width_in")})
    return out


def code_for(product_id, designs: dict | None = None) -> str:
    d = designs if designs is not None else load_designs()
    return ((d.get("products") or {}).get(_num(product_id)) or {}).get("code", "")


def no_dtf(product_id, designs: dict | None = None) -> str:
    """The method recorded for a product that deliberately has no DTF print
    (embroidery, patch, ...), or '' if none was recorded."""
    d = designs if designs is not None else load_designs()
    return (d.get("no_dtf") or {}).get(_num(product_id), "")


def width_text(w) -> str:
    try:
        return f'{float(w):g}"'
    except (TypeError, ValueError):
        return ""


def label(t: dict) -> str:
    """'HOH-0002 FB DK 11"' -- the short form printed on the slip."""
    return " ".join(x for x in (t.get("code"), t.get("placement"), t.get("ink"), width_text(t.get("width_in"))) if x)


# ------------------------------------------------------------------- ledger
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


@contextlib.contextmanager
def _ledger_edit():
    """Load, change, save under an exclusive lock, so two workers never lose an edit."""
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(LEDGER.with_name("dtf_ledger.lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = _read_ledger()
        yield data
        cutoff = datetime.now(timezone.utc).timestamp() - KEEP_DAYS * 86400
        for k in [k for k, e in data.items() if _ts(e.get("at")) < cutoff]:
            del data[k]
        tmp = LEDGER.with_name("dtf_ledger.json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, LEDGER)


def _ts(stamp) -> float:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _clean_keys(keys) -> list[str]:
    return [k for k in dict.fromkeys(str(k).strip() for k in keys or []) if k and "|" in k]


def mark(keys, supplier: str = "", ref: str = "") -> dict:
    keys = _clean_keys(keys)
    if not keys:
        return {"ok": False, "error": "tick at least one transfer"}
    try:
        with _ledger_edit() as led:
            changed = 0
            for k in keys:
                if k not in led:
                    led[k] = {"status": "ordered", "at": _now(), "supplier": supplier.strip()[:60],
                              "ref": ref.strip()[:60]}
                    changed += 1
        return {"ok": True, "changed": changed}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def unmark(keys) -> dict:
    keys = _clean_keys(keys)
    try:
        with _ledger_edit() as led:
            changed = sum(1 for k in keys if led.pop(k, None) is not None)
        return {"ok": True, "changed": changed}
    except OSError as e:
        return {"ok": False, "error": str(e)}


def receive(keys, undo: bool = False) -> dict:
    keys = _clean_keys(keys)
    try:
        with _ledger_edit() as led:
            changed = 0
            for k in keys:
                e = led.get(k)
                if not e:
                    continue
                if undo and e.get("status") == "received":
                    e["status"] = "ordered"
                    e.pop("received_at", None)
                    changed += 1
                elif not undo and e.get("status") != "received":
                    e["status"] = "received"
                    e["received_at"] = _now()
                    changed += 1
        return {"ok": True, "changed": changed}
    except OSError as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------- view
def _local(stamp: str) -> str:
    t = _ts(stamp)
    return datetime.fromtimestamp(t).strftime("%b %-d, %-I:%M %p") if t else ""


def view(prop: dict) -> dict:
    """The DTF section of the Purchasing page, from the last saved proposal.

    to_order   transfers not ordered yet, one group per library file, with qty
    awaiting   ordered, not received -- one group per supplier order ref
    received   received while the order is still open (drops off once fulfilled)
    unassigned open in-house lines whose product has no design yet
    """
    designs = load_designs()
    lines = (prop or {}).get("inhouse_lines")
    base = {"ready": False, "lib": str(LIB), "to_order": [], "awaiting": [], "received": [],
            "unassigned": [], "pieces": 0, "designs_at": ""}
    if not designs:
        return {**base, "problem": f"No designs.json in {LIB} yet. Assign files in the DTF Assignment "
                                   "tool and run Build library."}
    if lines is None:
        return {**base, "problem": "Refresh once — this proposal was built before the DTF section existed."}
    led = _read_ledger()
    to_order, awaiting, received, unassigned = {}, {}, [], []
    for l in lines:
        ts = transfers_for(l.get("product_id"), l.get("color", ""), l.get("size", ""), designs)
        if not ts:
            if not no_dtf(l.get("product_id"), designs):
                unassigned.append(l)
            continue
        for t in ts:
            key = f"{l['key']}|{t['file']}"
            row = {"key": key, "order": l.get("order", ""), "order_id": l.get("order_id", ""),
                   "title": l.get("title", ""), "variant_title": l.get("variant_title", ""),
                   "qty": int(l.get("qty") or 0), "label": label(t)}
            e = led.get(key)
            if not e:
                g = to_order.setdefault(t["file"], {**t, "label": label(t), "name": Path(t["file"]).name,
                                                    "where": PLACEMENT_NAMES.get(t["placement"], t["placement"]),
                                                    "qty": 0, "lines": []})
                g["qty"] += row["qty"]
                g["lines"].append(row)
            elif e.get("status") == "received":
                received.append({**row, "when": _local(e.get("received_at", ""))})
            else:
                gk = (e.get("supplier", ""), e.get("ref", ""))
                g = awaiting.setdefault(gk, {"supplier": e.get("supplier", ""), "ref": e.get("ref", ""),
                                             "when": _local(e.get("at", "")), "pieces": 0, "lines": []})
                g["pieces"] += row["qty"]
                g["lines"].append(row)
    groups = sorted(to_order.values(), key=lambda g: g["file"])
    return {**base, "ready": True, "to_order": groups, "awaiting": list(awaiting.values()),
            "received": received, "unassigned": unassigned, "pieces": sum(g["qty"] for g in groups),
            "designs_at": _local(designs.get("generated", ""))}


# ----------------------------------------------------- order sheet and files
def library_file(rel: str) -> Path | None:
    """A file inside the Print Library, or None -- never anything outside it."""
    try:
        p = (LIB / rel).resolve()
        p.relative_to(LIB.resolve())
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def selected(prop: dict, files=None) -> list[dict]:
    """The to-order groups, limited to the given library files if any are given."""
    groups = view(prop).get("to_order") or []
    want = set(files or [])
    return [g for g in groups if not want or g["file"] in want]


def sheet_csv(groups: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["file", "design", "placement", "ink", "width_in", "qty"])
    for g in groups:
        w.writerow([g["name"], g["code"], g["placement"], g["ink"], g.get("width_in") or "", g["qty"]])
    return buf.getvalue()


def files_zip(groups: list[dict]) -> tuple[bytes, list[str]]:
    """(zip bytes, missing files). Each file once, plus order.csv."""
    buf, missing = io.BytesIO(), []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("order.csv", sheet_csv(groups))
        for g in groups:
            p = library_file(g["file"])
            if p:
                z.write(p, arcname=p.name)
            else:
                missing.append(g["file"])
    return buf.getvalue(), missing
