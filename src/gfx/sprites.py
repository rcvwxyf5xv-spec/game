"""Voxel item pickup sprites.

Pickups are voxel cages with a glowing orb, pre-rendered at 12 yaw
angles. All rendering happens once per key and is cached in module-level
dicts.
"""

from __future__ import annotations

import hashlib
import math

import pygame

from src.gfx.voxel import VoxelModel, render_model

_CACHE = {}


def _pickup_color(name):
    """Deterministic pickup color per item name."""
    digest = hashlib.md5(name.encode()).digest()
    palette = [
        (100, 220, 130),
        (90, 180, 255),
        (240, 120, 90),
        (220, 200, 90),
        (150, 120, 255),
        (255, 160, 60),
        (110, 255, 230),
    ]
    return palette[digest[0] % len(palette)]


# ---------------------------------------------------------------------------
# Item pickups
# ---------------------------------------------------------------------------


def _rotate_model(model, deg):
    """Rotate a VoxelModel around its vertical axis by deg, rotating in
    place around the model's center."""
    if not model.voxels:
        return VoxelModel()
    xs = [v[0] for v in model.voxels]
    ys = [v[1] for v in model.voxels]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    theta = math.radians(deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)
    out = VoxelModel()
    for (x, y, z), color in model.voxels.items():
        rx = (x - cx) * cos_t - (y - cy) * sin_t + cx
        ry = (x - cx) * sin_t + (y - cy) * cos_t + cy
        out.voxels[(int(round(rx)), int(round(ry)), z)] = color
    return out


def _build_pickup_model(name):
    """Glowing orb inside a voxel cage (classic rotating pickup)."""
    color = _pickup_color(name)
    bright = tuple(min(255, c + 80) for c in color)
    cage = (110, 116, 132)
    cage_dark = (64, 68, 82)
    model = VoxelModel()
    # four corner posts + top/bottom plates
    model.add_box(2, 2, 1, 2, 2, 10, cage)
    model.add_box(10, 2, 1, 2, 2, 10, cage)
    model.add_box(2, 10, 1, 2, 2, 10, cage)
    model.add_box(10, 10, 1, 2, 2, 10, cage)
    # mid rails
    for z in (4, 8):
        for (px, py) in ((2, 2), (10, 2), (2, 10), (10, 10)):
            model.add_box(px, py, z, 2, 2, 1, cage_dark)
    model.add_box(0, 2, 6, 2, 2, 1, cage_dark)
    model.add_box(0, 10, 6, 2, 2, 1, cage_dark)
    model.add_box(12, 2, 6, 2, 2, 1, cage_dark)
    model.add_box(12, 10, 6, 2, 2, 1, cage_dark)
    # glowing orb inside
    model.add_ellipsoid(7, 6, 7, 4, 3, 4, color)
    model.add_voxel(7, 6, 8, bright)
    return model


def get_item_frames(item_name):
    """12 rotating frames (30 deg yaw steps) of a voxel pickup, colored by
    item name. All frames share the same bounding size so callers can blit
    them centered."""
    key = ("item", item_name)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    base = _build_pickup_model(item_name)
    frames = []
    for i in range(12):
        surf, _ = render_model(_rotate_model(base, i * 30), 3, yaw_deg=20, pitch_deg=22,
                               light=(-0.4, -0.5, 0.8))
        frames.append(surf)
    w = max(f.get_width() for f in frames)
    h = max(f.get_height() for f in frames)
    out = []
    for f in frames:
        pad = pygame.Surface((w, h), pygame.SRCALPHA)
        pad.blit(f, ((w - f.get_width()) // 2, h - f.get_height()))
        out.append(pad)
    _CACHE[key] = out
    return out
