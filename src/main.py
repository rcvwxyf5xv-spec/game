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
    TILE_CRUMBLE,
    TILE_ONEWAY_PLAT,
    TILE_SIZE,
    TILE_SOLID,
    TILE_SPRING,
)
from src.engine.game_world import PLAYER_H, PLAYER_W, GameWorld  # noqa: E402
from src.engine.room_assembler import AssembledRoom, room_origin_px  # noqa: E402
from src.engine.world_loader import WorldLoader  # noqa: E402

SCALE = 3
WINDOW_W = CELL_WIDTH_TILES * TILE_SIZE * SCALE
WINDOW_H = CELL_HEIGHT_TILES * TILE_SIZE * SCALE

WORLD_INDEX_PATH = ROOT / "src" / "data" / "world_index.json"
CELLS_DIR = ROOT / "src" / "data" / "cells"

SOLID_COLOR = (90, 90, 110)
SPRING_COLOR = (60, 240, 120)
ONEWAY_COLOR = (180, 140, 60)
CRUMBLE_COLOR = (160, 100, 100)
GRID_LINE_COLOR = (255, 255, 255)
PLAYER_COLOR = (240, 210, 60)

TILE_COLORS = {
    TILE_SOLID: SOLID_COLOR,
    TILE_SPRING: SPRING_COLOR,
    TILE_ONEWAY_PLAT: ONEWAY_COLOR,
    TILE_CRUMBLE: CRUMBLE_COLOR,
}


def camera_for_point(room: AssembledRoom, local_cx: float, local_cy: float):
    """World-space camera (top-left, in px) centered on a point that is
    `room`-local, clamped to that room's bounds. Returning WORLD-space
    (not room-local) is what lets a transition slide the camera across
    two different rooms' tile grids using one consistent coordinate
    space -- every room's grid lines up because they all come from the
    same global cell grid (see room_origin_px)."""
    room_ox, room_oy = room_origin_px(room)
    room_w_px = room.width * TILE_SIZE
    room_h_px = room.height * TILE_SIZE

    view_w = WINDOW_W / SCALE
    view_h = WINDOW_H / SCALE

    cam_x = local_cx - view_w / 2
    cam_y = local_cy - view_h / 2
    cam_x = max(0.0, min(cam_x, max(0.0, room_w_px - view_w)))
    cam_y = max(0.0, min(cam_y, max(0.0, room_h_px - view_h)))
    return room_ox + cam_x, room_oy + cam_y


def compute_camera(world: GameWorld):
    center_x = world.body.x + world.body.width / 2
    center_y = world.body.y + world.body.height / 2
    return camera_for_point(world.current_room, center_x, center_y)


def draw_room_tiles(surface, room: AssembledRoom, cam_x: float, cam_y: float) -> None:
    """Draws one room's tiles + grid overlay at a WORLD-space camera
    position. Safe to call more than once per frame (e.g. once per room
    visible during a transition slide) since tiles outside the window
    are skipped and nothing here touches `surface` outside its own
    room's footprint other than the tile/grid pixels themselves."""
    room_ox, room_oy = room_origin_px(room)

    for ty, row in enumerate(room.tiles):
        for tx, tile in enumerate(row):
            color = TILE_COLORS.get(tile)
            if color is None:
                continue
            px = (room_ox + tx * TILE_SIZE - cam_x) * SCALE
            py = (room_oy + ty * TILE_SIZE - cam_y) * SCALE
            size = TILE_SIZE * SCALE
            if px + size < 0 or py + size < 0 or px > WINDOW_W or py > WINDOW_H:
                continue
            if tile == TILE_ONEWAY_PLAT:
                pygame.draw.rect(surface, color, (px, py, size, size * 0.25))
            elif tile == TILE_SPRING:
                pygame.draw.rect(surface, color, (px, py + size * 0.5, size, size * 0.5))
                pygame.draw.polygon(surface, (80, 255, 150), [
                    (px + size * 0.5, py + size * 0.15),
                    (px + size * 0.2, py + size * 0.5),
                    (px + size * 0.8, py + size * 0.5),
                ])
            else:
                pygame.draw.rect(surface, color, (px, py, size, size))

    # Overlay dashed grid lines on multiples of one world cell, proving
    # this room is built from 1x1 grid squares even when it spans several.
    cell_w_px = CELL_WIDTH_TILES * TILE_SIZE
    cell_h_px = CELL_HEIGHT_TILES * TILE_SIZE
    for gx in range(0, room.width * TILE_SIZE + 1, cell_w_px):
        x = (room_ox + gx - cam_x) * SCALE
        if 0 <= x <= WINDOW_W:
            draw_dashed_line(surface, (x, 0), (x, WINDOW_H), GRID_LINE_COLOR)
    for gy in range(0, room.height * TILE_SIZE + 1, cell_h_px):
        y = (room_oy + gy - cam_y) * SCALE
        if 0 <= y <= WINDOW_H:
            draw_dashed_line(surface, (0, y), (WINDOW_W, y), GRID_LINE_COLOR)


def draw_player(surface, world_x: float, world_y: float, cam_x: float, cam_y: float,
                 width: float, height: float) -> None:
    """Player placeholder: a simple polygon (pentagon "ship" shape), at an
    explicit WORLD-space position so it can be drawn mid-transition too."""
    bx = (world_x - cam_x) * SCALE
    by = (world_y - cam_y) * SCALE
    bw = width * SCALE
    bh = height * SCALE
    points = [
        (bx + bw / 2, by),
        (bx + bw, by + bh * 0.4),
        (bx + bw * 0.75, by + bh),
        (bx + bw * 0.25, by + bh),
        (bx, by + bh * 0.4),
    ]
    pygame.draw.polygon(surface, PLAYER_COLOR, points)


def draw_room(surface, world: GameWorld, cam_x: float, cam_y: float) -> None:
    room = world.current_room
    surface.fill(room.background_color)
    draw_room_tiles(surface, room, cam_x, cam_y)
    room_ox, room_oy = room_origin_px(room)
    draw_player(
        surface,
        world.body.x + room_ox,
        world.body.y + room_oy,
        cam_x, cam_y,
        world.body.width, world.body.height,
    )


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


TRANSITION_HOLD_FRAMES = 10  # brief freeze on the old room before panning
TRANSITION_SLIDE_FRAMES = 20  # camera pan duration
TRANSITION_SETTLE_FRAMES = 6  # brief freeze on the new room before resuming


def _ease_in_out(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


class RoomTransition:
    """Presentation-only Super Metroid-style door transition: freezes
    gameplay and pans the camera from the old room's view to the new
    room's view, across the shared world-pixel grid both rooms sit in.
    Built entirely from the TransitionEvent GameWorld already hands back
    -- it does not reach into physics/world-loading itself."""

    def __init__(self, event) -> None:
        self.from_room = event.from_room
        self.to_room = event.to_room
        from_ox, from_oy = room_origin_px(self.from_room)
        to_ox, to_oy = room_origin_px(self.to_room)
        self.from_world_pos = (event.from_pos[0] + from_ox, event.from_pos[1] + from_oy)
        self.to_world_pos = (event.to_pos[0] + to_ox, event.to_pos[1] + to_oy)
        self.from_cam = camera_for_point(
            self.from_room, self.from_world_pos[0] - from_ox + PLAYER_W / 2,
            self.from_world_pos[1] - from_oy + PLAYER_H / 2,
        )
        self.to_cam = camera_for_point(
            self.to_room, self.to_world_pos[0] - to_ox + PLAYER_W / 2,
            self.to_world_pos[1] - to_oy + PLAYER_H / 2,
        )
        self.frame = 0
        self.total = TRANSITION_HOLD_FRAMES + TRANSITION_SLIDE_FRAMES + TRANSITION_SETTLE_FRAMES

    def done(self) -> bool:
        return self.frame >= self.total

    def advance(self) -> None:
        self.frame += 1

    def draw(self, surface) -> None:
        if self.frame < TRANSITION_HOLD_FRAMES:
            cam_x, cam_y = self.from_cam
            player_pos = self.from_world_pos
        elif self.frame < TRANSITION_HOLD_FRAMES + TRANSITION_SLIDE_FRAMES:
            t = (self.frame - TRANSITION_HOLD_FRAMES) / TRANSITION_SLIDE_FRAMES
            eased = _ease_in_out(t)
            cam_x = self.from_cam[0] + (self.to_cam[0] - self.from_cam[0]) * eased
            cam_y = self.from_cam[1] + (self.to_cam[1] - self.from_cam[1]) * eased
            player_pos = (
                self.from_world_pos[0] + (self.to_world_pos[0] - self.from_world_pos[0]) * eased,
                self.from_world_pos[1] + (self.to_world_pos[1] - self.from_world_pos[1]) * eased,
            )
        else:
            cam_x, cam_y = self.to_cam
            player_pos = self.to_world_pos

        surface.fill((0, 0, 0))
        draw_room_tiles(surface, self.from_room, cam_x, cam_y)
        draw_room_tiles(surface, self.to_room, cam_x, cam_y)
        draw_player(surface, player_pos[0], player_pos[1], cam_x, cam_y, PLAYER_W, PLAYER_H)


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

    loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=32)
    world = GameWorld(loader)
    transition: RoomTransition | None = None

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_w):
                    if transition is None:
                        world.jump(JUMP_SPEED)

        if transition is None:
            keys = pygame.key.get_pressed()
            direction = 0
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                direction -= 1
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                direction += 1
            world.set_horizontal_input(direction, MOVE_SPEED)

            world.update()
            if world.last_transition is not None:
                t = world.last_transition
                print(f"[transition] {t.from_cell} -> {t.to_cell} (went {t.direction})")
                transition = RoomTransition(t)
                world.last_transition = None

        if transition is not None:
            transition.draw(screen)
            transition.advance()
            if transition.done():
                transition = None
        else:
            cam_x, cam_y = compute_camera(world)
            draw_room(screen, world, cam_x, cam_y)
            draw_hud(screen, font, world, loader)

        pygame.display.flip()
        clock.tick(FPS)

    pygame.quit()


if __name__ == "__main__":
    main()
