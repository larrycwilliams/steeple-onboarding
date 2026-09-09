"""Logo handling shared by the postcards and the QR generator.

Partners send artwork in whatever state they have it: transparent PNGs, marks
flattened onto a white box, all-black silhouettes. These helpers make any of
them usable without altering the mark itself.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


WHITE_TOLERANCE = 26      # how close to white counts as background
MIN_LOGO_CONTRAST = 2.0   # below this the mark is placed on a plate instead


def _relative_luminance(rgb) -> float:
    channels = []
    for value in rgb[:3]:
        c = value / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b) -> float:
    la, lb = _relative_luminance(a), _relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return (high + 0.05) / (low + 0.05)


def prepare_logo(path: str | Path) -> Image.Image:
    """Load a logo and drop a solid white background if it has one.

    Partners send marks both ways: PNGs with real transparency, and flattened
    files with a white box behind them. The flattened ones would paste onto the
    card as a white rectangle, so the background is flood-filled away from the
    edges inward -- which leaves white *inside* the mark (an eye, a highlight)
    untouched, unlike a blanket "make white transparent" pass.
    """
    image = Image.open(path).convert("RGBA")

    alpha = image.getchannel("A")
    if alpha.getextrema()[0] < 250:
        return image                      # already has real transparency

    flat = image.convert("RGB")
    corners = [
        flat.getpixel((0, 0)),
        flat.getpixel((flat.width - 1, 0)),
        flat.getpixel((0, flat.height - 1)),
        flat.getpixel((flat.width - 1, flat.height - 1)),
    ]
    if not all(min(c) >= 255 - WHITE_TOLERANCE for c in corners):
        return image                      # not a white-boxed logo; leave it

    SENTINEL = (255, 0, 255)
    for corner in ((0, 0), (flat.width - 1, 0),
                   (0, flat.height - 1), (flat.width - 1, flat.height - 1)):
        try:
            ImageDraw.floodfill(flat, corner, SENTINEL, thresh=WHITE_TOLERANCE)
        except Exception:
            continue

    knocked = image.copy()
    pixels = knocked.load()
    source = flat.load()
    for y in range(knocked.height):
        for x in range(knocked.width):
            if source[x, y] == SENTINEL:
                r, g, b, _ = pixels[x, y]
                pixels[x, y] = (r, g, b, 0)
    return knocked


# Measured against real marks: the GCS cardinal puts 10% of its pixels above
# the contrast threshold on a near-black card (the red crest), while an all-black
# silhouette puts 0%. 8% sits clear of both.
LEGIBLE_SHARE = 0.08
LEGIBLE_CONTRAST = 2.5


def _logo_reads(logo: Image.Image, background) -> tuple[bool, tuple | None]:
    """Does enough of the mark contrast with the card to be seen?

    Averaging the whole mark is the wrong test: a cardinal is mostly near-black
    body with a red crest, and the mean says "invisible" while the crest reads
    perfectly well. So this measures the *share* of visible pixels that clear a
    contrast threshold, and only plates the logo when almost none of it does.
    Returns (reads, mean_ink) -- the mean is used to choose a plate colour.
    """
    import numpy as np

    data = np.asarray(logo, dtype=float)
    if data.ndim != 3 or data.shape[2] < 4:
        return True, None
    visible = data[data[..., 3] > 40][:, :3]
    if not len(visible):
        return True, None

    srgb = visible / 255
    lin = np.where(srgb <= 0.03928, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    lum = 0.2126 * lin[:, 0] + 0.7152 * lin[:, 1] + 0.0722 * lin[:, 2]

    bg = _relative_luminance(background)
    high = np.maximum(lum, bg)
    low = np.minimum(lum, bg)
    ratios = (high + 0.05) / (low + 0.05)

    share = float((ratios >= LEGIBLE_CONTRAST).mean())
    mean_ink = tuple(int(v) for v in visible.mean(axis=0))
    return share >= LEGIBLE_SHARE, mean_ink


