"""
Pure tile-grid collision/physics. This module knows nothing about rooms,
doors, the world grid, JSON, or pygame rendering -- it only operates on:
  - a 2D list-of-lists of tile ids
  - an axis-aligned bounding box with a velocity

Tile types:
  0 = empty
  1 = solid (always blocks)
  2 = spring (solid surface; bounces player upward on landing)
  3 = one-way platform (blocks only when falling onto from above)
  4 = crumble block (solid until stepped on, then collapses)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

from src.engine.constants import (
    CRUMBLE_DELAY_FRAMES,
    CRUMBLE_RESPAWN_FRAMES,
    GRAVITY,
    MAX_FALL_SPEED,
    SPRING_BOUNCE_SPEED,
    TILE_CRUMBLE,
    TILE_EMPTY,
    TILE_ONEWAY_PLAT,
    TILE_SIZE,
    TILE_SOLID,
    TILE_SPRING,
)


def tile_at(tiles, tile_x: int, tile_y: int) -> int:
    if tile_y < 0 or tile_y >= len(tiles):
        return TILE_EMPTY
    row = tiles[tile_y]
    if tile_x < 0 or tile_x >= len(row):
        return TILE_EMPTY
    return row[tile_x]


def _tile_range(pos: float, size: float):
    start = int(pos // TILE_SIZE)
    end = int((pos + size - 0.001) // TILE_SIZE)
    return range(start, end + 1)


def _is_blocking_x(tile_id: int) -> bool:
    return tile_id in (TILE_SOLID, TILE_SPRING, TILE_CRUMBLE)


def _is_blocking_y(tile_id: int, vy: float) -> bool:
    if tile_id in (TILE_SOLID, TILE_SPRING, TILE_CRUMBLE):
        return True
    if tile_id == TILE_ONEWAY_PLAT and vy > 0:
        return True
    return False


def rect_overlaps_solid(tiles, x: float, y: float, w: float, h: float) -> bool:
    for ty in _tile_range(y, h):
        for tx in _tile_range(x, w):
            if tile_at(tiles, tx, ty) == TILE_SOLID:
                return True
    return False


def _blocking_columns(tiles, x: float, y: float, w: float, h: float, vx: float):
    cols = set()
    for ty in _tile_range(y, h):
        for tx in _tile_range(x, w):
            if _is_blocking_x(tile_at(tiles, tx, ty)):
                cols.add(tx)
    return cols


def _blocking_rows(tiles, x: float, y: float, w: float, h: float, vy: float):
    rows = set()
    for ty in _tile_range(y, h):
        for tx in _tile_range(x, w):
            if _is_blocking_y(tile_at(tiles, tx, ty), vy):
                rows.add(ty)
    return rows


@dataclass
class CrumbleTracker:
    active: Dict[Tuple[int, int], int] = field(default_factory=dict)
    collapsed: Dict[Tuple[int, int], int] = field(default_factory=dict)

    def step_on(self, tx: int, ty: int):
        key = (tx, ty)
        if key not in self.active and key not in self.collapsed:
            self.active[key] = CRUMBLE_DELAY_FRAMES

    def update(self, tiles):
        remove = []
        for key, frames in self.active.items():
            frames -= 1
            if frames <= 0:
                tx, ty = key
                if 0 <= ty < len(tiles) and 0 <= tx < len(tiles[ty]):
                    tiles[ty][tx] = TILE_EMPTY
                self.collapsed[key] = CRUMBLE_RESPAWN_FRAMES
                remove.append(key)
            else:
                self.active[key] = frames
        for k in remove:
            del self.active[k]

        remove2 = []
        for key, frames in self.collapsed.items():
            frames -= 1
            if frames <= 0:
                tx, ty = key
                if 0 <= ty < len(tiles) and 0 <= tx < len(tiles[ty]):
                    tiles[ty][tx] = TILE_CRUMBLE
                remove2.append(key)
            else:
                self.collapsed[key] = frames
        for k in remove2:
            del self.collapsed[k]


@dataclass
class AABBBody:
    x: float
    y: float
    width: float
    height: float
    vx: float = 0.0
    vy: float = 0.0
    on_ground: bool = False

    def rect(self):
        return self.x, self.y, self.width, self.height


def apply_gravity(body: AABBBody) -> None:
    body.vy = min(body.vy + GRAVITY, MAX_FALL_SPEED)


def _tiles_beneath(tiles, body: AABBBody):
    foot_y = int((body.y + body.height) // TILE_SIZE)
    result = []
    for tx in _tile_range(body.x, body.width):
        result.append((tx, foot_y))
    return result


def move_and_collide(body: AABBBody, tiles, crumble: CrumbleTracker = None) -> None:
    new_x = body.x + body.vx
    if body.vx != 0:
        cols = _blocking_columns(tiles, new_x, body.y, body.width, body.height, body.vx)
        if cols:
            if body.vx > 0:
                new_x = min(cols) * TILE_SIZE - body.width
            else:
                new_x = (max(cols) + 1) * TILE_SIZE
            body.vx = 0
    body.x = new_x

    new_y = body.y + body.vy
    body.on_ground = False
    if body.vy != 0:
        rows = _blocking_rows(tiles, body.x, new_y, body.width, body.height, body.vy)
        if rows:
            if body.vy > 0:
                new_y = min(rows) * TILE_SIZE - body.height
                body.on_ground = True
            else:
                new_y = (max(rows) + 1) * TILE_SIZE
            body.vy = 0
    body.y = new_y
    if body.vy == 0 and not body.on_ground:
        rows = _blocking_rows(tiles, body.x, body.y + 0.1, body.width, body.height, 1.0)
        body.on_ground = bool(rows)

    if body.on_ground:
        foot_tiles = _tiles_beneath(tiles, body)
        for tx, ty in foot_tiles:
            t = tile_at(tiles, tx, ty)
            if t == TILE_SPRING:
                body.vy = SPRING_BOUNCE_SPEED
                body.on_ground = False
                break
            if t == TILE_CRUMBLE and crumble is not None:
                crumble.step_on(tx, ty)
