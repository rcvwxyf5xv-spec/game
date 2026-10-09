"""
Entry point / demo harness.

This file is the ONLY place that imports pygame directly for gameplay
purposes. It wires the data layer (WorldLoader) to the physics/transition
layer (GameWorld) and draws everything with plain polygons as placeholder
graphics, as requested -- no sprite assets at all.

Run with:  python -m src.main
Controls:  left/right arrows or A/D to move, space/up to jump, Esc to quit.

What this demo is meant to prove:
  1. The world is addressed by an integer grid centered on (0, 0); the
     cell to the right of center is (1, 0) (see HUD "world cell").
  2. A playspace can be a large/odd shape spanning multiple grid cells
     (the green L-Shaped Passage), assembled at runtime purely because
     its cells' shared edges are `open` -- while still being internally
     built from 1x1 grid squares (the dashed grid lines overlaid on it).
  3. Only a bounded number of Cell objects are ever resident in memory
     at once (see HUD "loaded cells" + console eviction messages) even
     though the on-disk world could be arbitrarily large.
  4. Physics (src/engine/physics.py) and map data (src/data/*) never
     import each other -- game_world.py and room_assembler.py are the
     only glue.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pygame

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engine.constants import (  # noqa: E402
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    FPS,
    JUMP_SPEED,
    MOVE_SPEED,
    TILE_SIZE,
    TILE_SOLID,
)
from src.engine.game_world import GameWorld  # noqa: E402
from src.engine.world_loader import WorldLoader  # noqa: E402

SCALE = 3
WINDOW_W = CELL_WIDTH_TILES * TILE_SIZE * SCALE
WINDOW_H = CELL_HEIGHT_TILES * TILE_SIZE * SCALE

WORLD_INDEX_PATH = ROOT / "src" / "data" / "world_index.json"
CELLS_DIR = ROOT / "src" / "data" / "cells"

SOLID_COLOR = (90, 90, 110)
GRID_LINE_COLOR = (255, 255, 255)
PLAYER_COLOR = (240, 210, 60)


def compute_camera(world: GameWorld):
    room = world.current_room
    room_w_px = room.width * TILE_SIZE
    room_h_px = room.height * TILE_SIZE

    view_w = WINDOW_W / SCALE
    view_h = WINDOW_H / SCALE

    center_x = world.body.x + world.body.width / 2
    center_y = world.body.y + world.body.height / 2

    cam_x = center_x - view_w / 2
    cam_y = center_y - view_h / 2

    cam_x = max(0.0, min(cam_x, max(0.0, room_w_px - view_w)))
    cam_y = max(0.0, min(cam_y, max(0.0, room_h_px - view_h)))
    return cam_x, cam_y


def draw_room(surface, world: GameWorld, cam_x: float, cam_y: float) -> None:
    room = world.current_room
    surface.fill(room.background_color)

    for ty, row in enumerate(room.tiles):
        for tx, tile in enumerate(row):
            if tile != TILE_SOLID:
                continue
            px = (tx * TILE_SIZE - cam_x) * SCALE
            py = (ty * TILE_SIZE - cam_y) * SCALE
            size = TILE_SIZE * SCALE
            if px + size < 0 or py + size < 0 or px > WINDOW_W or py > WINDOW_H:
                continue
            rect_points = [
                (px, py),
                (px + size, py),
                (px + size, py + size),
                (px, py + size),
            ]
            pygame.draw.polygon(surface, SOLID_COLOR, rect_points)

    # Overlay dashed grid lines on multiples of one world cell, proving
    # this room is built from 1x1 grid squares even when it spans several.
    cell_w_px = CELL_WIDTH_TILES * TILE_SIZE
    cell_h_px = CELL_HEIGHT_TILES * TILE_SIZE
    for gx in range(0, room.width * TILE_SIZE + 1, cell_w_px):
        x = (gx - cam_x) * SCALE
        if 0 <= x <= WINDOW_W:
            draw_dashed_line(surface, (x, 0), (x, WINDOW_H), GRID_LINE_COLOR)
    for gy in range(0, room.height * TILE_SIZE + 1, cell_h_px):
        y = (gy - cam_y) * SCALE
        if 0 <= y <= WINDOW_H:
            draw_dashed_line(surface, (0, y), (WINDOW_W, y), GRID_LINE_COLOR)

    # Player placeholder: a simple polygon (pentagon "ship" shape).
    bx = (world.body.x - cam_x) * SCALE
    by = (world.body.y - cam_y) * SCALE
    bw = world.body.width * SCALE
    bh = world.body.height * SCALE
    points = [
        (bx + bw / 2, by),
        (bx + bw, by + bh * 0.4),
        (bx + bw * 0.75, by + bh),
        (bx + bw * 0.25, by + bh),
        (bx, by + bh * 0.4),
    ]
    pygame.draw.polygon(surface, PLAYER_COLOR, points)


def draw_dashed_line(surface, start, end, color, dash_len=6):
    x1, y1 = start
    x2, y2 = end
    length = max(abs(x2 - x1), abs(y2 - y1))
    if length == 0:
        return
    steps = int(length // dash_len)
    for i in range(0, steps, 2):
        t0 = i / steps
        t1 = min((i + 1) / steps, 1.0)
        p0 = (x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0)
        p1 = (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)
        pygame.draw.line(surface, color, p0, p1, 1)


def draw_hud(surface, font, world: GameWorld, loader: WorldLoader) -> None:
    cell = world.current_world_cell()
    lines = [
        f"zone: {world.current_room.zone or '(unnamed)'}",
        f"world cell under player: {cell}",
        f"assembled playspace cells: {sorted(world.current_room.cells)}"
        + (" [capped]" if world.current_room.truncated else ""),
        f"loaded cells in memory: {sorted(loader.loaded_cell_coords())}  (cache size {loader.cache_size})",
    ]
    for i, line in enumerate(lines):
        text = font.render(line, True, (255, 255, 255))
        shadow = font.render(line, True, (0, 0, 0))
        surface.blit(shadow, (9, 9 + i * 16))
        surface.blit(text, (8, 8 + i * 16))


def main() -> None:
    pygame.init()
    pygame.display.set_caption("Metroidvania Map Demo")
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("monospace", 13)

    loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=4)
    world = GameWorld(loader)

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        keys = pygame.key.get_pressed()
        direction = 0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            direction -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            direction += 1
        world.set_horizontal_input(direction, MOVE_SPEED)
        if keys[pygame.K_SPACE] or keys[pygame.K_UP] or keys[pygame.K_w]:
            world.jump(JUMP_SPEED)

        world.update()
        if world.last_transition is not None:
            t = world.last_transition
            print(f"[transition] {t.from_cell} -> {t.to_cell} (went {t.direction})")
            world.last_transition = None

        cam_x, cam_y = compute_camera(world)
        draw_room(screen, world, cam_x, cam_y)
        draw_hud(screen, font, world, loader)

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
