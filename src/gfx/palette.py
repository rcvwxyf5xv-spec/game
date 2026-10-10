"""
Per-zone palettes for the voxel-art look.

Every zone owns:
  - a material ramp for rock (8 shades, dark -> light)
  - a "cap" ramp for the top surface (icy frost / moss / flesh / ember / rust)
  - an accent/glow color
  - a vertical fog gradient (top/bottom) for the background
  - an ambient light color used by the per-frame LightMap

Everything is authored as plain tuples; the caller (voxel renderers) does
the shading/quantization quantization via ``ramp_color`` / ``dither_pick``.
"""

from src.project import load_zones

RAMPS = {
    "Blue Brinstar": {
        "rock": [
            (12, 20, 34),
            (20, 32, 52),
            (30, 46, 72),
            (42, 62, 94),
            (56, 82, 118),
            (74, 104, 146),
            (96, 130, 176),
            (124, 162, 208),
        ],
        "cap": [
            (60, 140, 168),
            (84, 168, 196),
            (110, 198, 224),
            (138, 220, 244),
            (168, 236, 252),
            (198, 248, 255),
            (224, 255, 255),
            (248, 255, 255),
        ],
        "accent": (150, 220, 255),
        "fog_top": (8, 14, 26),
        "fog_bottom": (16, 28, 48),
        "ambient": (190, 205, 225),
    },
    "Green Brinstar": {
        "rock": [
            (10, 20, 14),
            (18, 32, 22),
            (28, 48, 34),
            (40, 66, 48),
            (54, 88, 64),
            (72, 112, 82),
            (92, 140, 102),
            (116, 172, 126),
        ],
        "cap": [
            (48, 110, 60),
            (66, 136, 78),
            (88, 162, 98),
            (112, 190, 118),
            (138, 214, 140),
            (164, 234, 160),
            (190, 248, 180),
            (216, 255, 200),
        ],
        "accent": (160, 255, 150),
        "fog_top": (6, 16, 10),
        "fog_bottom": (14, 34, 20),
        "ambient": (190, 215, 195),
    },
    "Pink Brinstar": {
        "rock": [
            (30, 10, 24),
            (46, 16, 38),
            (66, 24, 54),
            (88, 34, 72),
            (112, 46, 94),
            (140, 62, 118),
            (170, 82, 144),
            (204, 106, 172),
        ],
        "cap": [
            (128, 58, 116),
            (154, 74, 140),
            (180, 92, 164),
            (204, 112, 188),
            (226, 134, 208),
            (242, 158, 226),
            (252, 184, 240),
            (255, 214, 250),
        ],
        "accent": (255, 190, 230),
        "fog_top": (24, 10, 22),
        "fog_bottom": (42, 18, 38),
        "ambient": (215, 190, 210),
    },
    "Red Brinstar": {
        "rock": [
            (34, 12, 8),
            (52, 18, 12),
            (74, 26, 16),
            (100, 36, 22),
            (128, 48, 30),
            (160, 62, 40),
            (196, 80, 52),
            (234, 100, 66),
        ],
        "cap": [
            (96, 32, 18),
            (128, 44, 24),
            (162, 58, 30),
            (196, 74, 38),
            (226, 92, 48),
            (248, 112, 60),
            (255, 138, 74),
            (255, 170, 90),
        ],
        "accent": (255, 170, 100),
        "fog_top": (22, 8, 6),
        "fog_bottom": (42, 16, 10),
        "ambient": (220, 190, 180),
    },
    "Kraid's Lair": {
        "rock": [
            (30, 26, 10),
            (44, 38, 14),
            (60, 52, 20),
            (80, 68, 26),
            (102, 88, 34),
            (128, 110, 44),
            (158, 136, 56),
            (192, 164, 70),
        ],
        "cap": [
            (70, 62, 26),
            (92, 82, 34),
            (116, 104, 44),
            (142, 128, 56),
            (170, 152, 68),
            (198, 178, 82),
            (226, 204, 98),
            (250, 230, 116),
        ],
        "accent": (255, 220, 110),
        "fog_top": (16, 14, 6),
        "fog_bottom": (30, 26, 10),
        "ambient": (210, 205, 180),
    },
}

# Fallback for unknown/unnamed zones (plain gray stone cave, cool neutral).
DEFAULT_PALETTE = {
    "rock": [
        (18, 18, 24),
        (30, 30, 40),
        (44, 44, 58),
        (60, 60, 78),
        (78, 78, 100),
        (100, 100, 124),
        (124, 124, 150),
        (150, 150, 178),
    ],
    "cap": [
        (60, 66, 84),
        (80, 88, 108),
        (102, 110, 134),
        (126, 134, 160),
        (150, 158, 186),
        (174, 182, 210),
        (198, 206, 232),
        (222, 228, 252),
    ],
    "accent": (200, 200, 220),
    "fog_top": (12, 12, 18),
    "fog_bottom": (24, 24, 34),
    "ambient": (205, 205, 215),
}

# Zone palettes from the active project's zones.json override RAMPS; the
# converted dicts are cached per zone and dropped when the zones data
# object changes (see get_palette).
_CONVERTED = {}
_ZONES_ID = None


def _as_tuples(value):
    """Recursively turn lists (from JSON) into tuples."""
    if isinstance(value, list):
        return tuple(_as_tuples(v) for v in value)
    return value


def _convert_zone(pal: dict) -> dict:
    return {key: _as_tuples(val) for key, val in pal.items()}


def get_palette(zone: str) -> dict:
    """Per-zone palette dict; zones data from the active project wins,
    then RAMPS, then DEFAULT_PALETTE for unknown/unnamed zones."""
    global _ZONES_ID
    zones = load_zones()
    zid = id(zones)
    if _ZONES_ID != zid:
        _CONVERTED.clear()
        _ZONES_ID = zid
    cached = _CONVERTED.get(zone)
    if cached is not None:
        return cached
    if zone in zones:
        pal = _convert_zone(zones[zone])
    else:
        pal = RAMPS.get(zone, DEFAULT_PALETTE)
    _CONVERTED[zone] = pal
    return pal


def ramp_color(ramp, t: float) -> tuple:
    """Pick a shade from a ramp on 0..1 (clamped). Fast integer lerp over
    the 8-entry ramps so shading has the limited 256-color feel."""
    t = max(0.0, min(1.0, t))
    slot = t * (len(ramp) - 1)
    i = int(slot)
    j = min(i + 1, len(ramp) - 1)
    f = slot - i
    a, b = ramp[i], ramp[j]
    return (
        int(a[0] + (b[0] - a[0]) * f),
        int(a[1] + (b[1] - a[1]) * f),
        int(a[2] + (b[2] - a[2]) * f),
    )


_BAYER4 = (
    (0, 8, 2, 10),
    (12, 4, 14, 6),
    (3, 11, 1, 9),
    (15, 7, 13, 5),
)


def dither_pick(ramp, t: float, x: int, y: int) -> tuple:
    """ramp_color with ordered-dithering: the shade chosen is nudged by a
    4x4 Bayer pattern so bands break up into the classic 256-color speckle
    instead of smooth gradients."""
    t = max(0.0, min(1.0, t))
    grid_w = len(ramp) - 1
    bayer = _BAYER4[y & 3][x & 3] / 15.0
    # dithering picks between the two adjacent quantization slots
    slot_f = t * grid_w
    lo = int(slot_f)
    hi = min(lo + 1, grid_w)
    frac = slot_f - lo
    if frac < 1e-9:
        return ramp[lo]
    chosen = lo if frac < bayer else hi
    return ramp[max(0, min(grid_w, chosen))]
