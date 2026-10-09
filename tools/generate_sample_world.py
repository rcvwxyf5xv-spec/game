"""
Builds the sample/demo world used to prove out the map constraints:

  - world grid is centered on (0, 0)
  - the cell directly to the right of center is (1, 0)
  - an oddly-shaped (L-shaped) playspace spans multiple grid cells, and
    is still composed of individually-addressable 1x1 cells
  - cells are the unit of data storage (one JSON file per cell); whether
    a group of cells forms one loaded playspace is decided purely by
    each cell's edge types (`open` cells merge, `door`/`wall` do not) --
    never by any separately-declared "room" grouping

This script is a *tool*, not part of the runtime engine. It demonstrates
that the per-cell JSON files are just a serialization of plain
dataclasses (src/data/schema.py) -- the same path a future level editor,
mod loader, or procedural generator would use. Run it with:

    python -m tools.generate_sample_world

It (re)writes src/data/world_index.json and src/data/cells/*.json.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.schema import (  # noqa: E402
    EAST,
    EDGE_DOOR,
    EDGE_OPEN,
    EDGE_WALL,
    Cell,
    Edge,
    NORTH,
    SOUTH,
    WEST,
    WorldIndex,
)
from src.data.serialize import save_cell, save_world_index  # noqa: E402
from src.engine.constants import (  # noqa: E402
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    TILE_EMPTY,
    TILE_SOLID,
)

CELLS_DIR = ROOT / "src" / "data" / "cells"
WORLD_INDEX_PATH = ROOT / "src" / "data" / "world_index.json"

CW = CELL_WIDTH_TILES
CH = CELL_HEIGHT_TILES

# The row (in cell-local tile coords) every east/west door/opening sits
# on, and the column every north/south one sits on. Keeping one shared
# constant is what guarantees two adjacent cells' gaps line up physically
# -- the engine doesn't enforce this, so authoring tools/editors should.
DOOR_ROW = CH - 2
SEAM_SPAN = 2


def solid_cell_tiles():
    """A cell that's a closed box: solid 1-tile border, empty inside.
    Individual edges get their border reopened later via carve_gap()
    wherever that edge is declared `open` or `door`."""
    tiles = [[TILE_SOLID for _ in range(CW)] for _ in range(CH)]
    for y in range(1, CH - 1):
        for x in range(1, CW - 1):
            tiles[y][x] = TILE_EMPTY
    return tiles


def carve_gap(tiles, direction: str, span: int = SEAM_SPAN, row: int = DOOR_ROW, full: bool = False):
    """Carve a gap in the specified edge. If full=True, carve the entire
    edge (for seamless `open` edges). Otherwise, carve only a narrow span
    around `row` (for doors, which should be narrow passages)."""
    if direction == EAST:
        if full:
            for y in range(1, CH - 1):
                tiles[y][CW - 1] = TILE_EMPTY
        else:
            for y in range(row - span // 2, row - span // 2 + span):
                tiles[y][CW - 1] = TILE_EMPTY
    elif direction == WEST:
        if full:
            for y in range(1, CH - 1):
                tiles[y][0] = TILE_EMPTY
        else:
            for y in range(row - span // 2, row - span // 2 + span):
                tiles[y][0] = TILE_EMPTY
    elif direction == SOUTH:
        if full:
            for x in range(1, CW - 1):
                tiles[CH - 1][x] = TILE_EMPTY
        else:
            for x in range(row - span // 2, row - span // 2 + span):
                tiles[CH - 1][x] = TILE_EMPTY
    elif direction == NORTH:
        if full:
            for x in range(1, CW - 1):
                tiles[0][x] = TILE_EMPTY
        else:
            for x in range(row - span // 2, row - span // 2 + span):
                tiles[0][x] = TILE_EMPTY


def door_edge(direction: str, door_id: str, target_cell, target_edge: str) -> Edge:
    return Edge(
        direction=direction,
        edge_type=EDGE_DOOR,
        door_id=door_id,
        target_cell=target_cell,
        target_edge=target_edge,
    )


def open_edge(direction: str) -> Edge:
    return Edge(direction=direction, edge_type=EDGE_OPEN)


def build_central_hub() -> Cell:
    tiles = solid_cell_tiles()
    carve_gap(tiles, EAST)
    carve_gap(tiles, WEST)
    return Cell(
        coord=(0, 0),
        tiles=tuple(tuple(row) for row in tiles),
        edges={
            EAST: door_edge(EAST, "hub_to_east", (1, 0), WEST),
            WEST: door_edge(WEST, "hub_to_lroom", (-1, 0), EAST),
        },
        zone="Central Hub",
        entities=({"type": "spawn_point", "tile_x": CW // 2, "tile_y": CH - 2},),
        background_color=(24, 24, 40),
    )


def build_east_room() -> Cell:
    tiles = solid_cell_tiles()
    carve_gap(tiles, WEST)
    return Cell(
        coord=(1, 0),
        tiles=tuple(tuple(row) for row in tiles),
        edges={
            WEST: door_edge(WEST, "east_to_hub", (0, 0), EAST),
        },
        zone="East Room",
        entities=({"type": "item_pickup", "tile_x": CW // 2, "tile_y": CH - 2},),
        background_color=(40, 24, 24),
    )


def build_l_room_cells():
    """
    The L-shaped playspace: three separate 1x1 cell files, (-1,0),
    (-1,1) and (0,1), joined to each other by `open` edges so the
    flood-fill assembler merges them into one playable group at load
    time -- no cell declares "I am part of room X", the L shape is
    purely a consequence of which edges are open vs. walled.
    
    IMPORTANT: for `open` edges, BOTH cells' adjoining borders must be
    carved so the player can walk through seamlessly. Each cell carves
    its own side of the seam.
    """
    # (-1, 0): top-left arm. Open south (to (-1,1)), door east (to hub).
    nw_tiles = solid_cell_tiles()
    carve_gap(nw_tiles, SOUTH, full=True)  # carve entire south border for seamless open edge
    carve_gap(nw_tiles, EAST)              # carve narrow gap for the door
    nw = Cell(
        coord=(-1, 0),
        tiles=tuple(tuple(row) for row in nw_tiles),
        edges={
            SOUTH: open_edge(SOUTH),
            EAST: door_edge(EAST, "lroom_to_hub", (0, 0), WEST),
        },
        zone="L-Shaped Passage",
        background_color=(24, 40, 28),
    )

    # (-1, 1): bottom-left arm (the elbow). Open north (to (-1,0)) and
    # open east (to (0,1)).
    sw_tiles = solid_cell_tiles()
    carve_gap(sw_tiles, NORTH, full=True)  # carve entire north border for seamless open edge
    carve_gap(sw_tiles, EAST, full=True)   # carve entire east border for seamless open edge
    sw = Cell(
        coord=(-1, 1),
        tiles=tuple(tuple(row) for row in sw_tiles),
        edges={
            NORTH: open_edge(NORTH),
            EAST: open_edge(EAST),
        },
        zone="L-Shaped Passage",
        entities=({"type": "item_pickup", "tile_x": CW // 2, "tile_y": CH - 2},),
        background_color=(24, 40, 28),
    )

    # (0, 1): bottom-right arm. Open west (to (-1,1)). Note (0,0) directly
    # above it (the hub) is a different, unconnected cell group -- its
    # north edge here is left as the default wall.
    se_tiles = solid_cell_tiles()
    carve_gap(se_tiles, WEST, full=True)  # carve entire west border for seamless open edge
    se = Cell(
        coord=(0, 1),
        tiles=tuple(tuple(row) for row in se_tiles),
        edges={
            WEST: open_edge(WEST),
        },
        zone="L-Shaped Passage",
        background_color=(24, 40, 28),
    )

    return [nw, sw, se]


def main() -> None:
    CELLS_DIR.mkdir(parents=True, exist_ok=True)

    cells = [build_central_hub(), build_east_room(), *build_l_room_cells()]

    cell_files = {}
    for cell in cells:
        filename = f"{cell.coord[0]}_{cell.coord[1]}.json"
        save_cell(cell, CELLS_DIR / filename)
        cell_files[cell.coord] = filename

    index = WorldIndex(cell_files=cell_files, start_cell=(0, 0))
    save_world_index(index, WORLD_INDEX_PATH)
    print(f"Wrote {len(cell_files)} cell files to {CELLS_DIR} and {WORLD_INDEX_PATH}")


if __name__ == "__main__":
    main()
