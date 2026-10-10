from __future__ import annotations

import pygame

LOGICAL = 16

_ONE_WAY_SEAMS = {0: (4, 11), 1: (2, 8, 13), 2: (6,), 3: (3, 10)}


def _empty_grid():
    return [[None] * LOGICAL for _ in range(LOGICAL)]


def _to_surface(grid):
    surf = pygame.Surface((LOGICAL, LOGICAL), pygame.SRCALPHA)
    for y, row in enumerate(grid):
        for x, color in enumerate(row):
            if color is not None:
                surf.set_at((x, y), color)
    return surf


def _up_scale(tile, tile_px):
    return pygame.transform.scale(tile, (tile_px, tile_px))


def _variant_sheet(count, tile_px, draw):
    sheet = pygame.Surface((count * tile_px, tile_px), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for index in range(count):
        sheet.blit(_up_scale(draw(index), tile_px), (index * tile_px, 0))
    return sheet


def _solid_grid(colors, mask):
    rock = colors["rock"]
    cap = colors["cap"]
    grid = [[cap[5] if y % 8 == 0 else cap[6]
             for _ in range(LOGICAL)] for y in range(LOGICAL)]
    if not mask & 4:
        grid[12] = [cap[4]] * LOGICAL
        grid[13] = [cap[2]] * LOGICAL
        grid[14] = [cap[1]] * LOGICAL
        grid[15] = [rock[0]] * LOGICAL
    if not mask & 8:
        for y in range(LOGICAL):
            grid[y][0] = rock[0]
            grid[y][1] = cap[7]
    if not mask & 2:
        for y in range(LOGICAL):
            grid[y][14] = cap[1]
            grid[y][15] = rock[0]
    if not mask & 1:
        grid[0] = [rock[0]] * LOGICAL
        grid[1] = [cap[7]] * LOGICAL
        grid[2] = [cap[4]] * LOGICAL
        grid[3] = [cap[2]] * LOGICAL
        grid[4] = [cap[4]] * LOGICAL
        if not mask & 8:
            _close_box(grid, cap, rock)
        if not mask & 2:
            _zoom_box(grid, cap, rock)
    return grid


def _close_box(grid, cap, rock):
    for y in range(1, 6):
        grid[y][0] = rock[0]
        grid[y][5] = rock[0]
    for x in range(6):
        grid[1][x] = rock[0]
        grid[5][x] = rock[0]
    for y in range(2, 5):
        for x in range(1, 5):
            grid[y][x] = cap[7]
    for x, y in ((1, 2), (2, 3), (3, 4), (4, 2), (2, 4), (3, 3)):
        grid[y][x] = rock[0]


def _zoom_box(grid, cap, rock):
    for y in range(1, 6):
        grid[y][10] = rock[0]
        grid[y][15] = rock[0]
    for x in range(10, 16):
        grid[1][x] = rock[0]
        grid[5][x] = rock[0]
    for y in range(2, 5):
        for x in range(11, 15):
            grid[y][x] = cap[7]
    for x in range(11, 15):
        grid[3][x] = rock[0]
    grid[4][12] = rock[0]
    grid[4][13] = rock[0]


def _oneway_grid(colors, variant):
    rock = colors["rock"]
    cap = colors["cap"]
    grid = _empty_grid()
    for x in range(LOGICAL):
        grid[0][x] = rock[0]
        grid[1][x] = cap[7]
        grid[2][x] = cap[4]
        grid[3][x] = cap[2]
        grid[4][x] = cap[4]
    seams = _ONE_WAY_SEAMS[variant % len(_ONE_WAY_SEAMS)]
    for x in range(LOGICAL):
        grid[5][x] = cap[1] if x in seams else cap[0]
    return grid


def _spring_grid(colors, frame):
    rock = colors["rock"]
    cap = colors["cap"]
    accent = colors["accent"]
    pressed = frame == 1
    glow = frame == 2
    grid = _empty_grid()
    for x in range(LOGICAL):
        grid[0][x] = rock[0]
        grid[15][x] = rock[0]
    for y in range(LOGICAL):
        grid[y][0] = rock[0]
        grid[y][15] = rock[0]
    edge_light = cap[1] if pressed else cap[7]
    edge_dark = cap[6] if pressed else cap[1]
    for x in range(1, 15):
        grid[1][x] = edge_light
        grid[14][x] = edge_dark
    for y in range(1, 15):
        grid[y][1] = edge_light
        grid[y][14] = edge_dark
    face = cap[5] if pressed else cap[6]
    for y in range(2, 14):
        for x in range(2, 14):
            grid[y][x] = face
    if glow:
        for y in range(2, 14):
            grid[y][2] = cap[7]
    shift = 1 if pressed else 0
    arrow = rock[4] if pressed else accent
    top = 3 + shift
    for x in range(5, 11):
        grid[top][x] = arrow
    widths = ((top + 1, 6, 10), (top + 2, 5, 11), (top + 3, 4, 12),
              (top + 4, 4, 12), (top + 5, 4, 12))
    for y, lo, hi in widths:
        for x in range(lo, hi):
            grid[y][x] = arrow
    if glow:
        grid[top + 1][7] = cap[7]
        grid[top + 1][8] = cap[7]
    return grid


_CRACKS = {
    1: [[(2, 8), (3, 7), (4, 7), (5, 6), (6, 6), (7, 5), (8, 5)]],
    2: [[(2, 8), (3, 7), (4, 7), (5, 6), (6, 6), (7, 5), (8, 5),
         (9, 5), (10, 6), (10, 7), (10, 8)],
        [(13, 4), (12, 5), (12, 6), (11, 7), (11, 8), (10, 8)]],
    3: [[(2, 8), (3, 7), (4, 7), (5, 6), (6, 6), (7, 5), (8, 5),
         (9, 5), (10, 6), (10, 7), (10, 8)],
        [(13, 4), (12, 5), (12, 6), (11, 7), (11, 8), (10, 8)],
        [(6, 11), (7, 10), (7, 9), (8, 8), (9, 8)]],
}

_CHUNKS = {
    1: [(1, 13, 2, 14)],
    2: [(1, 13, 2, 14), (12, 3, 13, 4), (14, 0, 15, 1)],
    3: [(1, 13, 2, 14), (12, 3, 13, 4), (14, 0, 15, 1),
        (5, 5, 6, 7), (10, 4, 11, 5), (12, 10, 13, 11), (3, 6, 4, 7)],
}


def _crumble_grid(colors, stage):
    rock = colors["rock"]
    cap = colors["cap"]
    grid = [[cap[6]] * LOGICAL for _ in range(LOGICAL)]
    for x in range(LOGICAL):
        grid[15][x] = rock[0]
    for y in range(LOGICAL):
        grid[y][0] = rock[0]
        grid[y][15] = rock[0]
    for x in range(11):
        grid[0][x] = rock[0]
    for y in range(3):
        for x in range(11 + y, LOGICAL):
            grid[y][x] = cap[2]
    for x, y in ((12, 0), (13, 1), (14, 2)):
        grid[y][x] = cap[0]
    for x in range(3, 10):
        grid[9][x] = cap[3]
        grid[11][x] = cap[3]
        grid[13][x] = cap[3]
    for crack in _CRACKS.get(stage, ()):
        for x, y in crack:
            grid[y][x] = rock[0]
    for x0, y0, x1, y1 in _CHUNKS.get(stage, ()):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                grid[y][x] = None
    return grid


def build_sheet(pal, colors, kind, spec):
    tile_px = int(pal.get("tile_px", 48))
    if kind == "solid":
        variants = int(spec.get("variants", 4))
        frame_h = tile_px + int(spec.get("overhang_px", 0))
        sheet = pygame.Surface((variants * tile_px, 16 * frame_h),
                               pygame.SRCALPHA)
        sheet.fill((0, 0, 0, 0))
        for mask in range(16):
            tile = _up_scale(_to_surface(_solid_grid(colors, mask)), tile_px)
            for variant in range(variants):
                sheet.blit(tile, (variant * tile_px, mask * frame_h))
        return sheet
    if kind == "oneway":
        variants = int(spec.get("variants", 4))
        return _variant_sheet(variants, tile_px,
                              lambda v: _to_surface(_oneway_grid(colors, v)))
    if kind == "spring":
        frames = int(spec.get("frames", 3))
        return _variant_sheet(frames, tile_px,
                              lambda f: _to_surface(_spring_grid(colors, f)))
    stages = int(spec.get("stages", 4))
    return _variant_sheet(stages, tile_px,
                          lambda s: _to_surface(_crumble_grid(colors, s)))
