"""
Pure tile-grid collision/physics. This module knows nothing about rooms,
doors, the world grid, JSON, or pygame rendering -- it only operates on:
  - a 2D list-of-lists of tile ids (0 = empty, 1 = solid)
  - an axis-aligned bounding box with a velocity

Keeping this separate from src/data/* and src/engine/world_loader.py is
what lets the map/world be swapped out (new campaign, mod, procedurally
generated dungeon) without touching how movement/collision works, and
vice versa: physics can be tuned without touching map data.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.engine.constants import (
    GRAVITY,
    MAX_FALL_SPEED,
    TILE_EMPTY,
    TILE_SIZE,
    TILE_SOLID,
)


def tile_at(tiles, tile_x: int, tile_y: int) -> int:
    """Out-of-bounds tiles are treated as empty. Every room is expected to
    author a solid 1-tile border around itself (with explicit gaps only
    where a door should let the player pass through), so this is what
    actually contains the player -- not a hardcoded out-of-bounds wall.
    That's what lets a player walk straight through a door gap and out
    past the edge of the tile grid, where game_world.py is watching for
    exactly that to trigger a room transition."""
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


def rect_overlaps_solid(tiles, x: float, y: float, w: float, h: float) -> bool:
    for ty in _tile_range(y, h):
        for tx in _tile_range(x, w):
            if tile_at(tiles, tx, ty) == TILE_SOLID:
                return True
    return False


def _solid_columns(tiles, x: float, y: float, w: float, h: float):
    cols = set()
    for ty in _tile_range(y, h):
        for tx in _tile_range(x, w):
            if tile_at(tiles, tx, ty) == TILE_SOLID:
                cols.add(tx)
    return cols


def _solid_rows(tiles, x: float, y: float, w: float, h: float):
    rows = set()
    for ty in _tile_range(y, h):
        for tx in _tile_range(x, w):
            if tile_at(tiles, tx, ty) == TILE_SOLID:
                rows.add(ty)
    return rows


@dataclass
class AABBBody:
    """A simple axis-aligned physics body: position + size + velocity."""

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


def move_and_collide(body: AABBBody, tiles) -> None:
    """Move body by its velocity, axis by axis, resolving collisions
    against the given tile grid. Standard separated-axis tile collision,
    the same approach used by Super Metroid-style platformers."""

    # Horizontal. Resolve against whichever solid column is actually
    # nearest in the direction of travel (not just the column implied by
    # the naive new position), since out-of-bounds tiles are empty and
    # therefore must never be mistaken for the blocker.
    new_x = body.x + body.vx
    if body.vx != 0:
        cols = _solid_columns(tiles, new_x, body.y, body.width, body.height)
        if cols:
            if body.vx > 0:
                new_x = min(cols) * TILE_SIZE - body.width
            else:
                new_x = (max(cols) + 1) * TILE_SIZE
            body.vx = 0
    body.x = new_x

    # Vertical, same approach using solid rows.
    new_y = body.y + body.vy
    body.on_ground = False
    if body.vy != 0:
        rows = _solid_rows(tiles, body.x, new_y, body.width, body.height)
        if rows:
            if body.vy > 0:
                new_y = min(rows) * TILE_SIZE - body.height
                body.on_ground = True
            else:
                new_y = (max(rows) + 1) * TILE_SIZE
            body.vy = 0
    body.y = new_y
    if body.vy == 0 and not body.on_ground:
        # Horizontal-only frames still need a ground check so jumping
        # stays responsive; re-test a hair below the current position.
        rows = _solid_rows(tiles, body.x, body.y + 0.1, body.width, body.height)
        body.on_ground = bool(rows)
