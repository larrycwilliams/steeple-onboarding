"""Logo handling shared by the postcards and the QR generator.

Partners send artwork in whatever state they have it: transparent PNGs, marks
flattened onto a white box, all-black silhouettes. These helpers make any of
them usable without altering the mark itself.
"""
from __future__ import annotations

from functools import lru_cache
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
    """Cached front door to `_prepare_logo`. Returns a COPY every time.

    One package build asks for the same mark repeatedly -- the branded QR, the
    postcard front, the postcard back -- and each ask used to redo the flood
    fill from scratch. The cache is keyed on the file's size and mtime, so a
    partner replacing their logo invalidates it without anything having to
    remember to.

    The copy is not optional: every caller thumbnails what it gets back, and
    `Image.thumbnail` mutates in place. Handing out the cached object would
    mean the second caller received the first one's 300px version.
    """
    path = Path(path)
    try:
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
    except OSError:
        return _prepare_logo(str(path))
    return _prepared(str(path), stamp).copy()


# Two, not eight. These are full-resolution RGBA marks -- a 4000px logo is 64
# MB in memory -- and every gunicorn worker keeps its own. One package build
# only ever asks for one partner's logo, and regenerate_all works through them
# one at a time, so a bigger cache buys nothing and costs the hub real memory.
@lru_cache(maxsize=2)
def _prepared(path: str, stamp: tuple) -> Image.Image:
    return _prepare_logo(path)


def _prepare_logo(path: str | Path) -> Image.Image:
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

    return _knock_out(image, flat, SENTINEL)


def _knock_out(image: Image.Image, flat: Image.Image, sentinel) -> Image.Image:
    """Zero the alpha wherever the flood fill painted the sentinel.

    This was a per-pixel Python loop over the logo at FULL resolution -- four
    million iterations on a 2000px mark. `_best_branded` in qr.py then called
    `prepare_logo` once per retry step, so it ran up to four times for one
    partner, and the postcards ran it again. That is where four and a half
    minutes of Haven of Hope's package went on 15 Sep, forty seconds short of
    the gunicorn timeout killing a worker mid-write.

    Same result, as one array comparison. The loop stays as the fallback so
    this cannot become the reason a package fails to build.
    """
    knocked = image.copy()
    try:
        import numpy as np
    except ImportError:
        pixels = knocked.load()
        source = flat.load()
        for y in range(knocked.height):
            for x in range(knocked.width):
                if source[x, y] == sentinel:
                    r, g, b, _ = pixels[x, y]
                    pixels[x, y] = (r, g, b, 0)
        return knocked

    mask = (np.asarray(flat) == np.asarray(sentinel, dtype="uint8")).all(axis=2)
    rgba = np.array(knocked)
    rgba[mask, 3] = 0
    return Image.fromarray(rgba, "RGBA")


# Whether a mark survives being put on the card, and how that is judged.
#
# Three tests have stood here. The first asked whether at least 8% of the mark
# cleared a contrast threshold -- tuned on the GCS cardinal, whose red crest
# reads on its own. Any two-toned artwork passes that, and Haven of Hope's did.
#
# The second asked whether at least 70% of it reads. Better, and still wrong,
# because a share of PIXELS is not a share of meaning: the words HAVEN OF are
# thin black type next to a fat blue monogram and a fat blue HOPE, so they can
# be most of what the mark SAYS while being a small minority of what it is
# made of. Tuning that number further is how you end up plating marks that
# were fine.
#
# The third, here, asks both -- and the second question is the one that
# matters. Grid the mark and look for a REGION that has ink in it and almost
# nothing readable. That is what "part of the logo vanished" actually looks
# like, and it does not care whether the missing part is large. A thin dark
# outline running through the whole mark shares its cells with readable ink
# and correctly does not trigger it.
READS_SHARE = 0.70          # of the whole mark, by pixel
LEGIBLE_CONTRAST = 2.5
GRID = (8, 4)               # columns, rows, over the mark's bounding box
CELL_INK_SHARE = 0.01       # a cell holding less ink than this is not a region
CELL_READS_SHARE = 0.20     # a region below this has effectively disappeared


def _logo_reads(logo: Image.Image, background) -> tuple[bool, tuple | None, dict | None]:
    """Does the mark survive this background? (reads, mean_ink, detail).

    `mean_ink` picks a plate colour. `detail` carries the measurements and the
    reason, which the generate page prints -- a silent decision about a
    partner's artwork can only be caught by a human looking at a finished
    card, and that is exactly how the Haven of Hope postcard was caught.
    """
    import numpy as np

    data = np.asarray(logo, dtype=float)
    if data.ndim != 3 or data.shape[2] < 4:
        return True, None, None
    visible = data[..., 3] > 40
    total = int(visible.sum())
    if not total:
        return True, None, None

    srgb = data[..., :3] / 255
    lin = np.where(srgb <= 0.03928, srgb / 12.92, ((srgb + 0.055) / 1.055) ** 2.4)
    lum = 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]
    bg = _relative_luminance(background)
    ratios = (np.maximum(lum, bg) + 0.05) / (np.minimum(lum, bg) + 0.05)
    readable = visible & (ratios >= LEGIBLE_CONTRAST)

    share = float(readable.sum()) / total
    mean_ink = tuple(int(v) for v in data[visible][:, :3].mean(axis=0))

    worst = _worst_region(visible, readable, total)
    detail = {
        "share": share,
        "worst_region": worst,
        "threshold": READS_SHARE,
        "region_threshold": CELL_READS_SHARE,
        "background": "#%02X%02X%02X" % tuple(int(c) for c in background[:3]),
    }
    if share < READS_SHARE:
        detail["reason"] = "less than %d%% of the mark reads against the card" % (
            READS_SHARE * 100)
        return False, mean_ink, detail
    if worst is not None and worst < CELL_READS_SHARE:
        detail["reason"] = "a whole part of the mark disappears into the card"
        return False, mean_ink, detail
    detail["reason"] = ""
    return True, mean_ink, detail


def _worst_region(visible, readable, total) -> float | None:
    """The least readable patch of the mark that actually holds ink.

    Bounding box, not the whole canvas: a mark sitting in one corner of a
    padded PNG would otherwise be measured mostly against empty space.
    """
    import numpy as np

    ys, xs = np.nonzero(visible)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    cols, rows = GRID
    worst = None
    for r in range(rows):
        cy0 = y0 + (y1 - y0) * r // rows
        cy1 = y0 + (y1 - y0) * (r + 1) // rows
        for c in range(cols):
            cx0 = x0 + (x1 - x0) * c // cols
            cx1 = x0 + (x1 - x0) * (c + 1) // cols
            cell = visible[cy0:cy1, cx0:cx1]
            ink = int(cell.sum())
            if ink < total * CELL_INK_SHARE:
                continue
            here = float(readable[cy0:cy1, cx0:cx1].sum()) / ink
            if worst is None or here < worst:
                worst = here
    return worst



