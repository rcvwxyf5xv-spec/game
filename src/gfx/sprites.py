"""
Voxel character and pickup sprites.

The player is a chunky power-suit built from parametric voxel parts
(helmet, chest plate, shoulder pads, arm cannon, legs) and rendered as a
pre-rendered-style sprite. Pickups are voxel cages with a glowing orb,
pre-rendered at 12 yaw angles. All rendering happens once per key and
is cached in module-level dicts.
"""

from __future__ import annotations

import hashlib
import math
import random

import pygame

from src.gfx.voxel import VoxelModel, render_model

# Player palette (classic orange/red power suit)
ARMOR = (206, 88, 40)
ARMOR_LIGHT = (250, 168, 60)
ARMOR_DARK = (150, 58, 28)
JOINT = (46, 44, 54)
GLOW_VISOR = (96, 246, 150)
GLOW_VISOR_LIGHT = (210, 255, 228)
CANNON = (176, 178, 198)
CANNON_DARK = (104, 106, 124)
CANNON_TIP = (250, 216, 92)

PLAYER_VOXEL = 2

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
# Player model
# ---------------------------------------------------------------------------


def _leg_box(which, phase, pose):
    """Front ('f') or back ('b') leg box (x0, y0, z0, w, h, d) for a pose."""
    if pose == "run":
        swing = math.sin(phase / 6.0 * 2.0 * math.pi)
        off = int(round(swing * 2.0))
        if which == "f":
            return (7 + off, 4, 0, 5, 4, 7)
        return (2 - off, 1, 0, 5, 4, 7)
    if pose == "jump":
        if which == "f":
            return (7, 4, 4, 5, 4, 3)
        return (2, 1, 4, 5, 4, 3)
    if pose == "fall":
        if which == "f":
            return (8, 4, 0, 5, 4, 5)
        return (1, 1, 0, 5, 4, 5)
    # idle / default standing
    if which == "f":
        return (7, 4, 0, 5, 4, 7)
    return (3, 1, 0, 5, 4, 7)


def build_player_model(pose, phase):
    """Assemble the power-suit voxel model for a pose ('idle','run',
    'jump','fall') and animation phase. Body is 17 wide (x), 9 deep (y),
    22 tall (z); faces +x."""
    model = VoxelModel()
    bob = int(round(math.sin(phase * math.pi) * 0.6)) if pose == "idle" else 0
    body_z = bob + (1 if pose == "jump" else 0)

    for which in ("b", "f"):
        x0, y0, z0, w, h, d = _leg_box(which, phase, pose)
        model.add_box(x0, y0, z0 + body_z, w, h, d, JOINT if which == "b" else ARMOR_DARK)
        model.add_box(x0 + 1, y0 + 1, z0 + body_z, w - 2, h - 2, 2, ARMOR)

    # waist / pelvis
    model.add_box(3, 2, 6 + body_z, 11, 8, 3, JOINT)

    # torso (chest plate)
    model.add_box(2, 0, 9 + body_z, 13, 8, 6, ARMOR_DARK)
    model.add_box(2, 1, 10 + body_z, 13, 6, 4, ARMOR)
    model.add_box(3, 1, 10 + body_z, 4, 2, 4, ARMOR_LIGHT)

    # shoulder pads
    model.add_box(0, 1, 15 + body_z, 4, 6, 3, ARMOR)
    model.add_box(12, 1, 15 + body_z, 4, 6, 3, ARMOR)
    model.add_box(1, 2, 16 + body_z, 2, 4, 1, ARMOR_LIGHT)
    model.add_box(13, 2, 16 + body_z, 2, 4, 1, ARMOR_LIGHT)

    # back arm (swings opposite the front arm while running)
    swing_b = 0
    if pose == "run":
        swing_b = int(round(-math.sin(phase / 6.0 * 2.0 * math.pi) * 2.0))
    elif pose == "jump":
        swing_b = 2
    model.add_box(1, 5, 12 + body_z, 3, 4, 4 - max(0, swing_b), ARMOR_DARK)

    # front arm + big arm cannon on the facing side
    swing_f = 0
    if pose == "run":
        swing_f = int(round(math.sin(phase / 6.0 * 2.0 * math.pi) * 1.6))
    elif pose in ("jump", "fall"):
        swing_f = 2
    cannon_z = 13 + body_z - max(0, swing_f)
    model.add_box(12, 5, 12 + body_z, 3, 4, 4, ARMOR)
    model.add_box(13, 3, cannon_z + 1, 5, 4, 5, CANNON)
    model.add_box(14, 3, cannon_z + 2, 3, 2, 3, CANNON_DARK)
    model.add_box(15, 3, cannon_z + 2, 2, 4, 3, CANNON_TIP)

    # helmet: rounded dome + visor on the facing side
    model.add_ellipsoid(8, 2, 18 + body_z, 5, 3, 4, ARMOR)
    model.add_box(4, 1, 18 + body_z, 10, 5, 3, ARMOR_DARK)
    model.add_box(10, 2, 19 + body_z, 3, 2, 2, GLOW_VISOR)
    model.add_voxel(11, 2, 20 + body_z, GLOW_VISOR_LIGHT)
    return model


def get_player_frame(state, frame_index, facing):
    """(Surface, anchor_x, anchor_y) for one player frame. `state` is
    'idle'/'run'/'jump'/'fall', `facing` 1 = right, -1 = left. Blit so
    (anchor_x, anchor_y) sits at the physics hitbox bottom-center."""
    key = (state, frame_index, facing)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pose = state if state in ("idle", "run", "jump", "fall") else "idle"
    model = build_player_model(pose, frame_index)
    surf, (ax, ay) = render_model(model, PLAYER_VOXEL, light=(-0.35, -0.45, 0.82))
    if facing < 0:
        surf = pygame.transform.flip(surf, True, False)
        ax = surf.get_width() - 1 - ax
    result = (surf, ax, ay)
    if len(_CACHE) < 256:
        _CACHE[key] = result
    return result


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


# ---------------------------------------------------------------------------
# Save stations
# ---------------------------------------------------------------------------


def get_save_station():
    """Voxel pedestal with a glowing ring pad, 48px wide. Returns
    (Surface, anchor_y): blit the surface so the bottom of the silhouette
    sits at anchor_y below the surface top."""
    key = ("save_station",)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    model = VoxelModel()
    accent = (120, 220, 255)
    # pedestal base
    model.add_box(2, 2, 0, 12, 12, 2, (86, 92, 110))
    model.add_box(3, 3, 2, 10, 10, 2, (126, 134, 156))
    model.add_box(5, 5, 4, 6, 6, 4, (72, 78, 96))
    # glowing ring pad
    model.add_box(3, 4, 8, 10, 8, 1, (46, 52, 68))
    for a in range(8):
        x = 4 + int(round(4.5 * math.cos(a / 8.0 * 2.0 * math.pi)))
        y = 4 + int(round(4.5 * math.sin(a / 8.0 * 2.0 * math.pi)))
        model.add_voxel(x, y, 9, accent)
    model.add_voxel(8, 6, 9, (200, 245, 255))
    surf, (ax, ay) = render_model(model, 3, yaw_deg=25, pitch_deg=22, light=(-0.4, -0.5, 0.8))
    result = (surf, ay)
    _CACHE[key] = result
    return result
