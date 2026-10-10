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
from src.gfx.pixelart import render_pixel_art
from src.gfx.voxel import render_relief

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


def _tile_frame(rows, colors, heights=None, edge_pad=0):
    """Render a 16x16 art grid and crop any raised overhang so the tile
    footprint fills 48x48 (bottom-aligned), returning an SRCALPHA surface.

    edge_pad > 0 prepends/extends the edge-column pattern by one column on
    each side so the tile's outer columns get no boundary bevel and tiles
    join seamlessly horizontally."""
    if edge_pad > 0:
        rows = [row[-1] + row + row[0] for row in rows]
    surf = render_pixel_art(rows, colors, heights, VOXEL_PX)
    w, h = surf.get_size()
    out = pygame.Surface((TILE_PX, TILE_PX), pygame.SRCALPHA)
    out.blit(surf, (0, 0), area=(edge_pad * VOXEL_PX, h - TILE_PX,
                                 min(w, TILE_PX), TILE_PX))
    return out


_SPRING_METAL = {
    "M": (150, 160, 182),
    "m": (92, 98, 118),
    "d": (52, 56, 70),
}


def _spring_coil_rows(phase):
    """9 rows x 8 cols of the coil band (rows 3-11) as a zig-zag braid;
    `phase` shifts the pattern so the highlight travels down the spring."""
    band = []
    for r in range(9):
        seg = (r + phase) % 8
        cells = ["c"] * 8
        if seg < 4:
            lo = 4 - seg
            hi = 7 - seg
        else:
            k = seg - 4
            lo = k
            hi = k + 3
        for x in range(max(0, lo), min(8, hi + 1)):
            cells[x] = "C"
        band.append("".join(cells))
    return band


def _spring_grids():
    """16x16 grids for the 3 spring frames (pad fixed at rows 0-2; the
    coil highlight and pad glow animate). Returns (rows, colors) pairs."""
    pad_tops = (
        (150, 255, 200),
        (222, 255, 246),
        (134, 240, 178),
    )
    out = []
    for f in range(3):
        coil = _spring_coil_rows((0, 3, 5)[f])
        rows = []
        for r in range(16):
            if r == 0:
                row = "PPPPPPPPPPPPPPPP"
            elif r == 1:
                row = "pppppppppppppppp"
            elif r == 2:
                row = "qqqqqqqqqqqqqqqq"
            elif 3 <= r <= 11:
                row = "dMdd" + coil[r - 3] + "ddMd"
            elif r == 12:
                row = "rRrRRRRrRRRRrRRR"
            elif 13 <= r <= 14:
                row = "mmmmmmmmmmmmmmmm"
            else:
                row = "dddddddddddddddd"
            rows.append(row)
        colors = dict(_SPRING_METAL)
        colors["P"] = pad_tops[f]
        colors["p"] = (60, 235, 120) if f != 1 else (98, 246, 154)
        colors["q"] = (30, 150, 80)
        colors["C"] = (185, 198, 226)
        colors["c"] = (118, 128, 152)
        colors["R"] = (96, 104, 124)
        colors["r"] = (200, 210, 230)
        out.append((rows, colors))
    return out


def get_spring_frames(colors, cache_key):
    """3 frames of a side-view bounce block: bright pad on the tile's top
    rows 0-2 (always at the very top), zig-zag coil in the middle, dark
    metal base plate with rivets. Frames animate the coil highlight and
    pad glow, not the pad position. Surfaces 48x48."""
    key = ("spring", cache_key)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    frames = []
    for rows, frame_colors in _spring_grids():
        heights = {"r": 2}
        frames.append(_tile_frame(rows, frame_colors, heights))
    _CACHE[key] = frames
    return frames


# ---------------------------------------------------------------------------
# One-way platforms
# ---------------------------------------------------------------------------

_ONEWAY_SEAMS = {
    0: (4, 11),
    1: (2, 8, 13),
    2: (6,),
    3: (3, 10),
}

_ONEWAY_RIVETS = ((0, 2), (1, 14), (1, 2), (0, 14))


def get_oneway_tile(colors, cache_key, variant):
    """Thin side-view ledge plank occupying the tile's top rows 0-4 with
    a dark underside, seams and small edge brackets/rivets below (rows
    5-7). The plank spans columns 0-15 so adjacent tiles join into a
    continuous ledge. Only the seam/rivet placement varies per variant;
    rest of the tile transparent. Surface 48x48."""
    key = ("oneway", cache_key, variant)
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    rock = colors["rock"]
    cap = colors["cap"]
    seam_cols = _ONEWAY_SEAMS[variant % len(_ONEWAY_SEAMS)]
    rv = _ONEWAY_RIVETS[variant % len(_ONEWAY_RIVETS)]

    rows = []
    # plank top surface + body (rows 0-4), seams break the plank uniformly
    rows.append("T" * 16)
    for y in (1, 2, 3):
        row = []
        for x in range(16):
            row.append("E" if x in seam_cols else ("p" if y != 2 else "m"))
        rows.append("".join(row))
    rows.append("U" * 16)
    # edge brackets (rows 5-7) at columns 0-2 and 13-15
    bracket = ("dRd", "ddd", ".dd") if rv[0] == 0 else ("d.d", "ddd", ".dd")
    for y in range(3):
        rows.append(bracket[y] + "." * 10 + bracket[y][::-1])
    # everything below is air
    for _ in range(8):
        rows.append("." * 16)

    shade = {
        "T": cap[6],
        "p": rock[5],
        "m": rock[4],
        "E": rock[2],
        "U": cap[0],
        "d": rock[2],
        "R": rock[6],
    }
    colors_map = {ch: shade[ch] for ch in set("".join(rows)) if ch != "."}
    # edge_pad=1 continues the edge columns so the plank tiles seamlessly
    # horizontally (no boundary bevels at the tile edges)
    surf = _tile_frame(rows, colors_map, edge_pad=1)
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
