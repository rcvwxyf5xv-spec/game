"""Colours, metrics and fonts for the Maker GUI (one place to restyle)."""

from __future__ import annotations

import pygame

BG = (20, 20, 26)
PANEL = (32, 32, 40)
PANEL_2 = (40, 40, 50)
PANEL_3 = (50, 50, 62)
BORDER = (66, 66, 84)
TEXT = (222, 222, 232)
TEXT_DIM = (140, 140, 160)
TEXT_DARK = (20, 20, 24)
ACCENT = (255, 190, 80)
SELECT = (64, 104, 168)
HOVER = (60, 60, 76)
ERROR = (255, 96, 84)
WARNING = (255, 212, 96)
INFO = (150, 170, 230)
OK = (110, 220, 140)
INPUT_BG = (24, 24, 30)
FOCUS = (120, 160, 255)

SEVERITY_COLORS = {"error": ERROR, "warning": WARNING, "info": INFO}

# tile kind -> swatch colour for the brush buttons
KIND_COLORS = {
    "empty": (30, 30, 36),
    "solid": (130, 130, 150),
    "spring": (80, 220, 120),
    "oneway": (90, 160, 255),
    "crumble": (240, 150, 70),
}

ROW_H = 22
PAD = 6
TOOLBAR_H = 34
STATUS_H = 24
TOOLSTRIP_W = 132
INSPECTOR_W = 440

_FONTS: dict = {}


def font(size: int = 13, mono: bool = False, bold: bool = False):
    """Cached font; falls back to pygame's built-in font."""
    key = (size, mono, bold)
    f = _FONTS.get(key)
    if f is None:
        if not pygame.font.get_init():
            pygame.font.init()
        names = ("menlo,monaco,dejavusansmono,consolas,couriernew" if mono
                 else "helveticaneue,helvetica,arial,dejavusans")
        try:
            f = pygame.font.SysFont(names, size, bold=bold)
        except Exception:  # pragma: no cover - font backend quirks
            f = pygame.font.Font(None, size + 4)
        _FONTS[key] = f
    return f


def text(surface, s, pos, color=TEXT, size=13, mono=False, bold=False,
         max_w=None):
    """Blit one line of text, ellipsised to max_w; returns its width."""
    f = font(size, mono, bold)
    s = str(s)
    img = f.render(s, True, color)
    if max_w is not None and img.get_width() > max_w:
        while s and f.size(s + "…")[0] > max_w:
            s = s[:-1]
        img = f.render(s + "…", True, color)
    surface.blit(img, pos)
    return img.get_width()
