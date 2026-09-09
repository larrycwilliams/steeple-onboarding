"""Print-ready postcard generation, driven by the partner's sampled palette.

Trim 6 x 4 in, bleed 6.25 x 4.25 in, 300 DPI, two pages — matching the spec
already printed in the Launch Week Kit's FILES INCLUDED table.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .imaging import _contrast, _logo_reads, prepare_logo

DPI = 300
TRIM = (6.0, 4.0)
BLEED_IN = 0.125
SAFE_IN = 0.25

BLEED_PX = (
    int((TRIM[0] + BLEED_IN * 2) * DPI),
    int((TRIM[1] + BLEED_IN * 2) * DPI),
)
MARGIN = int((BLEED_IN + SAFE_IN) * DPI)

FONT_CANDIDATES = {
    "bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
}


def _font(weight: str, size: int):
    for path in FONT_CANDIDATES[weight]:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default(size=size)


def _rgb(value: str) -> tuple[int, int, int]:
    value = (value or "000000").lstrip("#")
    if len(value) != 6:
        value = "000000"
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _readable_on(bg: tuple[int, int, int]) -> tuple[int, int, int]:
    r, g, b = [c / 255 for c in bg]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (17, 22, 29) if lum > 0.55 else (255, 255, 255)


def _wrap(draw, text, font, max_width):
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def place_logo(card: Image.Image, logo: Image.Image, xy, background, plate_colour) -> int:
    """Paste the logo, adding a contrast plate when it would disappear.

    A black mark on the dark panel (or a white mark on a light one) is
    invisible. Rather than recolouring the partner's artwork -- which is not
    ours to alter -- it gets set on a plate that contrasts with it.
    Returns the vertical space consumed.
    """
    x, y = xy
    reads, ink = _logo_reads(logo, background)
    if reads or ink is None:
        card.paste(logo, (x, y), logo)
        return logo.height

    # pick whichever plate the mark actually reads against
    candidates = [plate_colour, (255, 255, 255), (17, 22, 29)]
    plate = max(candidates, key=lambda c: _contrast(ink, c))

    pad = int(max(logo.width, logo.height) * 0.12)
    box_w, box_h = logo.width + pad * 2, logo.height + pad * 2
    panel = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        [0, 0, box_w - 1, box_h - 1],
        radius=int(min(box_w, box_h) * 0.12),
        fill=(*plate[:3], 255),
    )
    panel.paste(logo, (pad, pad), logo)
    card.alpha_composite(panel, (x, y)) if card.mode == "RGBA" else card.paste(panel, (x, y), panel)
    return box_h


def _front(ctx: dict, qr_path: str | None, logo_path: str | None) -> Image.Image:
    palette = ctx.get("palette") or []
    primary = _rgb(palette[0]["hex"] if palette else "1B222C")
    ink = _rgb(palette[2]["hex"] if len(palette) > 2 else "11161D")
    chalk = _rgb(palette[3]["hex"] if len(palette) > 3 else "F4F1EC")

    card = Image.new("RGB", BLEED_PX, ink)
    draw = ImageDraw.Draw(card)
    width, height = BLEED_PX

    band_h = int(height * 0.34)
    band_y = height - band_h
    draw.rectangle([0, band_y, width, height], fill=primary)

    # perforation line between the message and the tear-off ticket band
    dot_y = band_y
    for x in range(MARGIN, width - MARGIN, 26):
        draw.ellipse([x, dot_y - 5, x + 10, dot_y + 5], fill=chalk)

    text_on_ink = _readable_on(ink)
    cursor = MARGIN

    if logo_path and Path(logo_path).exists():
        try:
            logo = prepare_logo(logo_path)
            logo.thumbnail((int(width * 0.20), int(height * 0.24)), Image.LANCZOS)
            used = place_logo(card, logo, (MARGIN, cursor), ink, chalk)
            cursor += used + 26
        except Exception:
            pass

    title_font = _font("bold", 116)
    sub_font = _font("regular", 44)

    name = ctx.get("org_name", "")
    for line in _wrap(draw, name, title_font, width - MARGIN * 2)[:2]:
        draw.text((MARGIN, cursor), line, font=title_font, fill=text_on_ink)
        cursor += 124

    cursor += 8
    draw.text(
        (MARGIN, cursor),
        "MERCH STORE  ·  NOW OPEN",
        font=sub_font,
        fill=_rgb(palette[4]["hex"] if len(palette) > 4 else "8A9099"),
    )

    # ticket band: white QR plate on the left, link and pitch on the right
    text_on_primary = _readable_on(primary)
    plate = int(band_h * 0.74)
    plate_x, plate_y = MARGIN, band_y + (band_h - plate) // 2
    draw.rounded_rectangle(
        [plate_x, plate_y, plate_x + plate, plate_y + plate],
        radius=18,
        fill=(255, 255, 255),
    )
    if qr_path and Path(qr_path).exists():
        try:
            code = Image.open(qr_path).convert("RGB")
            inner = plate - 24
            code = code.resize((inner, inner), Image.LANCZOS)
            card.paste(code, (plate_x + 12, plate_y + 12))
        except Exception:
            pass

    tx = plate_x + plate + 46
    ty = plate_y + 6
    draw.text((tx, ty), "SCAN TO SHOP", font=_font("bold", 46), fill=text_on_primary)
    ty += 62
    link_font = _font("bold", 38)
    for line in _wrap(
        draw, ctx.get("redirect_url_display", ""), link_font, width - tx - MARGIN
    )[:2]:
        draw.text((tx, ty), line, font=link_font, fill=text_on_primary)
        ty += 46
    ty += 6
    small = _font("regular", 32)
    pitch = f"Every order {ctx.get('supports_short', 'supports us')}."
    for line in _wrap(draw, pitch, small, width - tx - MARGIN)[:2]:
        draw.text((tx, ty), line, font=small, fill=text_on_primary)
        ty += 38

    return card


def _back(ctx: dict, qr_path: str | None) -> Image.Image:
    palette = ctx.get("palette") or []
    primary = _rgb(palette[0]["hex"] if palette else "1B222C")
    ink = _rgb(palette[2]["hex"] if len(palette) > 2 else "11161D")
    chalk = _rgb(palette[3]["hex"] if len(palette) > 3 else "F4F1EC")
    muted = _rgb(palette[4]["hex"] if len(palette) > 4 else "8A9099")

    card = Image.new("RGB", BLEED_PX, chalk)
    draw = ImageDraw.Draw(card)
    width, height = BLEED_PX

    column = int(width * 0.54)
    cursor = MARGIN

    draw.text((MARGIN, cursor), "WHAT'S IN THE SHOP", font=_font("bold", 46), fill=primary)
    cursor += 66

    body = _font("regular", 36)
    for line in _wrap(draw, ctx.get("product_lineup", ""), body, column - MARGIN)[:3]:
        draw.text((MARGIN, cursor), line, font=body, fill=ink)
        cursor += 46
    cursor += 10
    for line in _wrap(draw, ctx.get("size_range", ""), body, column - MARGIN)[:2]:
        draw.text((MARGIN, cursor), line, font=body, fill=muted)
        cursor += 44

    cursor += 26
    draw.text((MARGIN, cursor), "HOW IT WORKS", font=_font("bold", 46), fill=primary)
    cursor += 66
    steps = [
        "Scan the code or visit the link.",
        "Order any size, any time — nothing to pick up.",
        f"Printed to order, shipped to your door in {ctx.get('fulfillment_days','5-7 days')}.",
    ]
    for index, step in enumerate(steps, start=1):
        for offset, line in enumerate(_wrap(draw, f"{index}.  {step}", body, column - MARGIN)[:2]):
            draw.text((MARGIN + (0 if offset == 0 else 40), cursor), line, font=body, fill=ink)
            cursor += 44
        cursor += 8

    # right-hand panel: QR, link, and a clear area that can carry a mailing block
    panel_x = column + 30
    panel_w = width - panel_x - MARGIN
    draw.rounded_rectangle(
        [panel_x, MARGIN, panel_x + panel_w, MARGIN + panel_w + 210],
        radius=20,
        fill=(255, 255, 255),
    )
    if qr_path and Path(qr_path).exists():
        try:
            code = Image.open(qr_path).convert("RGB")
            inner = panel_w - 90
            code = code.resize((inner, inner), Image.LANCZOS)
            card.paste(code, (panel_x + 45, MARGIN + 45))
        except Exception:
            pass

    ly = MARGIN + panel_w + 20
    link_font = _font("bold", 32)
    for line in _wrap(draw, ctx.get("redirect_url_display", ""), link_font, panel_w - 60)[:2]:
        draw.text((panel_x + 45, ly), line, font=link_font, fill=ink)
        ly += 40

    footer = _font("regular", 28)
    draw.text(
        (MARGIN, height - MARGIN - 34),
        f"{ctx.get('company_name','')}  ·  {ctx.get('company_parent','')}",
        font=footer,
        fill=muted,
    )
    return card


def generate_postcards(ctx: dict, out_dir: Path | str, qr_path: str | None,
                       logo_path: str | None, namer=None) -> dict[str, str]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    namer = namer or (lambda descriptor, ext: f"{descriptor}.{ext}")

    front = _front(ctx, qr_path, logo_path)
    back = _back(ctx, qr_path)

    written: dict[str, str] = {}

    front_path = out_dir / namer("Postcard-Front-PRINT-bleed", "png")
    back_path = out_dir / namer("Postcard-Back-PRINT-bleed", "png")
    front.save(front_path, dpi=(DPI, DPI))
    back.save(back_path, dpi=(DPI, DPI))
    written["Postcard front (bleed)"] = str(front_path)
    written["Postcard back (bleed)"] = str(back_path)

    trim_px = (int(TRIM[0] * DPI), int(TRIM[1] * DPI))
    inset = int(BLEED_IN * DPI)
    for name, image in (("front", front), ("back", back)):
        cropped = image.crop((inset, inset, inset + trim_px[0], inset + trim_px[1]))
        path = out_dir / namer(f"Postcard-{name.capitalize()}-Digital-6x4", "png")
        cropped.save(path, dpi=(DPI, DPI))
        written[f"Postcard {name} (digital)"] = str(path)

    pdf_path = out_dir / namer("Postcard-PRINT", "pdf")
    front.convert("RGB").save(
        pdf_path,
        "PDF",
        resolution=DPI,
        save_all=True,
        append_images=[back.convert("RGB")],
    )
    written["Postcard PRINT PDF"] = str(pdf_path)

    return written
