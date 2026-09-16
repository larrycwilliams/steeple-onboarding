"""Full onboarding package build: QR, postcards, four documents, redirect row."""
from __future__ import annotations

import fcntl
import json
import zipfile
from contextlib import contextmanager
from pathlib import Path

from . import docx_pdf, merge, qr, store
from . import postcard
from .postcard import generate_postcards
from .schema import derive


class BuildInProgress(RuntimeError):
    """A build for this partner is already running somewhere else."""


@contextmanager
def _only_one(out_dir: Path, pid: str):
    """Refuse a second concurrent build into the same output folder.

    A full package takes minutes, the button gives no sign of it, and gunicorn
    runs two workers -- so a reload part way through starts a SECOND build
    writing the same filenames into the same directory as the first. That
    happened on Haven of Hope on 15 Sep: the QR timestamps are minutes apart
    from everything else, which only makes sense as two overlapping runs. The
    files survived it. A .docx written by two processes at once does not have
    to.

    Non-blocking on purpose. Queueing would tie up the other worker for the
    length of the first build and take the whole app down with it -- exactly
    what it looked like was happening. Refusing costs nothing and lets the
    caller say something true.
    """
    lock_path = out_dir / ".build.lock"
    handle = open(lock_path, "w")
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise BuildInProgress(pid) from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        handle.close()


def build(record: dict, want_postcards: bool = True) -> dict:
    """Generate everything for one partner. Returns a manifest.

    Raises BuildInProgress if another build for this partner is already
    running. Callers that are not a web request -- regenerate_all.py, the
    tools -- run one partner at a time and will never see it.
    """
    ctx = derive(record)
    pid = store.partner_id(record)
    out_dir = store.output_dir(record)
    with _only_one(out_dir, pid):
        return _build(record, ctx, pid, out_dir, want_postcards)


def _build(record: dict, ctx: dict, pid: str, out_dir: Path,
           want_postcards: bool = True) -> dict:
    namer = store.make_namer(record)
    # Both of these are attributes left on a function by the last build that
    # ran, and a worker handles many partners in a row. Cleared here so a
    # partner with no logo cannot inherit the previous partner's verdict --
    # the manifest would report it as this partner's, and be believed.
    qr.make_qr.last_check = None
    postcard.place_logo.last_check = None
    # Not record["logo_path"] directly: see store.resolve_logo -- a stale
    # absolute path silently costs you the branded QR and the postcard mark.
    logo = store.resolve_logo(record)

    files: dict[str, str] = {}
    pdf_fonts: dict[str, str] = {}

    qr_files = qr.make_qr(
        ctx["qr_target"],
        out_dir,
        namer=namer,
        logo_path=logo or None,
        dark="#" + (ctx.get("color3_hex") or "000000"),
        accent="#" + (ctx.get("color1_hex") or "000000"),
    )
    files.update(qr_files)
    qr_for_print = qr_files.get("QR branded print") or qr_files.get("QR print hi-res")

    postcard_files: dict[str, str] = {}
    if want_postcards:
        try:
            postcard_files = generate_postcards(
                ctx, out_dir, qr_for_print, logo or None, namer=namer
            )
            files.update(postcard_files)
        except Exception as exc:
            files["Postcard generation failed"] = str(exc)

    # No product photo. The kit's "product photo for your posts" section was
    # removed on 2026-09-05: it meant producing an extra asset for every
    # partner that no customer had asked for. onboarding/product_shot.py and
    # tools/fetch_product_shots.py are left in place, and the image URLs stay
    # on the partner records, so putting it back is restoring the section in
    # source_docs and calling product_shot.build here again.
    media = {
        "logo": logo,
        "qr": qr_for_print,
        "postcard_front": postcard_files.get("Postcard front (digital)", ""),
        "postcard_back": postcard_files.get("Postcard back (digital)", ""),
    }
    documents = merge.generate_documents(record, media=media)
    for item in documents:
        files[item["label"]] = item["path"]

    # Signable PDF, built from the rendered agreement so the terms in the two
    # files cannot drift. Optional dependency: if reportlab is not installed
    # the rest of the package still builds.
    # Documents that also go out as PDF. The kit is a read-this piece that
    # gets emailed; the checklist and promo templates are meant to be filled
    # in and copied from, so they stay .docx only. Add a label here to give
    # another document a PDF.
    kit_docx = next(
        (i["path"] for i in documents if i["label"] == "Launch Week Kit"), None
    )
    kit_pdf = {"ok": False, "engine": None, "error": ""}
    if kit_docx:
        kit_pdf = docx_pdf.convert(
            kit_docx, out_dir / namer("Launch-Week-Kit", "pdf")
        )
        if kit_pdf["ok"]:
            files["Launch Week Kit (PDF)"] = kit_pdf["path"]

    agreement_docx = next(
        (i["path"] for i in documents if i["label"] == "Service Agreement"), None
    )
    if agreement_docx:
        try:
            from .agreement_pdf import build_agreement_pdf

            files["Service Agreement (signable PDF)"] = build_agreement_pdf(
                agreement_docx,
                ctx,
                out_dir / namer("Service-Agreement-SIGNABLE", "pdf"),
            )
            # Which font each family in the .docx actually resolved to. A
            # substitution is metric-compatible and harmless; a "(fallback)"
            # means the PDF is set in a base-14 face and will not match the
            # .docx on the page.
            from .agreement_pdf import font_report

            pdf_fonts = font_report()
        except ImportError:
            files["Signable PDF skipped"] = (
                "reportlab is not installed - run: "
                "~/.venvs/steeple-onboarding/bin/pip install -r requirements.txt"
            )
        except Exception as exc:
            files["Signable PDF failed"] = str(exc)

    redirect_from, redirect_to = qr.redirect_row(ctx)
    redirect_csv = qr.write_redirect_csv(
        [(redirect_from, redirect_to)], out_dir / namer("Shopify-Redirect", "csv")
    )
    files["Shopify redirect CSV"] = redirect_csv

    manifest = {
        "partner_id": pid,
        "org_name": record.get("org_name", ""),
        "plan": record.get("plan", ""),
        "store_url": ctx["store_url"],
        "qr_target": ctx["qr_target"],
        "redirect": {"from": redirect_from, "to": redirect_to},
        "qr_check": getattr(qr.make_qr, "last_check", None),
        "logo_check": getattr(postcard.place_logo, "last_check", None),
        "agreement_template_version": ctx.get("template_version", ""),
        "incomplete": store.readiness(record),
        "pdf_fonts": pdf_fonts,
        "kit_pdf": {
            "ok": kit_pdf["ok"],
            "engine": kit_pdf["engine"],
            "error": kit_pdf["error"],
        },
        "files": files,
    }
    # The manifest on disk stores bare file names, not the absolute paths that
    # are still in `files` for this request's download links. An absolute path
    # bakes in the directory the run happened in -- iCloud gives the container
    # a different one each session -- so every stored reference broke on the
    # next run. Names are stable and resolve against out_dir.
    stored = dict(manifest)
    stored["files"] = {label: (Path(path).name if isinstance(path, str) else path)
                       for label, path in files.items()}
    (out_dir / "manifest.json").write_text(json.dumps(stored, indent=2))

    bundle = out_dir / namer("Onboarding-Package", "zip")
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as archive:
        for label, path in files.items():
            candidate = Path(path)
            if candidate.exists() and candidate.is_file():
                archive.write(candidate, candidate.name)
    manifest["bundle"] = str(bundle)
    files["Onboarding package (zip)"] = str(bundle)

    return manifest


def build_all_redirects() -> str:
    """One CSV of every partner's redirect, for a single Shopify bulk import."""
    rows = []
    for record in store.list_partners():
        ctx = derive(record)
        rows.append(qr.redirect_row(ctx))
    return qr.write_redirect_csv(rows, store.OUTPUT / "all_shopify_redirects.csv")
