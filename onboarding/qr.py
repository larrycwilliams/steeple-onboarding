"""Branded QR generation at print spec.

Codes point at a redirect you control (/go/<slug>) rather than the raw
collection URL, so a Shopify collection rename never kills printed postcards.
Error correction is fixed at H (30%) so the centre logo never costs a scan.
"""
from __future__ import annotations

import csv
from io import BytesIO
from pathlib import Path

import segno
from PIL import Image, ImageDraw

from .imaging import prepare_logo

PRINT_PX = 1176          # matches the spec already published in the Launch Kit
LOGO_FRACTION = 0.26     # centre mark as a share of code WIDTH (~7% of area)
LOGO_RETRY_STEPS = (0.26, 0.22, 0.18, 0.14)  # shrink until it verifiably scans
QUIET_ZONE_MODULES = 4


def _scale_for(qr: segno.QRCode, target_px: int) -> int:
    modules = qr.symbol_size(scale=1, border=QUIET_ZONE_MODULES)[0]
    return max(1, round(target_px / modules))


def _default_namer(descriptor: str, ext: str) -> str:
    return f"{descriptor}.{ext}"


def make_qr(
    url: str,
    out_dir: Path | str,
    namer=None,
    logo_path: str | None = None,
    dark: str = "#000000",
    accent: str | None = None,
) -> dict[str, str]:
    """Write print and digital QR assets. Returns {label: path}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    namer = namer or _default_namer

    qr = segno.make(url, error="h")
    scale = _scale_for(qr, PRINT_PX)

    written: dict[str, str] = {}

    svg_path = out_dir / namer("QR-Vector", "svg")
    qr.save(str(svg_path), scale=10, border=QUIET_ZONE_MODULES, dark=dark, light="#FFFFFF")
    written["QR vector (SVG)"] = str(svg_path)

    plain_path = out_dir / namer("QR-Print-1176px", "png")
    qr.save(str(plain_path), scale=scale, border=QUIET_ZONE_MODULES, dark=dark, light="#FFFFFF")
    written["QR print hi-res"] = str(plain_path)

    if logo_path and Path(logo_path).exists():
        branded, fraction, verified = _best_branded(
            qr, scale, logo_path, dark, accent, url
        )
        branded_path = out_dir / namer("QR-Branded-Print", "png")
        branded.save(branded_path, dpi=(300, 300))
        written["QR branded print"] = str(branded_path)
        make_qr.last_check = {
            "logo_fraction": fraction,
            "verified": verified,
            "url": url,
        }

    return written


def _best_branded(qr, scale, logo_path, dark, accent, url):
    """Largest logo that still verifiably decodes.

    Starts at the nicest-looking size and steps down only if the decode fails,
    so the mark is as big as it can safely be rather than as small as is safe.
    """
    fallback = None
    for fraction in LOGO_RETRY_STEPS:
        image = _with_logo(qr, scale, logo_path, dark, accent, fraction)
        result = verify_scannable(image, url)
        if result is None:            # no OpenCV — accept the default size
            return image, fraction, None
        if result:
            return image, fraction, True
        fallback = fallback or image
    return fallback, LOGO_RETRY_STEPS[-1], False


def _rgb(value: str) -> tuple[int, int, int]:
    value = (value or "#000000").lstrip("#")
    if len(value) != 6:
        value = "000000"
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def _with_logo(qr: segno.QRCode, scale: int, logo_path: str, dark: str,
               accent: str | None = None, fraction: float = LOGO_FRACTION) -> Image.Image:
    """Drop the partner's mark into the centre on a framed white plate.

    The white plate is what keeps the code readable: it gives the scanner a
    clean quiet area around the mark instead of logo pixels bleeding into
    modules. The accent ring is cosmetic and sits outside that plate.
    """
    buf = BytesIO()
    qr.save(buf, kind="png", scale=scale, border=QUIET_ZONE_MODULES, dark=dark, light="#FFFFFF")
    buf.seek(0)
    code = Image.open(buf).convert("RGBA")

    width, height = code.size
    box = int(width * fraction)

    # Same preparation as the postcards: a white-boxed logo would otherwise
    # paste a white square over the centre of the code.
    logo = prepare_logo(logo_path)
    logo.thumbnail((box, box), Image.LANCZOS)

    pad = int(box * 0.16)
    ring = max(3, int(box * 0.045)) if accent else 0
    plate_w = logo.width + (pad + ring) * 2
    plate_h = logo.height + (pad + ring) * 2

    plate = Image.new("RGBA", (plate_w, plate_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(plate)
    radius = int(min(plate_w, plate_h) * 0.18)

    if ring:
        draw.rounded_rectangle(
            [0, 0, plate_w - 1, plate_h - 1], radius=radius,
            fill=(*_rgb(accent), 255),
        )
    draw.rounded_rectangle(
        [ring, ring, plate_w - 1 - ring, plate_h - 1 - ring],
        radius=max(2, radius - ring), fill=(255, 255, 255, 255),
    )
    plate.paste(logo, (pad + ring, pad + ring), logo)

    code.alpha_composite(plate, ((width - plate_w) // 2, (height - plate_h) // 2))
    return code.convert("RGB")


def verify_scannable(image: Image.Image, expected_url: str) -> bool | None:
    """Decode the finished code and confirm it still resolves to the URL.

    Returns True/False, or None when OpenCV isn't installed (the check is
    optional -- the app works without it, you just don't get the guarantee).
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        array = np.array(image.convert("RGB"))[:, :, ::-1]
        decoded, _, _ = cv2.QRCodeDetector().detectAndDecode(array)
        return decoded == expected_url
    except Exception:
        return None


def redirect_row(record_ctx: dict) -> tuple[str, str]:
    """One Shopify URL-redirect mapping: /go/<slug> -> /collections/<handle>."""
    return (
        f"/go/{record_ctx['redirect_slug']}",
        f"/collections/{record_ctx['collection_handle']}",
    )


def write_redirect_csv(rows: list[tuple[str, str]], path: Path | str) -> str:
    """Shopify Admin > Online Store > Navigation > URL Redirects accepts this."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Redirect from", "Redirect to"])
        writer.writerows(rows)
    return str(path)
