"""The partner's own product photography, assembled into one sheet.

Section 4 of the Launch Week Kit hands the partner a picture to put in a
newsletter or a post. Until now that picture came from the template, which
means every partner's kit shipped with the GCS Cardinals photo under the
words "apparel from your launch lineup" -- another school's merchandise,
presented as theirs.

The fix is not to fake a mockup. Every partner already has real photography:
the print-on-demand apps generate a studio shot of their actual artwork on
the actual garment, and those images are already live on their Shopify
collection. This module collects them into a single sheet, sized to the slot
the kit reserves.

Order of preference, and it never falls through to another partner's goods:

1. ``assets/<partner>/product-photo.*`` -- a photo Larry shot himself. Always
   wins; nothing here second-guesses it.
2. ``assets/<partner>/product-shots/`` -- images already downloaded.
3. ``record["product_shot_urls"]`` -- Shopify CDN URLs cached on the record,
   downloaded once and then kept in the folder above. Plain CDN GETs, so this
   works without an Admin API token in .env.
4. A branded placeholder, for a partner whose store has no products yet.
"""
from __future__ import annotations

import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import store
from .imaging import prepare_logo

# Matches word/media/image5.jpg in the kit template (898x990). Keeping the
# aspect means the sheet fills the slot instead of being letterboxed into it;
# the higher resolution is for print.
SHEET_W, SHEET_H = 1200, 1323

GUTTER = 26
PAD = 30
MAX_SHOTS = 4
TIMEOUT = 25
PHOTO_NAMES = ("product-photo", "product_photo")
PHOTO_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

USER_AGENT = "SteepleStitchOnboarding/1.0 (+steepleandstitch.com)"

# Only used on the placeholder card, so a miss costs a nicer typeface and
# nothing else.
PLACEHOLDER_FONTS = (
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
)


def _placeholder_font(size: int):
    for path in PLACEHOLDER_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hex(value: str | None, fallback: str) -> tuple[int, int, int]:
    raw = (value or fallback).lstrip("#")
    if len(raw) != 6:
        raw = fallback
    return tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))


def _palette(ctx: dict) -> dict:
    return {
        "light": _hex(ctx.get("color4_hex"), "F4F1EC"),
        "ink": _hex(ctx.get("color3_hex"), "1B222C"),
        "accent": _hex(ctx.get("color1_hex"), "102C48"),
        "muted": _hex(ctx.get("color5_hex"), "7A8089"),
    }


# ------------------------------------------------------------- gathering ----

def manual_photo(record: dict) -> Path | None:
    """A photo Larry dropped in by hand, which outranks everything else."""
    folder = store.ASSETS / store.partner_id(record)
    if not folder.is_dir():
        return None
    for name in PHOTO_NAMES:
        for suffix in PHOTO_SUFFIXES:
            candidate = folder / f"{name}{suffix}"
            if candidate.exists():
                return candidate
    return None


def cache_dir(record: dict) -> Path:
    return store.ASSETS / store.partner_id(record) / "product-shots"


def cached_shots(record: dict) -> list[Path]:
    folder = cache_dir(record)
    if not folder.is_dir():
        return []
    return sorted(
        path for path in folder.iterdir()
        if path.suffix.lower() in PHOTO_SUFFIXES
    )


def download_shots(record: dict, force: bool = False) -> dict:
    """Fetch the record's cached Shopify image URLs into the assets folder.

    Returns {ok, downloaded, skipped, errors}. Every failure is reported and
    none of them raise: a partner with no network still gets a package, just
    without new photography.
    """
    urls = [u for u in (record.get("product_shot_urls") or []) if u]
    result = {"ok": True, "downloaded": 0, "skipped": 0, "errors": []}
    if not urls:
        result["ok"] = False
        result["errors"].append("no product image URLs on this partner record")
        return result

    folder = cache_dir(record)
    folder.mkdir(parents=True, exist_ok=True)

    for index, url in enumerate(urls[:MAX_SHOTS], start=1):
        suffix = ".png" if ".png" in url.lower().split("?")[0] else ".jpg"
        target = folder / f"{index:02d}{suffix}"
        if target.exists() and not force:
            result["skipped"] += 1
            continue
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                data = response.read()
            # Decode before writing, so a captive-portal HTML page or a 404
            # body never lands in the folder pretending to be a product shot.
            Image.open(BytesIO(data)).verify()
            target.write_bytes(data)
            result["downloaded"] += 1
        except (urllib.error.URLError, OSError, ValueError) as exc:
            result["errors"].append(f"{url.split('/')[-1].split('?')[0]}: {exc}")

    result["ok"] = bool(cached_shots(record))
    return result


def gather(record: dict) -> tuple[str, list[Path], list[str]]:
    """(source, images, errors). Never another partner's images."""
    manual = manual_photo(record)
    if manual:
        return "manual", [manual], []

    shots = cached_shots(record)
    if shots:
        return "cached", shots[:MAX_SHOTS], []

    if record.get("product_shot_urls"):
        result = download_shots(record)
        shots = cached_shots(record)
        if shots:
            return "downloaded", shots[:MAX_SHOTS], result["errors"]
        return "none", [], result["errors"]

    return "none", [], ["no product image URLs on this partner record"]


# --------------------------------------------------------------- drawing ----

def _fit_square(image: Image.Image, size: int, background) -> Image.Image:
    """Fit a product shot into a square cell, centred, on the sheet colour.

    Product mockups arrive square from the POD apps but hand-shot photos do
    not, so never crop -- a cropped garment reads as a mistake in a document
    the partner forwards to their congregation.
    """
    cell = Image.new("RGB", (size, size), background)
    shot = image.convert("RGBA")
    shot.thumbnail((size - 2, size - 2), Image.LANCZOS)
    cell.paste(shot, ((size - shot.width) // 2, (size - shot.height) // 2), shot)
    return cell


def _layout(count: int) -> list[tuple[int, int, int, int]]:
    """Cell boxes for 1-4 shots, in a frame of SHEET_W x SHEET_H."""
    inner_w = SHEET_W - 2 * PAD
    inner_h = SHEET_H - 2 * PAD

    if count <= 1:
        return [(PAD, PAD, inner_w, inner_h)]

    half_w = (inner_w - GUTTER) // 2

    if count == 2:
        # Side by side, vertically centred. Two full-width rows would leave a
        # square shot marooned in the middle of each with 260px of dead space
        # either side; a centred pair reads as a deliberate layout.
        top = (SHEET_H - half_w) // 2
        return [
            (PAD, top, half_w, half_w),
            (PAD + half_w + GUTTER, top, half_w, half_w),
        ]

    if count == 3:
        big_h = int(inner_h * 0.56)
        small_h = inner_h - big_h - GUTTER
        return [
            (PAD, PAD, inner_w, big_h),
            (PAD, PAD + big_h + GUTTER, half_w, small_h),
            (PAD + half_w + GUTTER, PAD + big_h + GUTTER, half_w, small_h),
        ]

    half_h = (inner_h - GUTTER) // 2
    return [
        (PAD, PAD, half_w, half_h),
        (PAD + half_w + GUTTER, PAD, half_w, half_h),
        (PAD, PAD + half_h + GUTTER, half_w, half_h),
        (PAD + half_w + GUTTER, PAD + half_h + GUTTER, half_w, half_h),
    ]


def _placeholder(ctx: dict, record: dict, colours: dict) -> Image.Image:
    """For a partner whose store has no products yet.

    Deliberately not a fake garment. The kit goes out before some stores have
    anything in them, and an invented mockup would be the same lie as the
    borrowed photo it replaces -- just harder to spot.
    """
    sheet = Image.new("RGB", (SHEET_W, SHEET_H), colours["light"])
    draw = ImageDraw.Draw(sheet)

    # Say whose card this is and why it is empty. A blank cream rectangle in a
    # partner-facing document reads as a broken image; a named one reads as a
    # store that has not been stocked yet, which is the truth.
    name = (record.get("org_name") or "").strip()
    lines = [(name, 46, colours["ink"], 62)] if name else []
    lines += [
        ("Product photos appear here once", 30, colours["muted"], 42),
        ("your store has its first items.", 30, colours["muted"], 42),
    ]

    mark = None
    logo_path = store.resolve_logo(record)
    if logo_path:
        try:
            mark = prepare_logo(logo_path)
            mark.thumbnail((int(SHEET_W * 0.5), int(SHEET_H * 0.30)), Image.LANCZOS)
        except Exception:
            mark = None

    # Compose logo, rule and text as one block and centre the block, rather
    # than pinning the text to a fixed offset -- otherwise a partner with no
    # logo yet gets a card with everything crowded into the bottom third.
    text_h = sum(step for _, _, _, step in lines)
    block_h = text_h + 40 + (mark.height + 46 if mark else 0)
    y = (SHEET_H - block_h) // 2

    if mark:
        sheet.paste(mark, ((SHEET_W - mark.width) // 2, y), mark)
        y += mark.height + 46

    draw.line([(SHEET_W // 2 - 90, y), (SHEET_W // 2 + 90, y)],
              fill=colours["accent"], width=4)
    y += 40

    for text, size, colour, step in lines:
        font = _placeholder_font(size)
        width = draw.textlength(text, font=font)
        draw.text(((SHEET_W - width) / 2, y), text, font=font, fill=colour)
        y += step

    return sheet


def build(record: dict, ctx: dict, out_dir: Path, namer) -> dict:
    """Write the partner's product sheet. Returns {path, source, count}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    colours = _palette(ctx)

    source, images, errors = gather(record)

    if not images:
        sheet = _placeholder(ctx, record, colours)
    else:
        sheet = Image.new("RGB", (SHEET_W, SHEET_H), colours["light"])
        boxes = _layout(len(images))
        for path, (x, y, w, h) in zip(images, boxes):
            try:
                shot = Image.open(path)
            except Exception:
                continue
            side = min(w, h)
            cell = _fit_square(shot, side, colours["light"])
            sheet.paste(cell, (x + (w - side) // 2, y + (h - side) // 2))

    path = out_dir / namer("Product-Photo", "png")
    sheet.save(path, "PNG", dpi=(300, 300))
    return {
        "path": str(path),
        "source": source,
        "count": len(images),
        "errors": errors,
    }
