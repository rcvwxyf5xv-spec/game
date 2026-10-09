"""
Voxel tile renderers: rock tiles, spring pads, one-way planks and crumble
blocks. All tiles are 16 x 16 voxels with voxel_px = 3 (48x48 window px,
SCALE already applied), generated lazily and memoized in module-level
caches. Build-time only: the game at runtime loads pre-built texture
sheets instead of importing this module.

Renderers take a `colors` dict ("rock", "cap", "accent" ramps) plus a
`cache_key` string (the palette label) that replaces the zone string in
memo/rng keys, so texture generation is fully data-driven.

Rock tiles additionally encode a 4-bit neighbor mask (N=1,E=2,S=4,W=8;
out-of-bounds counts as solid) so exposed edges bevel down into noisy
cliff faces and interior faces show low-relief chunky stones. A depth
level 0..3 (distance to the nearest non-solid tile) darkens deep rock so
tunnels read as near-black masses.
"""

from __future__ import annotations

import random
import zlib

import pygame

from src.gfx.palette import dither_pick
from src.gfx.voxel import VoxelModel, render_model, render_relief

TILE_VOXELS = 16
VOXEL_PX = 3
TILE_PX = TILE_VOXELS * VOXEL_PX  # 48
# Design max voxel height per rock tile column: h=1 fills a cell slot,
# h=2..4 poke layers above it (caps, spikes).
ROCK_DESIGN_H = 4

_CACHE = {}


def _seed(*parts):
    """Deterministic int RNG seed. String parts are CRC32'd because str
    hashing is randomized per process (PYTHONHASHSEED) and would otherwise
    make the texture sheets nondeterministic."""
    acc = 0x9E3779B9
    for p in parts:
        if isinstance(p, str):
            p = zlib.crc32(p.encode("utf-8"))
        acc = ((acc * 1664525) + (p & 0xFFFFFFFF)) & 0xFFFFFFFF
    return acc


def get_rock_tile(colors, cache_key, mask, variant, depth_level):
    """(Surface, y_offset) for a rock tile. Blit at (px, py + y_offset) so
    the tile's 48px footprint aligns with the world grid cell."""
    key = ("rock", cache_key, mask, variant, depth_level)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    rng = random.Random(_seed("rock", mask, variant))
    rock_ramp = colors["rock"]
    cap_ramp = colors["cap"]
    depth = (1.0, 0.7, 0.45, 0.25)[depth_level]
    solid_n = bool(mask & 1)
    solid_e = bool(mask & 2)
    solid_s = bool(mask & 4)
    solid_w = bool(mask & 8)
    exposed = not (solid_n and solid_e and solid_s and solid_w)

    # Low-frequency chunky stone blobs (interior relief).
    patch = {}
    for py in range(-1, 4):
        for px in range(-1, 4):
            patch[(px, py)] = 2 + (1 if rng.random() < 0.55 else 0)

    hmap = [[1] * TILE_VOXELS for _ in range(TILE_VOXELS)]
    meta = [[{} for _ in range(TILE_VOXELS)] for _ in range(TILE_VOXELS)]

    for y in range(TILE_VOXELS):
        cap_band = (not solid_n) and y <= 2
        for x in range(TILE_VOXELS):
            cell = {"cap": False, "spike": False, "tuft": False}
            # distance (in cells) to the nearest exposed SIDE/BOTTOM edge.
            # The north edge is the cap surface (beveled naturally), so it
            # does not pull the height map down.
            d = 99
            if not solid_s:
                d = min(d, TILE_VOXELS - 1 - y)
            if not solid_w:
                d = min(d, x)
            if not solid_e:
                d = min(d, TILE_VOXELS - 1 - x)
            if d > 2:
                d = 2

            if cap_band and d >= 1:
                # exposed top: cap layer with tufts + occasional spikes
                cell["cap"] = True
                cell["tuft"] = rng.random() < 0.3
                h = 2 + (1 if rng.random() < 0.45 else 0)
                if cell["tuft"] and rng.random() < 0.30:
                    h = 4
                    cell["spike"] = True
                if d == 1:
                    h = min(h, 2)
            else:
                h = patch[(x // 4, y // 4)]
                if rng.random() < 0.06:
                    h = 1  # crevice
                if d == 0:
                    h = 1  # exposed edge bevels down
                elif d == 1 and h > 2:
                    h = 2
            meta[y][x] = cell
            hmap[y][x] = max(1, min(ROCK_DESIGN_H, h))

    def color_fn(x, y, h, shade):
        cell = meta[y][x]
        t = max(0.0, min(1.0, 0.30 + shade * 0.85))
        if cell["cap"]:
            t = max(0.0, min(1.0, 0.45 + shade * 0.75))
            if cell["tuft"]:
                t = min(1.0, t + 0.15)
            if cell["spike"]:
                t = min(1.0, t + 0.2)
            c = dither_pick(cap_ramp, t, x * 3 + variant, y * 3)
        else:
            c = dither_pick(rock_ramp, t, x * 3 + variant * 2, y * 3)
        return (
            min(255, int(c[0] * depth)),
            min(255, int(c[1] * depth)),
            min(255, int(c[2] * depth)),
        )

    surf = render_relief(hmap, color_fn, VOXEL_PX)
    max_h = max(max(row) for row in hmap)
    result = (surf, -(max_h - 1) * VOXEL_PX)
    _CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Spring pads
# ---------------------------------------------------------------------------


def _render_fitted(model, voxel_px=3):
    """Render a voxel model and center its silhouette horizontally with the
    top edge flush to the tile's top row; returns a 48x48 SRCALPHA."""
    surf, _ = render_model(model, voxel_px)
    if surf.get_width() < 1 or surf.get_height() < 1:
        return pygame.Surface((TILE_PX, TILE_PX), pygame.SRCALPHA)
    mask = pygame.mask.from_surface(surf)
    rects = mask.get_bounding_rects()
    if not rects:
        return pygame.Surface((TILE_PX, TILE_PX), pygame.SRCALPHA)
    rect = rects[0]
    out = pygame.Surface((TILE_PX, TILE_PX), pygame.SRCALPHA)
    out.blit(surf, (TILE_PX // 2 - rect.centerx, -rect.top))
    return out


def get_spring_frames(colors, cache_key):
    """3 frames of a voxel bounce pad (metal base + coil + glowing pad):
    rest -> compressed -> released, animating a bounce. The pad's top sits
    at the tile's top edge; surfaces 48x48."""
    key = ("spring", cache_key)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    metal_dark = (52, 56, 70)
    metal = (92, 98, 118)
    metal_light = (150, 160, 182)
    coil_dark = (120, 128, 150)
    pad = (60, 235, 120)
    pad_light = (150, 255, 200)

    # pad top z for the three bounce phases
    tops = (9, 6, 12)
    frames = []
    for i, top_z in enumerate(tops):
        model = VoxelModel()
        # chunky metal base
        model.add_box(1, 0, 0, 12, 11, 3, metal_dark)
        model.add_box(1, 1, 3, 12, 9, 1, metal)
        # base rivets
        for rx in (2, 11):
            model.add_voxel(rx, 3, 3, metal_light)
        # coil: zig-zag stacked rings between base and pad
        coil_z_s = 4
        coil_z_e = top_z - 2
        for z in range(coil_z_s, coil_z_e + 1):
            r = 5 if (z % 2 == 0 and (coil_z_e - coil_z_s) > 0) else 4
            model.add_cylinder(7, 5, z, r, 1, coil_dark if (z - coil_z_s) % 2 == 0 else metal)
        # glowing pad
        model.add_box(2, 0, top_z - 2, 10, 10, 1, pad)
        model.add_box(2, 0, top_z - 1, 10, 10, 1, pad_light)
        for rx in (2, 9):
            model.add_voxel(rx, 2, top_z, (200, 255, 240))
        frames.append(_render_fitted(model))
    _CACHE[key] = frames
    return frames


# ---------------------------------------------------------------------------
# One-way platforms
# ---------------------------------------------------------------------------


def get_oneway_tile(colors, cache_key, variant):
    """Thin voxel metal grate/bridge plank occupying the top ~5 voxels of
    the tile, with rivets and support posts; rest transparent."""
    key = ("oneway", cache_key, variant)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    rng = random.Random(_seed("oneway", cache_key, variant))
    metal = (168, 132, 64)
    metal_dark = (104, 82, 42)
    metal_light = (214, 188, 104)
    plank_h = 6  # bottom of the plank slab

    model = VoxelModel()
    # plank slab: 12 wide x 11 deep, occupying the top ~5 voxels of the
    # tile (voxel z 10..14 out of a 16-voxel-tall visual column)
    model.add_box(2, 0, 14, 12, 9, 2, metal)
    model.add_box(2, 0, 12, 12, 9, 2, metal_light)
    model.add_box(2, 0, plank_h, 12, 9, 2, metal_dark)
    # rivets along the top
    for rx in (3, 6, 9, 12):
        model.add_voxel(rx, 3, 15, metal_light)
    # support posts dropping to the bottom of the slab
    model.add_box(3, 3, plank_h - 5, 2, 2, 5, metal_dark)
    model.add_box(9, 3, plank_h - 5, 2, 2, 5, metal_dark)
    surf = _render_fitted(model)
    _CACHE[key] = surf
    return surf


# ---------------------------------------------------------------------------
# Crumble blocks
# ---------------------------------------------------------------------------


def _crumble_heightmap(cache_key, crack_level):
    rng = random.Random(_seed("crumble", cache_key, crack_level))
    chunk = 4 - crack_level  # 4,3,3,2 cells per chunk
    if chunk < 2:
        chunk = 2
    hmap = [[0] * TILE_VOXELS for _ in range(TILE_VOXELS)]
    n = (TILE_VOXELS + chunk - 1) // chunk
    for cy in range(n):
        for cx in range(n):
            ch = 2 + (1 if rng.random() < 0.5 else 0)
            w0 = cx * chunk
            w1 = min(TILE_VOXELS, w0 + chunk)
            h0 = cy * chunk
            h1 = min(TILE_VOXELS, h0 + chunk)
            for y in range(h0, h1):
                for x in range(w0, w1):
                    # jagged chunk silhouette + chipped voxels
                    if rng.random() < 0.08:
                        continue
                    v = ch
                    edge = (x == w0 or x == w1 - 1 or y == h0 or y == h1 - 1)
                    if edge and rng.random() < 0.35:
                        v -= 1
                    hmap[y][x] = max(1, v)
    return hmap


def get_crumble_tile(colors, cache_key, crack_level):
    """Brittle cracked block of visibly separate voxel chunks with dark
    gaps; crack_level 0..3 increasingly fractured."""
    key = ("crumble", cache_key, crack_level)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    rock_ramp = colors["rock"]
    rng = random.Random(_seed("crumble_ck", cache_key, crack_level))
    hmap = _crumble_heightmap(cache_key, crack_level)
    darken = 0.78 - 0.13 * crack_level

    def color_fn(x, y, h, shade):
        t = max(0.0, min(1.0, 0.25 + shade * 0.75))
        c = dither_pick(rock_ramp, t, x * 3, y * 3)
        return (int(c[0] * darken), int(c[1] * darken), int(c[2] * darken))

    surf = render_relief(hmap, color_fn, VOXEL_PX)
    _CACHE[key] = surf
    return surf
