"""
Flood-fill assembly: turns one or more Cells (src/data/schema.Cell) into a
single stitched playspace the physics/rendering code can treat exactly
like the old monolithic "Room" did.

This is the one place where "which cells count as one loaded room" is
decided, and it is decided purely from edge data (EDGE_OPEN merges,
EDGE_DOOR/EDGE_WALL do not) -- never from any authored room name or
grouping label. That's the whole point of the cell-based data model:
room membership is computed, not declared.

Nothing here knows about JSON or caching (that's world_loader.py's job)
or about pygame/physics (those only ever see the AssembledRoom this
module produces).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from src.data.schema import (
    ALL_DIRECTIONS,
    Cell,
    DIRECTION_DELTA,
    EDGE_DOOR,
    EDGE_OPEN,
    GridCoord,
)
from src.engine.constants import (
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    MAX_ASSEMBLED_CELLS,
    TILE_SIZE,
    TILE_SOLID,
)

CELL_WIDTH_PX = CELL_WIDTH_TILES * TILE_SIZE
CELL_HEIGHT_PX = CELL_HEIGHT_TILES * TILE_SIZE

CellFetcher = Callable[[GridCoord], Optional[Cell]]


@dataclass(frozen=True)
class DoorCrossing:
    """
    A door, anchored to the specific member cell it belongs to (not to
    the assembled room's overall bounding box). Transition detection in
    engine.game_world checks against `cell_rect`, so a door on a cell
    that sits on a concave/internal part of an oddly-shaped room's
    bounding box (like the L-room's notch) still works correctly.
    """

    direction: str
    cell_rect: Tuple[float, float, float, float]  # x, y, w, h, room-local px
    door_id: str
    target_cell: GridCoord
    target_edge: str


@dataclass
class AssembledRoom:
    """Runtime-only stitched playspace. Never persisted; rebuilt on demand
    by assemble() each time the player enters a new cell group."""

    cells: List[GridCoord]
    origin: GridCoord  # min (x, y) among `cells` -- top-left of the bbox
    width: int  # tiles
    height: int  # tiles
    tiles: List[List[int]]
    door_crossings: List[DoorCrossing] = field(default_factory=list)
    entities: List[dict] = field(default_factory=list)
    background_color: Tuple[int, int, int] = (18, 18, 28)
    zone: str = ""
    truncated: bool = False  # True if MAX_ASSEMBLED_CELLS cut the flood fill short


def assemble(start_coord: GridCoord, fetch_cell: CellFetcher) -> AssembledRoom:
    """
    Flood-fills outward from `start_coord` through EDGE_OPEN edges only,
    capped at MAX_ASSEMBLED_CELLS, and stitches the resulting cells into
    one AssembledRoom. `fetch_cell` is supplied by world_loader so this
    function never touches the filesystem/cache directly.
    """

    start_cell = fetch_cell(start_coord)
    if start_cell is None:
        raise KeyError(f"No cell authored at {start_coord}")

    members: Dict[GridCoord, Cell] = {start_coord: start_cell}
    queue: List[GridCoord] = [start_coord]
    truncated = False

    while queue:
        coord = queue.pop(0)
        cell = members[coord]
        for direction in ALL_DIRECTIONS:
            edge = cell.edge(direction)
            if edge.edge_type != EDGE_OPEN:
                continue
            dx, dy = DIRECTION_DELTA[direction]
            neighbor_coord = (coord[0] + dx, coord[1] + dy)
            if neighbor_coord in members:
                continue
            if len(members) >= MAX_ASSEMBLED_CELLS:
                truncated = True
                continue
            neighbor_cell = fetch_cell(neighbor_coord)
            if neighbor_cell is None:
                continue
            members[neighbor_coord] = neighbor_cell
            queue.append(neighbor_coord)

    xs = [c[0] for c in members]
    ys = [c[1] for c in members]
    origin = (min(xs), min(ys))
    bbox_cells_w = max(xs) - min(xs) + 1
    bbox_cells_h = max(ys) - min(ys) + 1
    width = bbox_cells_w * CELL_WIDTH_TILES
    height = bbox_cells_h * CELL_HEIGHT_TILES

    tiles = [[TILE_SOLID for _ in range(width)] for _ in range(height)]
    entities: List[dict] = []
    door_crossings: List[DoorCrossing] = []

    for coord, cell in members.items():
        local_cx = coord[0] - origin[0]
        local_cy = coord[1] - origin[1]
        tile_off_x = local_cx * CELL_WIDTH_TILES
        tile_off_y = local_cy * CELL_HEIGHT_TILES

        for ty, row in enumerate(cell.tiles):
            for tx, tile in enumerate(row):
                tiles[tile_off_y + ty][tile_off_x + tx] = tile

        for entity in cell.entities:
            moved = dict(entity)
            moved["tile_x"] = entity["tile_x"] + tile_off_x
            moved["tile_y"] = entity["tile_y"] + tile_off_y
            entities.append(moved)

        cell_rect = (
            tile_off_x * TILE_SIZE,
            tile_off_y * TILE_SIZE,
            CELL_WIDTH_PX,
            CELL_HEIGHT_PX,
        )
        for direction in ALL_DIRECTIONS:
            edge = cell.edge(direction)
            if edge.edge_type != EDGE_DOOR:
                continue
            door_crossings.append(
                DoorCrossing(
                    direction=direction,
                    cell_rect=cell_rect,
                    door_id=edge.door_id,
                    target_cell=edge.target_cell,
                    target_edge=edge.target_edge,
                )
            )

    anchor = members[start_coord]
    return AssembledRoom(
        cells=list(members.keys()),
        origin=origin,
        width=width,
        height=height,
        tiles=tiles,
        door_crossings=door_crossings,
        entities=entities,
        background_color=anchor.background_color,
        zone=anchor.zone,
        truncated=truncated,
    )


def room_origin_px(room: AssembledRoom) -> Tuple[float, float]:
    """World-pixel position of this room's top-left corner. Since every
    room's tile grid is stitched from the same global cell grid, two
    rooms' pixel rects line up consistently in this shared space -- that
    is what lets the presentation layer pan a camera across both of them
    during a door transition instead of just cutting between them."""
    ox, oy = room.origin
    return ox * CELL_WIDTH_PX, oy * CELL_HEIGHT_PX


def local_tile_offset(room: AssembledRoom, coord: GridCoord) -> Tuple[int, int]:
    """Tile offset (within `room`'s stitched grid) of the given member cell."""
    return (
        (coord[0] - room.origin[0]) * CELL_WIDTH_TILES,
        (coord[1] - room.origin[1]) * CELL_HEIGHT_TILES,
    )
