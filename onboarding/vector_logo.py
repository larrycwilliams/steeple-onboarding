"""Turn a vector logo -- PDF, AI, EPS, SVG -- into a raster the app can use.

Churches send artwork the way their designer handed it over, which is a PDF
far more often than a PNG. Before this, the upload field said `image/*` and a
PDF simply could not be given to the app, so it was converted by hand
somewhere else first. That conversion is where the quality goes: Haven of
Hope's mark arrived as a PDF, came back as a raster of one artboard, and the
postcard it produced had to be caught by eye -- see doc 39.

Rasterising here instead means one path, at a known resolution, with real
transparency, trimmed to the artwork. It also keeps the original: a vector
master is worth having the day somebody needs it at billboard size, and it
costs a few hundred kilobytes to keep.

Nothing in here raises. A logo that cannot be converted returns a reason, and
the caller shows it -- a partner record that refuses to save because of an
artwork file would be a worse failure than a missing mark.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image

# PDF and AI go down the same path: an .ai file written any time this century
# is a PDF with an Illustrator private section, and PyMuPDF opens it happily.
PDF_LIKE = {".pdf", ".ai"}
VECTOR_SUFFIXES = PDF_LIKE | {".eps", ".svg"}

# Big enough that the mark is never the limiting factor on a 300dpi postcard
# or a 1176px branded QR, and bounded so a poster-sized artboard cannot render
# a 200-megapixel image into a gunicorn worker.
TARGET_PX = 2400
MAX_PX = 5000


def is_vector(filename: str | Path) -> bool:
    return Path(str(filename)).suffix.lower() in VECTOR_SUFFIXES


def rasterise(src: Path | str, dest: Path | str) -> dict:
    """Render a vector file to a trimmed, transparent PNG at `dest`.

    Returns {"ok", "engine", "error", "size"}. Never raises.
    """
    src, dest = Path(src), Path(dest)
    suffix = src.suffix.lower()
    if suffix in PDF_LIKE:
        return _from_pdf(src, dest)
    if suffix == ".svg":
        return _from_svg(src, dest)
    if suffix == ".eps":
        return _from_eps(src, dest)
    return {"ok": False, "engine": None, "size": None,
            "error": f"{suffix or 'that file'} is not a vector format this reads."}


def _finish(image: Image.Image, dest: Path, engine: str) -> dict:
    """Trim the transparent margin and write the PNG.

    Trimming matters more than it sounds. A PDF artboard is usually much larger
    than the mark on it, and every consumer here sizes the logo by its image
    bounds -- so an untrimmed render puts a small mark in the middle of a big
    empty box and the postcard lays out around the box.
    """
    image = image.convert("RGBA")
    box = image.getchannel("A").getbbox()
    if box:
        image = image.crop(box)
    if not image.width or not image.height:
        return {"ok": False, "engine": engine, "size": None,
                "error": "that file rendered to nothing -- is the artwork on page 1?"}
    dest.parent.mkdir(parents=True, exist_ok=True)
    image.save(dest, "PNG")
    return {"ok": True, "engine": engine, "size": image.size, "error": ""}


def _from_pdf(src: Path, dest: Path) -> dict:
    try:
        # The module was renamed; `fitz` still works and warns. Prefer the new
        # name so the deprecation notice does not land in the error log on
        # every logo upload.
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
    except ImportError:
        return {"ok": False, "engine": None, "size": None,
                "error": ("PDF artwork needs PyMuPDF on this Mac: "
                          "pip install pymupdf. Until then, export the logo "
                          "as a transparent PNG and upload that.")}
    try:
        with fitz.open(str(src)) as document:
            if not document.page_count:
                return {"ok": False, "engine": "pymupdf", "size": None,
                        "error": "that PDF has no pages."}
            page = document.load_page(0)
            rect = page.rect
            longest = max(rect.width, rect.height) or 1
            zoom = min(TARGET_PX / longest, MAX_PX / longest)
            pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
            image = Image.frombytes("RGBA", (pixmap.width, pixmap.height),
                                    pixmap.samples)
    except Exception as exc:
        return {"ok": False, "engine": "pymupdf", "size": None,
                "error": f"that file could not be read as a PDF ({exc})."}
    return _finish(image, dest, "pymupdf")


def _from_svg(src: Path, dest: Path) -> dict:
    try:
        import cairosvg
    except ImportError:
        return {"ok": False, "engine": None, "size": None,
                "error": ("SVG artwork needs cairosvg on this Mac: "
                          "pip install cairosvg. Until then, export the logo "
                          "as a transparent PNG and upload that.")}
    import io
    try:
        png = cairosvg.svg2png(url=str(src), output_width=TARGET_PX)
        image = Image.open(io.BytesIO(png))
    except Exception as exc:
        return {"ok": False, "engine": "cairosvg", "size": None,
                "error": f"that SVG could not be rendered ({exc})."}
    return _finish(image, dest, "cairosvg")


def _from_eps(src: Path, dest: Path) -> dict:
    # PIL shells out to Ghostscript for EPS. Usually absent on a fresh Mac,
    # and the exception it throws does not say so in as many words.
    try:
        image = Image.open(src)
        image.load(scale=max(1, round(TARGET_PX / max(image.size))))
    except Exception as exc:
        return {"ok": False, "engine": "ghostscript", "size": None,
                "error": ("EPS artwork needs Ghostscript on this Mac: "
                          f"brew install ghostscript. ({exc})")}
    return _finish(image, dest, "ghostscript")
