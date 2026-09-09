"""Sample a brand palette from the partner's own logo.

The hand-built GCS kit used colours sampled from their artwork rather than
guessed, which is why the printed pieces matched the merch. This reproduces
that step: k-means over the logo's opaque pixels, then assign each cluster to
a role by luminance so the roles stay consistent across partners.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .schema import DEFAULT_PALETTE, PALETTE_ROLES

MAX_SIDE = 220
NEUTRAL_SAT = 0.12
ACCENT_SAT = 0.42       # a real brand accent, not a tinted neutral
MIN_ACCENT_LUM = 0.10   # near-black reads as ink even when it has a hue


def _kmeans(pixels: np.ndarray, k: int, iters: int = 40, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    # k-means++ style seeding: spread the initial centres out
    centres = [pixels[rng.integers(len(pixels))]]
    for _ in range(k - 1):
        dist = np.min(
            ((pixels[:, None, :] - np.array(centres)[None, :, :]) ** 2).sum(axis=2), axis=1
        )
        total = dist.sum()
        if total <= 0:
            centres.append(pixels[rng.integers(len(pixels))])
            continue
        centres.append(pixels[rng.choice(len(pixels), p=dist / total)])
    centres = np.array(centres, dtype=float)

    for _ in range(iters):
        labels = np.argmin(
            ((pixels[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2), axis=1
        )
        moved = False
        for i in range(k):
            members = pixels[labels == i]
            if len(members):
                new = members.mean(axis=0)
                if not np.allclose(new, centres[i]):
                    centres[i] = new
                    moved = True
        if not moved:
            break
    return centres, labels


def _hex(rgb) -> str:
    return "{:02X}{:02X}{:02X}".format(*(int(max(0, min(255, c))) for c in rgb))


def _luminance(rgb) -> float:
    r, g, b = [c / 255 for c in rgb]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturation(rgb) -> float:
    r, g, b = [c / 255 for c in rgb]
    high, low = max(r, g, b), min(r, g, b)
    if high == 0:
        return 0.0
    return (high - low) / high


def extract_palette(logo_path: str | Path, k: int = 6) -> list[dict]:
    """Return five role-assigned colours sampled from the logo."""
    path = Path(logo_path)
    if not path.exists():
        return [dict(c) for c in DEFAULT_PALETTE]

    try:
        image = Image.open(path).convert("RGBA")
    except Exception:
        return [dict(c) for c in DEFAULT_PALETTE]

    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    data = np.asarray(image, dtype=float).reshape(-1, 4)
    opaque = data[data[:, 3] > 160][:, :3]
    if len(opaque) < k:
        return [dict(c) for c in DEFAULT_PALETTE]

    centres, labels = _kmeans(opaque, min(k, len(np.unique(opaque, axis=0))))
    counts = np.bincount(labels, minlength=len(centres))

    clusters = [
        {
            "idx": i,
            "rgb": centres[i],
            "hex": _hex(centres[i]),
            "share": counts[i] / counts.sum(),
            "lum": _luminance(centres[i]),
            "sat": _saturation(centres[i]),
        }
        for i in range(len(centres))
        if counts[i] > 0
    ]

    # A brand accent is a genuinely saturated colour that occupies real estate.
    # Ranking by share alone picks the dark navy body of a mark over its red
    # crest, so weight saturation heavily and treat very dark pixels as ink
    # regardless of their hue.
    def is_accent(colour) -> bool:
        return colour["sat"] >= ACCENT_SAT and colour["lum"] >= MIN_ACCENT_LUM

    def accent_score(colour) -> float:
        return (colour["sat"] ** 1.5) * (0.35 + colour["share"])

    chromatic = sorted(
        [c for c in clusters if is_accent(c)], key=accent_score, reverse=True
    )
    if not chromatic:  # nothing saturated enough — relax once
        # Relax the saturation bar, never the luminance one. A near-black
        # pixel can clear NEUTRAL_SAT on rounding noise alone (0E0E12 scores
        # 0.22), and promoting that to "primary accent" is how a monochrome
        # mark ends up with three indistinguishable blacks in its palette.
        chromatic = sorted(
            [
                c for c in clusters
                if c["sat"] >= NEUTRAL_SAT and c["lum"] >= MIN_ACCENT_LUM
            ],
            key=accent_score,
            reverse=True,
        )

    by_luminance = sorted(clusters, key=lambda c: c["lum"])
    fallback = [dict(c) for c in DEFAULT_PALETTE]

    if chromatic:
        # The cluster centroid is dragged dark by antialiased edge pixels, so
        # report the vivid core of the cluster instead -- that is the ink the
        # garment is actually printed in.
        primary = _vivid_representative(opaque, labels, chromatic[0]["idx"])
    else:
        primary = fallback[0]["hex"]

    # Shadow: the darkest cluster sharing the primary's hue family, else a
    # darkened primary. Keeps the two accents related rather than arbitrary.
    primary_hue = _hue(_rgb_from_hex(primary))
    kin = [
        c for c in clusters
        if c["hex"] != primary and abs(_hue_delta(_hue(c["rgb"]), primary_hue)) < 40
        and c["sat"] >= NEUTRAL_SAT
    ]
    if kin:
        shadow = min(kin, key=lambda c: c["lum"])["hex"]
    else:
        shadow = _hex([c * 0.62 for c in _rgb_from_hex(primary)])

    ink = by_luminance[0]["hex"]
    light = by_luminance[-1]["hex"]
    if _luminance(_rgb_from_hex(light)) < 0.80:
        light = fallback[3]["hex"]      # no true light in the mark — use chalk
    if ink == light:
        ink = fallback[2]["hex"]
    muted = _hex(
        [(a + b) / 2 for a, b in zip(_rgb_from_hex(ink), _rgb_from_hex(light))]
    )

    primary, shadow, ink = _separate(primary, shadow, ink, fallback)

    names = _name_colours([primary, shadow, ink, light, muted])
    hexes = [primary, shadow, ink, light, muted]
    return [
        {"name": names[i], "hex": hexes[i], "role": PALETTE_ROLES[i][1]}
        for i in range(5)
    ]


# Below this the two swatches read as one colour on screen and in print.
# Calibrated against real marks rather than guessed: the ALT-WEST monochrome
# logo put its three dark roles ~1-10 apart, while the GCS cardinal's genuine
# red/deep-red pair sits at ~25. Anything between those separates the broken
# case without touching a palette that was already doing its job.
MIN_ROLE_DISTANCE = 12


def _distance(a: str, b: str) -> float:
    """Rough perceptual distance between two hex colours.

    Weighted RGB rather than plain Euclidean: the eye separates greens far
    more finely than blues, and a flat distance calls #0E0E12 and #0E0E11
    "different" while calling two visibly distinct blues "the same".
    """
    ar, ag, ab = _rgb_from_hex(a)
    br, bg, bb = _rgb_from_hex(b)
    return (
        2 * (ar - br) ** 2 + 4 * (ag - bg) ** 2 + 3 * (ab - bb) ** 2
    ) ** 0.5 / 3


def _shift(value: str, factor: float) -> str:
    """Lighten (factor > 1) or darken (factor < 1) a colour."""
    return _hex([c * factor for c in _rgb_from_hex(value)])


def _separate(primary: str, shadow: str, ink: str, fallback: list) -> tuple:
    """Force the three dark roles apart when the mark is monochrome.

    A single-colour logo puts every cluster in the same corner of the space,
    so primary, shadow and ink all land within a few RGB units of each other.
    The form then shows three swatches that look identical, and the documents
    render headings, links and body copy in the same colour -- the palette
    stops doing any work.

    Roles are pulled apart in order of how much the document depends on them:
    ink is the body copy and must stay dark, so it holds; the shadow moves
    first, and the primary is promoted to the brand accent only if it has
    nowhere useful to go.
    """
    if _distance(primary, ink) < MIN_ROLE_DISTANCE:
        # The mark has no accent of its own. Borrow the house accent rather
        # than printing near-black headings on a near-black rule.
        primary = fallback[0]["hex"]

    if _distance(shadow, primary) < MIN_ROLE_DISTANCE:
        shadow = _shift(primary, 0.62)

    if _distance(shadow, ink) < MIN_ROLE_DISTANCE:
        shadow = _shift(primary, 1.28 if _luminance(_rgb_from_hex(primary)) < 0.4
                        else 0.62)

    if _distance(primary, ink) < MIN_ROLE_DISTANCE:
        ink = fallback[2]["hex"]

    return primary, shadow, ink


def _vivid_representative(pixels: np.ndarray, labels: np.ndarray, index: int,
                          percentile: float = 88) -> str:
    """The saturated core of a cluster, not its edge-softened average."""
    members = pixels[labels == index]
    if not len(members):
        return "000000"
    high = members.max(axis=1)
    low = members.min(axis=1)
    sat = np.divide(high - low, np.where(high == 0, 1, high))
    score = sat * (high / 255)
    cutoff = np.percentile(score, percentile)
    core = members[score >= cutoff]
    if not len(core):
        core = members
    return _hex(core.mean(axis=0))


def _hue_delta(a: float, b: float) -> float:
    diff = abs(a - b) % 360
    return diff if diff <= 180 else 360 - diff


def _rgb_from_hex(value: str):
    value = value.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


HUE_NAMES = [
    (15, "Red"), (45, "Orange"), (70, "Gold"), (160, "Green"),
    (200, "Teal"), (255, "Blue"), (290, "Violet"), (335, "Magenta"), (360, "Red"),
]


def _hue(rgb) -> float:
    r, g, b = [c / 255 for c in rgb]
    high, low = max(r, g, b), min(r, g, b)
    delta = high - low
    if delta == 0:
        return 0.0
    if high == r:
        hue = ((g - b) / delta) % 6
    elif high == g:
        hue = (b - r) / delta + 2
    else:
        hue = (r - g) / delta + 4
    return hue * 60


def _name_colours(hexes: list[str]) -> list[str]:  # noqa: C901
    out = []
    for i, value in enumerate(hexes):
        rgb = _rgb_from_hex(value)
        sat, lum = _saturation(rgb), _luminance(rgb)
        if sat < NEUTRAL_SAT:
            if lum > 0.8:
                out.append("Chalk")
            elif lum > 0.45:
                out.append("Steel")
            else:
                out.append("Ink")
            continue
        hue = _hue(rgb)
        base = next(name for bound, name in HUE_NAMES if hue <= bound)
        prefix = "Deep " if lum < 0.28 else ("Light " if lum > 0.72 else "")
        out.append(f"{prefix}{base}".strip())
    # The accent and its shadow are usually the same hue family. Name them as
    # a pair ("Red" / "Deep Red") rather than emitting "Red 2".
    if len(out) > 1 and out[0].split()[-1] == out[1].split()[-1]:
        base = out[0].split()[-1]
        lum0 = _luminance(_rgb_from_hex(hexes[0]))
        lum1 = _luminance(_rgb_from_hex(hexes[1]))
        if lum1 <= lum0:
            out[0], out[1] = base, f"Deep {base}"
        else:
            out[0], out[1] = f"Deep {base}", base

    seen: dict[str, int] = {}
    final = []
    for name in out:
        if name in seen:
            seen[name] += 1
            final.append(f"{name} {seen[name]}")
        else:
            seen[name] = 1
            final.append(name)
    return final
