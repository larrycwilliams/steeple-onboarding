"""The screens everything we build must work on (doc 70: the device standard).

Sizes are CSS pixels, the numbers a browser lays out against. A page passes
when nothing is wider than the screen at every one of these and it can be
read and tapped at each.

The iPhone Duo's two sizes are ESTIMATES until Larry reads them off the
Portal's Settings page ("This screen") folded and unfolded; then replace
them here and in steeple-studio/tests/devices.py.
"""

DEVICES = [
    # Phones
    {"name": "duo-folded", "w": 360, "h": 780, "dpr": 3, "mobile": True},   # estimate
    {"name": "iphone", "w": 390, "h": 844, "dpr": 3, "mobile": True},
    {"name": "iphone-max", "w": 440, "h": 956, "dpr": 3, "mobile": True},
    {"name": "iphone-land", "w": 844, "h": 390, "dpr": 3, "mobile": True},
    # Fold open and tablets
    {"name": "duo-open", "w": 700, "h": 800, "dpr": 3, "mobile": True},     # estimate
    {"name": "ipad-mini", "w": 744, "h": 1133, "dpr": 2, "mobile": True},
    {"name": "ipad-air", "w": 820, "h": 1180, "dpr": 2, "mobile": True},
    {"name": "ipad-land", "w": 1180, "h": 820, "dpr": 2, "mobile": True},
    # Laptops and desktops
    {"name": "laptop-13", "w": 1280, "h": 800, "dpr": 2},
    {"name": "laptop-14", "w": 1512, "h": 982, "dpr": 2},
    # Shop and office screens
    {"name": "tv-1080", "w": 1920, "h": 1080, "dpr": 1},
    {"name": "tv-4k", "w": 3840, "h": 2160, "dpr": 1},
]
