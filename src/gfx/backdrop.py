"""
Parallax backdrop layers: seamlessly-tileable voxel cave rock, rendered
once per zone with render_relief and heavily blended toward the zone's
fog color grid so they sit far behind the tile layer.
"""

from __future__ import annotations

import random

import pygame

from src.gfx.palette import dither_pick, get_palette, ramp_color
from src.gfx.voxel import render_relief

LAYER_W = 384
LAYER_H = 480

_CACHE = {}


def _value_noise(rng, lx, ly, cell, seed):
    """Torus-wrapped value noise: random lattice values every `cell`
    cells, bilinear-smoothed. Returns a generator over (nx, ny, v)."""
    nx = (lx + cell - 1) // cell
    ny = (ly + cell - 1) // cell
    vals = {}
    rr = random.Random(seed)
    for gy in range(ny + 1):
        for gx in range(nx + 1):
            vals[(gx % nx, gy % ny)] = rr.random()
    for y in range(ly):
        for x in range(lx):
            gx = x // cell
            gy = y // cell
            fx = (x % cell) / float(cell)
            fy = (y % cell) / float(cell)
            v00 = vals[(gx % nx, gy % ny)]
            v10 = vals[((gx + 1) % nx, gy % ny)]
            v01 = vals[(gx % nx, (gy + 1) % ny)]
            v11 = vals[((gx + 1) % nx, (gy + 1) % ny)]
            a = v00 + (v10 - v00) * fx
            b = v01 + (v11 - v01) * fx
            v = a + (b - a) * fy
            yield x, y, v


def _cave_heightmap(zone, voxel_px, seed, motif=None):
    """Torus heightmap for a backdrop layer. Heights 1..4 (voxels)."""
    rng = random.Random(seed)
    lx = LAYER_W // voxel_px
    ly = LAYER_H // voxel_px
    hmap = [[1] * lx for _ in range(ly)]
    # broad cave masses
    for x, y, v in _value_noise(rng, lx, ly, 20, seed):
        v = v * v
        h = 1 + int(v * 2.4)
        hmap[y][x] = h
    # carved-ish variation at smaller scale
    for x, y, v in _value_noise(rng, lx, ly, 6, seed + 1):
        if v < 0.3:
            hmap[y][x] = max(1, hmap[y][x] - 1)
        elif v > 0.72 and hmap[y][x] < 4:
            hmap[y][x] += 1
    # occasional tall stalactite / pillar silhouettes
    for _ in range(lx * ly // 120):
        sx = rng.randrange(lx)
        sy = rng.randrange(ly)
        radius = rng.randint(3, 6)
        for dy in range(-radius * 2, radius + 1):
            for dx in range(-radius, radius + 1):
                xx = (sx + dx) % lx
                yy = (sy + max(0, dy)) % ly
                d = (dx * dx + dy * dy) / float(radius * radius)
                if d > 1:
                    continue
                add = int((1.0 - d) * (7 if dy <= 0 else 2))
                hmap[yy][xx] = max(hmap[yy][xx], min(4, 1 + add))
    if motif:
        _apply_motif(hmap, motif, rng, seed)
    return hmap, lx, ly


def _apply_motif(hmap, motif, rng, seed):
    lx = len(hmap[0])
    ly = len(hmap)
    if motif == "crystals":
        for x, y, v in _value_noise(rng, lx, ly, 8, seed + 9):
            if v > 0.82:
                hmap[y][x] = min(4, hmap[y][x] + 2)
    elif motif == "vines":
        for _ in range(lx * ly // 60):
            vx = rng.randrange(lx)
            yy = rng.randrange(ly)
            for k in range(rng.randint(8, 18)):
                yy = (yy + 1) % ly
                hmap[yy][vx] = min(4, hmap[yy][vx] + 2)
    elif motif == "bulbs":
        for x, y, v in _value_noise(rng, lx, ly, 10, seed + 9):
            if v > 0.78:
                hmap[y][x] = min(4, hmap[y][x] + 3)
    elif motif == "magma":
        # winding bright crack lines
        for _ in range(lx * ly // 220):
            cx = rng.randrange(lx)
            cy = rng.randrange(ly)
            for _ in range(rng.randint(20, 40)):
                cx = (cx + rng.choice((-1, 0, 1))) % lx
                cy = (cy + rng.choice((-1, 0, 1))) % ly
                if rng.random() < 0.6:
                    hmap[cy][cx] = max(2, hmap[cy][cx])
    elif motif == "girders":
        # metal girder lattice: brighten pillar/beam lines
        for x in range(0, lx, rng.randint(9, 14)):
            for y in range(ly):
                hmap[y][x] = min(4, hmap[y][x] + 1)
        yb = rng.randrange(ly)
        for x in range(lx):
            hmap[yb][x] = min(4, hmap[yb][x] + 2)


def _make_layer(zone, voxel_px, seed, fog_blend, motif=None, brighten=0.0):
    pal = get_palette(zone)
    rock_ramp = pal["rock"]
    cap_ramp = pal["cap"]
    accent = pal["accent"]
    hmap, lx, ly = _cave_heightmap(zone, voxel_px, seed, motif)

    def color_fn(x, y, h, shade):
        t = max(0.0, min(1.0, 0.25 + shade * 0.8))
        c = dither_pick(rock_ramp, t, x, y)
        # zone motif accents
        if motif == "crystals" and h >= 4:
            c = dither_pick(cap_ramp, min(1.0, t + 0.4), x, y)
        elif motif == "magma" and h >= 3:
            c = accent
        elif motif == "bulbs" and h >= 4:
            c = tuple(min(255, int(v * 1.25)) for v in accent)
        # blend toward the fog so far layers sit behind the scene
        if fog_blend > 0:
            fog = pal["fog_bottom"]
            c = tuple(int(c[i] * (1.0 - fog_blend) + fog[i] * fog_blend) for i in range(3))
        if brighten > 0:
            c = tuple(min(255, int(v * (1.0 + brighten))) for v in c)
        return c

    surf = render_relief(hmap, color_fn, voxel_px)
    # crop the extra overhang rows so the surface is exactly LAYER_W x
    # LAYER_H (it tiles in x perfectly thanks to the torus, and mostly
    # seamlessly in y after crop).
    max_h = max(max(row) for row in hmap)
    extra = (max_h - 1) * voxel_px
    if extra > 0 and surf.get_height() > LAYER_H:
        crop = pygame.Surface((surf.get_width(), surf.get_height() - extra), pygame.SRCALPHA)
        crop.blit(surf, (0, 0))
        surf = crop
    if surf.get_width() != LAYER_W or surf.get_height() != LAYER_H:
        surf = pygame.transform.scale(surf, (LAYER_W, LAYER_H))
    return surf


def get_backdrop_layers(zone):
    """list of (Surface, parallax_factor): far foggy cave silhouettes then
    a nearer, slightly brighter formation layer with the zone's motif."""
    key = ("layers", zone)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    motifs = {
        "Blue Brinstar": "crystals",
        "Green Brinstar": "vines",
        "Pink Brinstar": "bulbs",
        "Red Brinstar": "magma",
        "Kraid's Lair": "girders",
    }
    far = _make_layer(zone, 6, hash(("far", zone)) & 0xFFFF, fog_blend=0.72, brighten=0.0)
    near = _make_layer(zone, 4, hash(("near", zone)) & 0xFFFF,
                       fog_blend=0.35, motif=motifs.get(zone), brighten=0.18)
    result = [(far, 0.25), (near, 0.5)]
    _CACHE[key] = result
    return result


def get_fog_gradient(zone):
    """768x480 vertical gradient from the zone's top to bottom fog color."""
    key = ("fog", zone)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    pal = get_palette(zone)
    top = pal["fog_top"]
    bottom = pal["fog_bottom"]
    surf = pygame.Surface((LAYER_W * 2, LAYER_H))
    for y in range(LAYER_H):
        t = y / max(1, LAYER_H - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        surf.fill(color, (0, y, LAYER_W * 2, 1))
    _CACHE[key] = surf
    return surf
