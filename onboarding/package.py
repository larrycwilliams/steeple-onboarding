"""Full onboarding package build: QR, postcards, four documents, redirect row."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from . import docx_pdf, merge, qr, store
from .postcard import generate_postcards
from .schema import derive


def build(record: dict, want_postcards: bool = True) -> dict:
    """Generate everything for one partner. Returns a manifest."""
    ctx = derive(record)
    pid = store.partner_id(record)
    out_dir = store.output_dir(record)
    namer = store.make_namer(record)
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
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

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
