"""
Data-only description of the game world.

This module intentionally contains NO pygame, NO physics, NO rendering,
and NO loading/caching/flood-fill logic. It is the "database" layer:
plain dataclasses that can be constructed from JSON (see
src/data/cells/*.json and world_index.json), produced by a level editor,
or generated procedurally by a future module.

World addressing:
    The world is an infinite integer grid of "cells". The cell at (0, 0)
    is the center of the map. The cell directly to the right of it is
    (1, 0). The cell above it is (0, -1) (y increases downward, matching
    screen/array conventions used by the renderer).

Cells, not rooms, are the unit of data:
    Every 1x1 world grid cell is its own self-contained record: its own
    tile grid, plus a type for each of its four edges (wall / open /
    door). There is no separately-declared "this cell belongs to room X"
    fact anywhere in this module -- whether a group of cells forms one
    contiguous playspace is an EMERGENT property of which edges are
    `open` vs `wall`/`door`, computed at load time by
    engine.room_assembler, never authored directly. This is deliberate:
    it removes an entire class of bug (declared grouping disagreeing
    with actual wall/door placement) and it means a mod/DLC/procedural
    generator can add or change a single cell file without having to
    find and edit some other "room" record to keep it consistent.

    An optional `zone` string on a Cell is purely cosmetic metadata (map
    label, music cue, ambience) -- it has no bearing on what gets loaded
    together. Loading groups are decided only by edge types.

Tiles carry their own data:
    Each tile in a Cell is a Tile (see below): a collision `kind` (what
    the physics should treat it as) plus a `palette` label (which named
    texture set it should be drawn with, resolved by the renderer). A
    palette of "" means "use the room's default palette".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

GridCoord = Tuple[int, int]

EDGE_WALL = "wall"
EDGE_OPEN = "open"
EDGE_DOOR = "door"

NORTH = "north"
SOUTH = "south"
EAST = "east"
WEST = "west"
ALL_DIRECTIONS = (NORTH, SOUTH, EAST, WEST)

DIRECTION_DELTA = {
    NORTH: (0, -1),
    SOUTH: (0, 1),
    EAST: (1, 0),
    WEST: (-1, 0),
}

OPPOSITE_DIRECTION = {
    NORTH: SOUTH,
    SOUTH: NORTH,
    EAST: WEST,
    WEST: EAST,
}

TILE_KIND_EMPTY = "empty"
TILE_KIND_SOLID = "solid"
TILE_KIND_SPRING = "spring"
TILE_KIND_ONEWAY = "oneway"
TILE_KIND_CRUMBLE = "crumble"
TILE_KINDS = (
    TILE_KIND_EMPTY,
    TILE_KIND_SOLID,
    TILE_KIND_SPRING,
    TILE_KIND_ONEWAY,
    TILE_KIND_CRUMBLE,
)
# legacy integer ids used by the old cell files (0..4, same order as above)
LEGACY_TILE_IDS = {
    0: TILE_KIND_EMPTY,
    1: TILE_KIND_SOLID,
    2: TILE_KIND_SPRING,
    3: TILE_KIND_ONEWAY,
    4: TILE_KIND_CRUMBLE,
}


@dataclass(frozen=True)
class Tile:
    """One map tile: what it does (kind -> collision behaviour) and what it
    looks like (palette -> named texture set, resolved by the renderer).
    An empty palette string means "use the room's default palette"."""

    kind: str = TILE_KIND_EMPTY
    palette: str = ""


EMPTY_TILE = Tile()


def tile_from_legacy_id(tile_id: int, palette: str = "") -> Tile:
    """Convert an old-style integer tile id (0..4, see LEGACY_TILE_IDS)
    into a Tile."""
    try:
        kind = LEGACY_TILE_IDS[tile_id]
    except KeyError:
        raise ValueError(f"Unknown legacy tile id: {tile_id!r}") from None
    return Tile(kind=kind, palette=palette)


@dataclass(frozen=True)
class Edge:
    """
    Describes what's on one side (north/south/east/west) of a single cell.

    - EDGE_WALL: solid boundary, no connection to the neighbor at all
      (the tile grid should also be solid along that side).
    - EDGE_OPEN: no wall -- this cell and its neighbor in that direction
      are part of the same contiguous playspace. The flood-fill assembler
      (engine.room_assembler) follows these to merge cells together.
      The tile grid must have a matching gap for this to be walkable.
    - EDGE_DOOR: an explicit, named transition to a specific cell/edge.
      Crossing it triggers a reload (new flood-fill from the target),
      not a seamless merge, even though the tiles have a walkable gap
      here too. This is the hook for locked doors, one-way doors, etc.
      in the future.
    """

    direction: str
    edge_type: str = EDGE_WALL
    door_id: str = ""
    target_cell: Optional[GridCoord] = None
    target_edge: str = ""


@dataclass(frozen=True)
class Cell:
    """One 1x1 world grid square: its own tiles, its own four edges."""

    coord: GridCoord
    # Tile grid local to this cell only, CELL_WIDTH_TILES x CELL_HEIGHT_TILES.
    tiles: Tuple[Tuple[Tile, ...], ...]
    edges: Dict[str, Edge] = field(default_factory=dict)
    # Cosmetic-only grouping label (map name, music track, etc). Never
    # consulted by the loader/assembler.
    zone: str = ""
    entities: Tuple[dict, ...] = field(default_factory=tuple)
    background_color: Tuple[int, int, int] = (18, 18, 28)

    def edge(self, direction: str) -> Edge:
        return self.edges.get(direction, Edge(direction=direction, edge_type=EDGE_WALL))


@dataclass(frozen=True)
class WorldIndex:
    """
    Lightweight map of the entire world: which file holds which cell's
    data. This -- not any per-room concept -- is the only thing that must
    stay in memory for the whole game; individual Cell payloads are
    loaded on demand by engine.world_loader, keeping memory bounded no
    matter how large the authored world becomes.
    """

    # Maps every authored (x, y) cell to the filename (relative to
    # src/data/cells/) that contains its definition. A cell coordinate
    # with no entry simply does not exist (e.g. solid rock / void).
    cell_files: Dict[GridCoord, str]
    start_cell: GridCoord
