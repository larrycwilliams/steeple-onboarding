"""Render the four partner documents from the templates."""
from __future__ import annotations

import shutil
import zipfile
from io import BytesIO
from pathlib import Path

import jinja2
from PIL import Image
from docxtpl import DocxTemplate

from . import store
from .schema import derive

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "docx_templates"

DOCUMENTS = [
    ("service_agreement.docx", "Service-Agreement", "Service Agreement"),
    ("setup_checklist.docx", "Setup-Checklist", "Setup Checklist"),
    ("launch_week_kit.docx", "Launch-Week-Kit", "Launch Week Kit"),
    ("promo_templates.docx", "Promo-Templates", "Promo Templates"),
]

# Which embedded image each media part in the Launch Week Kit holds.
KIT_MEDIA = {
    "word/media/image1.png": "logo",
    "word/media/image2.png": "qr",
    "word/media/image3.png": "postcard_front",
    "word/media/image4.png": "postcard_back",
}
# image5.jpg was the "product photo for your posts" slot. The section was
# removed from the source document on 2026-09-05 -- it was an extra asset to
# produce for every partner and nobody had asked for it. If it comes back,
# restore the section in source_docs, rebuild, and map the new part here.


def _sentence(value: str) -> str:
    """Upper-case the first character only.

    Jinja's own `capitalize` lower-cases the remainder, which turns 3XL into
    3xl and PA into Pa. Every acronym in these documents needs this instead.
    """
    text = str(value)
    return text[:1].upper() + text[1:] if text else text


def _jinja_env() -> jinja2.Environment:
    # autoescape is required: without it docxtpl drops the "&" in values and
    # in any source text it rewrites (Steeple & Stitch, 4X & 5X).
    env = jinja2.Environment(autoescape=True)
    env.filters["sentence"] = _sentence
    return env


def render_document(template_name: str, ctx: dict, out_path: Path) -> Path:
    template = DocxTemplate(str(TEMPLATES / template_name))
    template.render(ctx, jinja_env=_jinja_env(), autoescape=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    template.save(str(out_path))
    return out_path


def _fit_to(src_path: str | Path, size: tuple[int, int], fmt: str) -> bytes:
    """Fit a replacement image into the original's box, centred on white.

    Word lays images out by EMU in the XML, so keeping the original pixel box
    keeps the document layout identical no matter what the partner sends.
    """
    image = Image.open(src_path).convert("RGBA")
    target = Image.new("RGBA", size, (255, 255, 255, 0))
    scaled = image.copy()
    scaled.thumbnail(size, Image.LANCZOS)
    target.paste(
        scaled,
        ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2),
        scaled,
    )
    buf = BytesIO()
    if fmt.lower() in ("jpg", "jpeg"):
        flat = Image.new("RGB", size, (255, 255, 255))
        flat.paste(target, mask=target.split()[3])
        flat.save(buf, "JPEG", quality=92)
    else:
        target.save(buf, "PNG")
    return buf.getvalue()


def swap_media(docx_path: Path, replacements: dict[str, str]) -> None:
    """Replace embedded images in a rendered .docx.

    replacements maps the media role ('logo', 'qr', ...) to a source file path.
    Anything not supplied keeps the image already in the template.
    """
    wanted = {
        part: replacements[role]
        for part, role in KIT_MEDIA.items()
        if role in replacements and replacements[role]
        and Path(replacements[role]).exists()
    }
    if not wanted:
        return

    source = zipfile.ZipFile(docx_path)
    sizes = {}
    for part in wanted:
        try:
            with source.open(part) as handle:
                sizes[part] = Image.open(BytesIO(handle.read())).size
        except KeyError:
            continue

    tmp_path = docx_path.with_suffix(".tmp.docx")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as out:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename in wanted and item.filename in sizes:
                fmt = "jpg" if item.filename.endswith(".jpg") else "png"
                try:
                    data = _fit_to(wanted[item.filename], sizes[item.filename], fmt)
                except Exception:
                    pass
            out.writestr(item, data)
    source.close()
    shutil.move(str(tmp_path), str(docx_path))


def generate_documents(record: dict, media: dict[str, str] | None = None) -> list[dict]:
    """Render all four documents for a partner. Returns file descriptors."""
    ctx = derive(record)
    pid = store.partner_id(record)
    out_dir = store.output_dir(record)
    media = media or {}

    produced = []
    for template_name, descriptor, label in DOCUMENTS:
        filename = store.output_filename(record, descriptor, "docx")
        path = out_dir / filename
        render_document(template_name, ctx, path)
        if template_name == "launch_week_kit.docx":
            swap_media(path, media)
        produced.append({"label": label, "path": str(path), "name": filename})
    return produced
