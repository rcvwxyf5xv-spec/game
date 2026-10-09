"""
Generates a Brinstar-complexity metroidvania world: ~55 distinct rooms across
5 themed sub-zones, connected via doors in a hub-and-spoke topology.

Rooms are separated by DOORS. Multi-cell rooms use OPEN edges internally.
This is the opposite of the previous (reverted) attempt which made everything
one giant open playspace.

Run with:  python -m tools.generate_brinstar_world
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.schema import (
    ALL_DIRECTIONS,
    DIRECTION_DELTA,
    EAST,
    EDGE_DOOR,
    EDGE_OPEN,
    EDGE_WALL,
    Cell,
    Edge,
    GridCoord,
    NORTH,
    OPPOSITE_DIRECTION,
    SOUTH,
    WEST,
    WorldIndex,
)
from src.data.serialize import save_cell, save_world_index
from src.engine.constants import (
    CELL_HEIGHT_TILES as CH,
    CELL_WIDTH_TILES as CW,
    TILE_CRUMBLE,
    TILE_EMPTY,
    TILE_ONEWAY_PLAT,
    TILE_SOLID,
    TILE_SPRING,
)

CELLS_DIR = ROOT / "src" / "data" / "cells"
WORLD_INDEX_PATH = ROOT / "src" / "data" / "world_index.json"

DOOR_ROW = CH - 3
DOOR_COL = CW // 2
SEAM_SPAN = 2

# ---------------------------------------------------------------------------
# Edge helpers — ALL doors go through add_door_pair to guarantee symmetry
# ---------------------------------------------------------------------------

all_edges: Dict[GridCoord, Dict[str, Edge]] = {}


def _ensure(coord: GridCoord):
    if coord not in all_edges:
        all_edges[coord] = {}


def add_open_pair(a: GridCoord, direction: str, b: GridCoord):
    opp = OPPOSITE_DIRECTION[direction]
    _ensure(a)
    _ensure(b)
    all_edges[a][direction] = Edge(direction=direction, edge_type=EDGE_OPEN)
    all_edges[b][opp] = Edge(direction=opp, edge_type=EDGE_OPEN)


def add_door_pair(a: GridCoord, direction: str, b: GridCoord, door_id: str):
    opp = OPPOSITE_DIRECTION[direction]
    dx, dy = DIRECTION_DELTA[direction]
    expected = (a[0] + dx, a[1] + dy)
    assert expected == b, f"Cells not adjacent: {a} {direction} -> {b} (expected {expected})"
    _ensure(a)
    _ensure(b)
    all_edges[a][direction] = Edge(
        direction=direction, edge_type=EDGE_DOOR,
        door_id=door_id, target_cell=b, target_edge=opp,
    )
    all_edges[b][opp] = Edge(
        direction=opp, edge_type=EDGE_DOOR,
        door_id=door_id, target_cell=a, target_edge=direction,
    )


# ---------------------------------------------------------------------------
# Tile generation helpers
# ---------------------------------------------------------------------------

def solid_cell_tiles():
    tiles = [[TILE_SOLID for _ in range(CW)] for _ in range(CH)]
    for y in range(1, CH - 1):
        for x in range(1, CW - 1):
            tiles[y][x] = TILE_EMPTY
    return tiles


def carve_gap(tiles, direction: str, full: bool = False):
    if direction == EAST:
        if full:
            for y in range(1, CH - 2):
                tiles[y][CW - 1] = TILE_EMPTY
        else:
            for y in range(DOOR_ROW - SEAM_SPAN // 2, DOOR_ROW - SEAM_SPAN // 2 + SEAM_SPAN):
                tiles[y][CW - 1] = TILE_EMPTY
    elif direction == WEST:
        if full:
            for y in range(1, CH - 2):
                tiles[y][0] = TILE_EMPTY
        else:
            for y in range(DOOR_ROW - SEAM_SPAN // 2, DOOR_ROW - SEAM_SPAN // 2 + SEAM_SPAN):
                tiles[y][0] = TILE_EMPTY
    elif direction == SOUTH:
        if full:
            for x in range(1, CW - 1):
                tiles[CH - 1][x] = TILE_EMPTY
        else:
            for x in range(DOOR_COL - SEAM_SPAN // 2, DOOR_COL - SEAM_SPAN // 2 + SEAM_SPAN):
                tiles[CH - 1][x] = TILE_EMPTY
    elif direction == NORTH:
        if full:
            for x in range(1, CW - 1):
                tiles[0][x] = TILE_EMPTY
        else:
            for x in range(DOOR_COL - SEAM_SPAN // 2, DOOR_COL - SEAM_SPAN // 2 + SEAM_SPAN):
                tiles[0][x] = TILE_EMPTY


def add_floor(tiles, y, x_start=1, x_end=CW - 1):
    for x in range(x_start, x_end):
        tiles[y][x] = TILE_SOLID


def add_platform(tiles, y, x_start, x_end, tile_type=TILE_SOLID):
    for x in range(x_start, x_end):
        tiles[y][x] = tile_type


# ---------------------------------------------------------------------------
# Room definitions: each returns a list of (coord, tiles, entities) tuples
# ---------------------------------------------------------------------------

RoomCellData = Tuple[GridCoord, List[List[int]], tuple]

# We'll track all cells and their zone/bg info
cell_registry: Dict[GridCoord, dict] = {}


def register_room(cells_data: List[RoomCellData], zone: str, bg_color: tuple):
    for coord, tiles, entities in cells_data:
        assert coord not in cell_registry, f"Cell {coord} already registered!"
        cell_registry[coord] = {
            "tiles": tiles, "zone": zone, "bg_color": bg_color, "entities": entities,
        }


# === BLUE BRINSTAR (starting area) ===
# Cells roughly at x=0..3, y=0

def build_blue_brinstar():
    zone = "Blue Brinstar"
    bg = (20, 30, 70)

    # Room 1: Landing Site — 3x1 corridor at (0,0), (1,0), (2,0)
    cells = []
    for i, cx in enumerate([(0, 0), (1, 0), (2, 0)]):
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        if i == 0:
            entities = ({"type": "spawn_point", "tile_x": CW // 2, "tile_y": CH - 3},)
        else:
            entities = ()
        cells.append((cx, t, entities))
    add_open_pair((0, 0), EAST, (1, 0))
    add_open_pair((1, 0), EAST, (2, 0))
    register_room(cells, zone, bg)

    # Room 2: First Corridor — 2x1 at (3,0), (4,0)
    cells = []
    for cx in [(3, 0), (4, 0)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 5, 3, 7, TILE_ONEWAY_PLAT)
        cells.append((cx, t, ()))
    add_open_pair((3, 0), EAST, (4, 0))
    add_door_pair((2, 0), EAST, (3, 0), "d_landing_to_corridor1")
    register_room(cells, zone, bg)

    # Room 3: Missile Chamber — 1x1 at (5,0) dead-end
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "item_missile", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((4, 0), EAST, (5, 0), "d_corridor1_to_missile")
    register_room([((5, 0), t, entities)], zone, bg)

    # Room 4: Save Station Alpha — 1x1 at (0,-1)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "save_station", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((0, 0), NORTH, (0, -1), "d_landing_to_save_alpha")
    register_room([((0, -1), t, entities)], zone, bg)

    # Room 5: Morph Ball Tunnel — 2x1 at (1,-1), (2,-1)
    cells = []
    for cx in [(1, -1), (2, -1)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        for x in range(1, CW - 1):
            t[1][x] = TILE_SOLID
            t[2][x] = TILE_SOLID
            t[3][x] = TILE_SOLID
        cells.append((cx, t, ()))
    add_open_pair((1, -1), EAST, (2, -1))
    add_door_pair((1, 0), NORTH, (1, -1), "d_landing_to_morphtunnel")
    register_room(cells, zone, bg)

    # Room 6: Blue Shaft — 1x2 at (4,-1), (4,0) already taken, use (4,1)
    # Actually let's put it vertically: (4,-1) above first corridor
    t1 = solid_cell_tiles()
    add_platform(t1, CH - 3, 2, 6, TILE_SOLID)
    add_platform(t1, CH - 6, 8, 13, TILE_SOLID)
    t2_coord = (4, -1)
    add_door_pair((4, 0), NORTH, t2_coord, "d_corridor1_to_blueshaft")
    register_room([(t2_coord, t1, ())], zone, (25, 35, 80))


# === GREEN BRINSTAR (main hub) ===
# Main shaft at x=4, extending vertically from y=1 to y=5

def build_green_brinstar():
    zone = "Green Brinstar"
    bg = (20, 50, 25)

    # Room 7: Main Shaft (Hub) — 1x5 at (4,1)..(4,5)
    shaft_cells = []
    for i, cy in enumerate(range(1, 6)):
        coord = (4, cy)
        t = solid_cell_tiles()
        add_platform(t, CH - 3, 2, 7, TILE_SOLID)
        add_platform(t, CH - 6, 9, 14, TILE_ONEWAY_PLAT)
        if i == 0:
            add_platform(t, 3, 3, 8, TILE_SOLID)
        shaft_cells.append((coord, t, ()))
    for cy in range(1, 5):
        add_open_pair((4, cy), SOUTH, (4, cy + 1))
    add_door_pair((4, 0), SOUTH, (4, 1), "d_corridor1_to_mainshaft")
    register_room(shaft_cells, zone, bg)

    # Room 8: Map Station — 1x1 at (3,1)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "map_station", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((4, 1), WEST, (3, 1), "d_shaft_to_mapstation")
    register_room([((3, 1), t, entities)], zone, bg)

    # Room 9: Save Station Beta — 1x1 at (5,1)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "save_station", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((4, 1), EAST, (5, 1), "d_shaft_to_save_beta")
    register_room([((5, 1), t, entities)], zone, bg)

    # Room 10: Upper Corridor — 3x1 at (5,2),(6,2),(7,2)
    cells = []
    for cx in [(5, 2), (6, 2), (7, 2)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 5, 4, 10, TILE_ONEWAY_PLAT)
        cells.append((cx, t, ()))
    add_open_pair((5, 2), EAST, (6, 2))
    add_open_pair((6, 2), EAST, (7, 2))
    add_door_pair((4, 2), EAST, (5, 2), "d_shaft_to_upper_corridor")
    register_room(cells, zone, bg)

    # Room 11: Fireflea Room — 2x1 at (5,3),(6,3)
    cells = []
    for cx in [(5, 3), (6, 3)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 5, 2, 5, TILE_CRUMBLE)
        add_platform(t, CH - 5, 8, 12, TILE_CRUMBLE)
        cells.append((cx, t, ()))
    add_open_pair((5, 3), EAST, (6, 3))
    add_door_pair((4, 3), EAST, (5, 3), "d_shaft_to_fireflea")
    register_room(cells, zone, bg)

    # Room 12: Green Corridor East — 2x1 at (5,4),(6,4) -> to Red Brinstar
    cells = []
    for cx in [(5, 4), (6, 4)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        cells.append((cx, t, ()))
    add_open_pair((5, 4), EAST, (6, 4))
    add_door_pair((4, 4), EAST, (5, 4), "d_shaft_to_green_east")
    register_room(cells, zone, bg)

    # Room 13: Green Corridor West — 2x1 at (2,2),(3,2) -> to Pink Brinstar
    cells = []
    for cx in [(2, 2), (3, 2)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 5, 3, 8, TILE_ONEWAY_PLAT)
        cells.append((cx, t, ()))
    add_open_pair((2, 2), EAST, (3, 2))
    add_door_pair((4, 2), WEST, (3, 2), "d_shaft_to_green_west")
    register_room(cells, zone, bg)

    # Room 14: Etecoon Chamber — 2x1 at (2,3),(3,3) spring teaching room
    cells = []
    for cx in [(2, 3), (3, 3)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        cells.append((cx, t, ()))
    t_etecoon = cells[0][1]
    t_etecoon[CH - 3][CW // 2] = TILE_SPRING
    t_etecoon[CH - 3][CW // 2 + 1] = TILE_SPRING
    add_platform(cells[1][1], 3, 4, 12, TILE_SOLID)
    add_open_pair((2, 3), EAST, (3, 3))
    add_door_pair((4, 3), WEST, (3, 3), "d_shaft_to_etecoon")
    register_room(cells, zone, bg)

    # Room 15: Reserve Tank — 1x1 at (3,4)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "item_reserve_tank", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((4, 4), WEST, (3, 4), "d_shaft_to_reserve")
    register_room([((3, 4), t, entities)], zone, bg)

    # Room 16: Crumble Bridge — 2x1 at (5,5),(6,5)
    cells = []
    for cx in [(5, 5), (6, 5)]:
        t = solid_cell_tiles()
        add_platform(t, CH - 4, 1, CW - 1, TILE_CRUMBLE)
        cells.append((cx, t, ()))
    add_open_pair((5, 5), EAST, (6, 5))
    add_door_pair((4, 5), EAST, (5, 5), "d_shaft_to_crumblebridge")
    add_door_pair((6, 5), EAST, (7, 5), "d_crumblebridge_to_redtower")
    register_room(cells, zone, bg)

    # Room 17: Elevator North — 1x1 at (4,-2) connects Blue -> Green
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    add_door_pair((4, -1), SOUTH, (4, -2), "d_blueshaft_to_elevator_n")
    # Note: (4,-2) is actually below (4,-1) in screen coords... 
    # Actually y increases downward, so SOUTH = +y. Let me reconsider.
    # (4,-1) SOUTH -> (4,0) which is already taken. Let me place this differently.
    # Elevator north should connect the blue shaft to green shaft top.
    # Blue shaft is at (4,-1), Main shaft starts at (4,1). We need (4,0) to bridge.
    # (4,0) is part of First Corridor. The door d_corridor1_to_mainshaft connects (4,0) S -> (4,1).
    # So the shaft top is already connected. Skip this room.
    # Unregister the door we just made — actually, we can't easily do that.
    # Let's just not build this room; the connectivity already works via (4,0).
    # Remove the bad door:
    if SOUTH in all_edges.get((4, -1), {}):
        del all_edges[(4, -1)][SOUTH]
    if NORTH in all_edges.get((4, -2), {}):
        del all_edges[(4, -2)][NORTH]

    # Room 18: Elevator South — 1x1 at (4,6) connects Green -> Kraid's
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    add_door_pair((4, 5), SOUTH, (4, 6), "d_shaft_to_elevator_south")
    register_room([((4, 6), t, ())], zone, (30, 55, 30))

    # Room 19: Green Junction — 2x1 at (2,4),(3,4) - wait (3,4) taken by Reserve Tank
    # Use (2,4) as standalone
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    add_platform(t, CH - 5, 3, 8, TILE_SOLID)
    add_door_pair((2, 3), SOUTH, (2, 4), "d_etecoon_to_junction")
    register_room([((2, 4), t, ())], zone, bg)


# === PINK BRINSTAR ===

def build_pink_brinstar():
    zone = "Pink Brinstar"
    bg = (55, 22, 40)

    # Room 20: Dachora's Room — 2x2 at (0,2),(1,2),(0,3),(1,3)
    cells = []
    for cy in [2, 3]:
        for cx in [0, 1]:
            t = solid_cell_tiles()
            if cy == 3:
                add_floor(t, CH - 2)
            else:
                add_platform(t, CH - 4, 3, 10, TILE_ONEWAY_PLAT)
            cells.append(((cx, cy), t, ()))
    add_open_pair((0, 2), EAST, (1, 2))
    add_open_pair((0, 3), EAST, (1, 3))
    add_open_pair((0, 2), SOUTH, (0, 3))
    add_open_pair((1, 2), SOUTH, (1, 3))
    add_door_pair((2, 2), WEST, (1, 2), "d_greenwest_to_dachora")
    register_room(cells, zone, bg)

    # Room 21: Big Pink Shaft — 1x4 at (-1,2)..(-1,5)
    shaft_cells = []
    for cy in range(2, 6):
        coord = (-1, cy)
        t = solid_cell_tiles()
        if cy % 2 == 0:
            add_platform(t, CH - 4, 2, 7, TILE_SOLID)
            add_platform(t, CH - 7, 9, 14, TILE_ONEWAY_PLAT)
        else:
            add_platform(t, CH - 4, 9, 14, TILE_SOLID)
            add_platform(t, CH - 7, 2, 7, TILE_ONEWAY_PLAT)
        shaft_cells.append((coord, t, ()))
    for cy in range(2, 5):
        add_open_pair((-1, cy), SOUTH, (-1, cy + 1))
    add_door_pair((0, 2), WEST, (-1, 2), "d_dachora_to_pinkshaft")
    register_room(shaft_cells, zone, bg)

    # Room 22: Spore Spawn Antechamber — 2x1 at (-2,3),(-1,3) — wait (-1,3) in shaft
    # Use (-2,3),(-3,3) instead
    cells = []
    for cx in [(-2, 3), (-3, 3)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 5, 4, 10, TILE_CRUMBLE)
        cells.append((cx, t, ()))
    add_open_pair((-2, 3), WEST, (-3, 3))
    add_door_pair((-1, 3), WEST, (-2, 3), "d_pinkshaft_to_sporespawn_ante")
    register_room(cells, zone, bg)

    # Room 23: Spore Spawn Arena — 2x2 at (-4,3),(-5,3),(-4,4),(-5,4)
    cells = []
    for cy in [3, 4]:
        for cx in [-4, -5]:
            t = solid_cell_tiles()
            if cy == 4:
                add_floor(t, CH - 2)
            else:
                add_platform(t, 3, 3, 12, TILE_SOLID)
            cells.append(((cx, cy), t, ()))
    add_open_pair((-4, 3), WEST, (-5, 3))
    add_open_pair((-4, 4), WEST, (-5, 4))
    add_open_pair((-4, 3), SOUTH, (-4, 4))
    add_open_pair((-5, 3), SOUTH, (-5, 4))
    add_door_pair((-3, 3), WEST, (-4, 3), "d_ante_to_sporespawn")
    register_room(cells, zone, bg)

    # Room 24: Pink Corridor Alpha — 3x1 at (-2,4),(-3,4),(-4,4) — wait (-4,4) taken
    # Use (-2,5),(-3,5),(-4,5) instead
    cells = []
    for cx in [(-2, 5), (-3, 5), (-4, 5)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 6, 2, 6, TILE_SPRING)
        cells.append((cx, t, ()))
    add_open_pair((-2, 5), WEST, (-3, 5))
    add_open_pair((-3, 5), WEST, (-4, 5))
    add_door_pair((-1, 5), WEST, (-2, 5), "d_pinkshaft_to_pinkcorridor_a")
    register_room(cells, zone, bg)

    # Room 25: Pink Corridor Beta — 2x1 at (-2,2),(-3,2)
    cells = []
    for cx in [(-2, 2), (-3, 2)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        cells.append((cx, t, ()))
    add_open_pair((-2, 2), WEST, (-3, 2))
    add_door_pair((-1, 2), WEST, (-2, 2), "d_pinkshaft_to_pinkcorridor_b")
    register_room(cells, zone, bg)

    # Room 26: Waterway — 4x1 at (-2,6),(-3,6),(-4,6),(-5,6)
    cells = []
    for i, cx in enumerate([(-2, 6), (-3, 6), (-4, 6), (-5, 6)]):
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        if i % 2 == 0:
            t[CH - 3][CW // 2] = TILE_SPRING
        cells.append((cx, t, ()))
    add_open_pair((-2, 6), WEST, (-3, 6))
    add_open_pair((-3, 6), WEST, (-4, 6))
    add_open_pair((-4, 6), WEST, (-5, 6))
    add_door_pair((-1, 5), SOUTH, (-1, 6), "d_pinkshaft_bottom_to_waterway_access")
    # Need a connector cell at (-1,6)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    register_room([((-1, 6), t, ())], zone, bg)
    add_door_pair((-1, 6), WEST, (-2, 6), "d_waterway_access_to_waterway")
    register_room(cells, zone, bg)

    # Room 27: Pipeworks — 2x1 at (-3,2) taken. Use (-4,2),(-5,2)
    cells = []
    for cx in [(-4, 2), (-5, 2)]:
        t = solid_cell_tiles()
        add_floor(t, CH - 2)
        add_platform(t, CH - 5, 2, CW - 2, TILE_CRUMBLE)
        cells.append((cx, t, ()))
    add_open_pair((-4, 2), WEST, (-5, 2))
    add_door_pair((-3, 2), WEST, (-4, 2), "d_pinkcorridor_b_to_pipeworks")
    register_room(cells, zone, bg)

    # Room 28: Save Station Gamma — 1x1 at (-1,4)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "save_station", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((-1, 4), WEST, (-2, 4), "d_pinkshaft_to_save_gamma")
    register_room([((-2, 4), t, entities)], zone, bg)

    # Room 29: Pink Junction — 1x1 at (0,4)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    add_platform(t, CH - 5, 3, 8, TILE_SOLID)
    add_door_pair((0, 3), SOUTH, (0, 4), "d_dachora_to_pink_junction")
    register_room([((0, 4), t, ())], zone, bg)

    # Room 30: Overgrown Alcove — 1x1 at (1,4)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "item_energy", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((0, 4), EAST, (1, 4), "d_pinkjunction_to_alcove")
    register_room([((1, 4), t, entities)], zone, bg)

    # Room 31: Spore Spawn Reward — 1x1 at (-5,3) — wait that's in arena
    # Use (-6,3)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "item_super_missile", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((-5, 3), WEST, (-6, 3), "d_sporespawn_to_reward")
    register_room([((-6, 3), t, entities)], zone, bg)

    # Room 32: Crumble Descent — 1x2 at (0,5),(0,6)
    cells = []
    for cy in [5, 6]:
        t = solid_cell_tiles()
        add_platform(t, CH - 4, 2, CW - 2, TILE_CRUMBLE)
        cells.append(((0, cy), t, ()))
    add_open_pair((0, 5), SOUTH, (0, 6))
    add_door_pair((0, 4), SOUTH, (0, 5), "d_pinkjunction_to_crumbledescent")
    register_room(cells, zone, bg)

    # Room 33: Pink Dead End — 1x1 at (-6,4)
    t = solid_cell_tiles()
    add_floor(t, CH - 2)
    entities = ({"type": "item_missile", "tile_x": CW // 2, "tile_y": CH - 3},)
    add_door_pair((-5, 4), WEST, (-6, 4), "d_sporespawn_to_deadend")
    register_room([((-6, 4), t, entities)], zone, bg)


# === RED BRINSTAR ===

def build_red_brinstar():
    zone = "Red Brinstar"
    bg = (60, 20, 20)

    # Room 34: Red Tower — 1x4 at (7,2)..(7,5)
    shaft_cells = []
    for cy in range(2, 6):
        coord = (7, cy)
        t = solid_cell_tiles()
        if cy % 2 == 0:
            add_platform(t, CH - 3, 2, 7, TILE_SOLID)
            add_platform(t, CH - 6, 9, 14, TILE_SOLID)
        else:
            add_platform(t, CH - 3, 9, 14, TILE_SOLID)
            add_platform(t, CH - 6, 2, 7, TILE_SOLID)
        shaft_cells.append((coord, t, ()))
    for cy in range(2, 5):
        add_open_pair((7, cy), SOUTH, (7, cy + 1))
    add_door_pair((7, 2), WEST, (6, 2), "d_upper_corridor_to_redtower")
    # Already have (6,2) in upper corridor
    # But wait, we already declared (7,2) in upper_corridor? No, upper corridor is (5,2)(6,2)(7,2)
    # Collision! Let me adjust. Upper corridor should be (5,2),(6,2) only, 2x1
    # Fix: we need to not overlap. Let me check - upper corridor was (5,2),(6,2),(7,2).
    # Red tower starts at (7,2). That's a conflict. Let me adjust upper corridor to (5,2),(6,2) only.
    # We'll fix this after — upper corridor's (7,2) was already registered. Let me use (8,2) for red tower start.
    # Actually, let me just shift the red tower to x=8
    pass

# The approach of manually handling collisions is getting error-prone.
# Let me take a more systematic approach: define ALL rooms with their
# absolute cell coordinates first, check for collisions, THEN connect them.

# ---------------------------------------------------------------------------
# SYSTEMATIC APPROACH: Define all rooms, then connect, then generate tiles
# ---------------------------------------------------------------------------

# Clear everything and start fresh
all_edges.clear()
cell_registry.clear()

# Room definitions: (room_name, zone, bg_color, cell_coords, special_info)
ROOMS = []

def R(name, zone, bg, coords, **kwargs):
    ROOMS.append({"name": name, "zone": zone, "bg": bg, "coords": coords, **kwargs})

# BLUE BRINSTAR — starting area (top)
R("Landing Site",       "Blue Brinstar",  (20,30,70),  [(0,0),(1,0),(2,0)], spawn=(0,0))
R("First Corridor",     "Blue Brinstar",  (20,30,70),  [(3,0),(4,0)])
R("Missile Chamber",    "Blue Brinstar",  (20,30,70),  [(5,0)], item="missile")
R("Save Alpha",         "Blue Brinstar",  (25,35,80),  [(0,-1)], save=True)
R("Morph Ball Tunnel",  "Blue Brinstar",  (20,30,70),  [(1,-1),(2,-1)])
R("Blue Shaft",         "Blue Brinstar",  (25,35,80),  [(4,-1)])

# GREEN BRINSTAR — central hub
R("Main Shaft",         "Green Brinstar", (20,50,25),  [(4,1),(4,2),(4,3),(4,4),(4,5)])
R("Map Station",        "Green Brinstar", (20,50,25),  [(3,1)], item="map")
R("Save Beta",          "Green Brinstar", (25,55,30),  [(5,1)], save=True)
R("Upper Corridor",     "Green Brinstar", (20,50,25),  [(5,2),(6,2)])
R("Fireflea Room",      "Green Brinstar", (15,40,20),  [(5,3),(6,3)])
R("Green Corridor E",   "Green Brinstar", (20,50,25),  [(5,4),(6,4)])
R("Green Corridor W",   "Green Brinstar", (20,50,25),  [(2,2),(3,2)])
R("Etecoon Chamber",    "Green Brinstar", (20,50,25),  [(2,3),(3,3)], springs=True)
R("Reserve Tank",       "Green Brinstar", (20,50,25),  [(3,4)], item="reserve_tank")
R("Crumble Bridge",     "Green Brinstar", (15,45,20),  [(5,5),(6,5)])
R("Elevator South",     "Green Brinstar", (30,55,30),  [(4,6)])
R("Green Junction",     "Green Brinstar", (20,50,25),  [(2,4)])

# PINK BRINSTAR — west wing
R("Dachora's Room",     "Pink Brinstar",  (55,22,40),  [(0,2),(1,2),(0,3),(1,3)])
R("Big Pink Shaft",     "Pink Brinstar",  (55,22,40),  [(-1,2),(-1,3),(-1,4),(-1,5)])
R("Spore Ante",         "Pink Brinstar",  (55,22,40),  [(-2,3),(-3,3)])
R("Spore Spawn Arena",  "Pink Brinstar",  (60,18,35),  [(-4,3),(-5,3),(-4,4),(-5,4)])
R("Pink Corridor A",    "Pink Brinstar",  (55,22,40),  [(-2,5),(-3,5),(-4,5)])
R("Pink Corridor B",    "Pink Brinstar",  (55,22,40),  [(-2,2),(-3,2)])
R("Waterway",           "Pink Brinstar",  (50,25,45),  [(-2,6),(-3,6),(-4,6),(-5,6)])
R("Waterway Access",    "Pink Brinstar",  (55,22,40),  [(-1,6)])
R("Pipeworks",          "Pink Brinstar",  (55,22,40),  [(-4,2),(-5,2)])
R("Save Gamma",         "Pink Brinstar",  (55,25,45),  [(-2,4)])
R("Pink Junction",      "Pink Brinstar",  (55,22,40),  [(0,4)])
R("Overgrown Alcove",   "Pink Brinstar",  (55,22,40),  [(1,4)], item="energy")
R("Spore Reward",       "Pink Brinstar",  (55,22,40),  [(-6,3)], item="super_missile")
R("Crumble Descent",    "Pink Brinstar",  (50,20,35),  [(0,5),(0,6)])
R("Pink Dead End",      "Pink Brinstar",  (55,22,40),  [(-6,4)], item="missile")

# RED BRINSTAR — east wing
R("Red Tower",          "Red Brinstar",   (60,20,20),  [(7,2),(7,3),(7,4),(7,5)])
R("Flooded Hall A",     "Red Brinstar",   (60,20,20),  [(8,3),(9,3),(10,3)])
R("Flooded Hall B",     "Red Brinstar",   (60,20,20),  [(8,4),(9,4)])
R("Spazer Room",        "Red Brinstar",   (60,20,20),  [(10,4)], item="spazer")
R("X-Ray Scope",        "Red Brinstar",   (55,18,18),  [(8,2)], item="xray")
R("Red Corridor",       "Red Brinstar",   (60,20,20),  [(8,5),(9,5),(10,5)])
R("Bat Cave",           "Red Brinstar",   (50,15,15),  [(9,2),(10,2),(9,1),(10,1)])
R("Save Delta",         "Red Brinstar",   (65,25,25),  [(7,1)], save=True)
R("Red Junction",       "Red Brinstar",   (60,20,20),  [(8,1)])
R("Crumble Gauntlet",   "Red Brinstar",   (55,18,18),  [(9,4)])  # wait (9,4) taken
R("Red Dead End",       "Red Brinstar",   (60,20,20),  [(11,3)], item="missile")
R("Hidden Alcove",      "Red Brinstar",   (55,18,18),  [(11,5)], item="power_bomb")

# KRAID'S LAIR — bottom
R("Warehouse Entrance", "Kraid's Lair",   (45,40,20),  [(3,7),(4,7)])
R("Warehouse Scaffold", "Kraid's Lair",   (45,40,20),  [(3,8),(4,8),(3,9),(4,9)])
R("Boss Corridor",      "Kraid's Lair",   (45,40,20),  [(5,8),(6,8),(7,8)])
R("Kraid's Room",       "Kraid's Lair",   (50,35,15),  [(5,9),(6,9),(5,10),(6,10),(7,9),(7,10)])
R("Kraid Reward",       "Kraid's Lair",   (50,45,25),  [(8,9)], item="varia_suit")
R("Save Epsilon",       "Kraid's Lair",   (50,45,25),  [(2,8)], save=True)
R("Lair Shaft",         "Kraid's Lair",   (45,40,20),  [(2,9),(2,10)])
R("Lair Corridor",      "Kraid's Lair",   (45,40,20),  [(3,10),(4,10)])  # wait (3,10) free? no conflict with warehouse scaffold
# Hmm (3,9),(4,9) are in Warehouse Scaffold and (3,10),(4,10) might work if not taken by Kraid's Room
# Kraid's Room has (5,9),(6,9),(5,10),(6,10),(7,9),(7,10) — OK, no conflict
R("Trap Room",          "Kraid's Lair",   (45,40,20),  [(8,8)])
R("Lair Junction",      "Kraid's Lair",   (45,40,20),  [(5,7)])

# Remove the conflicting Crumble Gauntlet ((9,4) already taken by Flooded Hall B)
# Replace with (11,4)
ROOMS = [r for r in ROOMS if r["name"] != "Crumble Gauntlet"]
R("Crumble Gauntlet",   "Red Brinstar",   (55,18,18),  [(11,4)])


def check_no_cell_collisions():
    """Verify no two rooms claim the same cell."""
    seen = {}
    for room in ROOMS:
        for coord in room["coords"]:
            if coord in seen:
                raise ValueError(f"Cell {coord} claimed by both '{seen[coord]}' and '{room['name']}'")
            seen[coord] = room["name"]
    print(f"  No collisions among {len(seen)} cells in {len(ROOMS)} rooms.")


# Door connections: (room_a_cell, direction, room_b_cell, door_id)
DOORS = [
    # Blue Brinstar internal and connections
    ((2, 0), EAST, (3, 0), "d_landing_to_corridor1"),
    ((4, 0), EAST, (5, 0), "d_corridor1_to_missile"),
    ((0, 0), NORTH, (0, -1), "d_landing_to_save_alpha"),
    ((1, 0), NORTH, (1, -1), "d_landing_to_morphtunnel"),
    ((4, 0), NORTH, (4, -1), "d_corridor1_to_blueshaft"),

    # Blue -> Green
    ((4, 0), SOUTH, (4, 1), "d_blue_to_green_shaft"),

    # Green Brinstar shaft connections
    ((4, 1), WEST, (3, 1), "d_shaft_to_mapstation"),
    ((4, 1), EAST, (5, 1), "d_shaft_to_save_beta"),
    ((4, 2), EAST, (5, 2), "d_shaft_to_upper_corridor"),
    ((4, 3), EAST, (5, 3), "d_shaft_to_fireflea"),
    ((4, 4), EAST, (5, 4), "d_shaft_to_green_east"),
    ((4, 2), WEST, (3, 2), "d_shaft_to_green_west"),
    ((4, 3), WEST, (3, 3), "d_shaft_to_etecoon"),
    ((4, 4), WEST, (3, 4), "d_shaft_to_reserve"),
    ((4, 5), EAST, (5, 5), "d_shaft_to_crumblebridge"),
    ((4, 5), SOUTH, (4, 6), "d_shaft_to_elevator_south"),
    ((2, 3), SOUTH, (2, 4), "d_etecoon_to_junction"),

    # Green -> Red
    ((6, 2), EAST, (7, 2), "d_upper_to_redtower"),
    ((6, 4), EAST, (7, 4), "d_green_east_to_redtower"),

    # Green -> Pink
    ((2, 2), WEST, (1, 2), "d_greenwest_to_dachora"),

    # Green -> Kraid
    ((4, 6), SOUTH, (4, 7), "d_elevator_to_warehouse"),

    # Pink Brinstar connections
    ((0, 2), WEST, (-1, 2), "d_dachora_to_pinkshaft"),
    ((-1, 3), WEST, (-2, 3), "d_pinkshaft_to_spore_ante"),
    ((-3, 3), WEST, (-4, 3), "d_ante_to_sporespawn"),
    ((-1, 5), WEST, (-2, 5), "d_pinkshaft_to_pinkcorridor_a"),
    ((-1, 2), WEST, (-2, 2), "d_pinkshaft_to_pinkcorridor_b"),
    ((-1, 5), SOUTH, (-1, 6), "d_pinkshaft_to_waterway_access"),
    ((-1, 6), WEST, (-2, 6), "d_waterway_access_to_waterway"),
    ((-3, 2), WEST, (-4, 2), "d_pinkcorridor_b_to_pipeworks"),
    ((-1, 4), WEST, (-2, 4), "d_pinkshaft_to_save_gamma"),
    ((0, 3), SOUTH, (0, 4), "d_dachora_to_pink_junction"),
    ((0, 4), EAST, (1, 4), "d_pinkjunction_to_alcove"),
    ((-5, 3), WEST, (-6, 3), "d_sporespawn_to_reward"),
    ((0, 4), SOUTH, (0, 5), "d_pinkjunction_to_crumbledescent"),
    ((-5, 4), WEST, (-6, 4), "d_sporespawn_to_deadend"),

    # Red Brinstar connections
    ((7, 3), EAST, (8, 3), "d_redtower_to_flooded_a"),
    ((10, 3), EAST, (11, 3), "d_flooded_a_to_deadend"),
    ((8, 4), EAST, (9, 4), "d_flooded_b_to_flooded_b2"),  # wait (9,4) is flooded hall B
    # Actually Flooded Hall B is (8,4),(9,4) — so this is open within the room, not a door
    ((10, 3), SOUTH, (10, 4), "d_flooded_a_to_spazer"),  # wait (10,4) is spazer
    # Spazer is a separate room at (10,4) — this needs a door
    ((7, 2), EAST, (8, 2), "d_redtower_to_xray"),
    ((7, 5), EAST, (8, 5), "d_redtower_to_red_corridor"),
    ((10, 5), EAST, (11, 5), "d_redcorridor_to_hidden"),
    ((10, 2), EAST, (11, 4), "d_placeholder"),  # skip this, cells not adjacent
    ((7, 1), EAST, (8, 1), "d_save_delta_to_redjunction"),
    ((9, 1), EAST, (10, 1), "d_redjunction_batcave"),  # (9,1) and (10,1) are in bat cave, (8,1) is junction
    ((8, 1), EAST, (9, 1), "d_redjunction_to_batcave"),
    ((11, 4), WEST, (10, 4), "d_crumble_gauntlet_to_spazer"),  # wait, need to check adjacency
    ((7, 4), SOUTH, (7, 5), "d_skip"),  # these are within red tower, already open

    # Kraid's Lair connections
    ((3, 7), WEST, (2, 7), "d_skip2"),  # wait (2,7) doesn't exist
    ((4, 7), EAST, (5, 7), "d_warehouse_to_lairjunction"),
    ((4, 8), EAST, (5, 8), "d_scaffold_to_bosscorridor"),
    ((7, 8), SOUTH, (7, 9), "d_bosscorridor_to_kraid"),
    ((7, 9), EAST, (8, 9), "d_kraid_to_reward"),
    ((3, 8), WEST, (2, 8), "d_scaffold_to_save_epsilon"),
    ((2, 8), SOUTH, (2, 9), "d_save_epsilon_to_lairshaft"),  # wait (2,9) is lair shaft
    ((7, 8), EAST, (8, 8), "d_bosscorridor_to_trap"),
]

# I realize this manual approach is getting messy with many errors. Let me clean it up properly.
# Remove all the bad/conflicting door entries and rebuild systematically.

all_edges.clear()
cell_registry.clear()
DOORS.clear()

# Rebuild DOORS list cleanly
DOORS = [
    # === BLUE BRINSTAR ===
    ((2, 0), EAST,  (3, 0),  "d01"),  # Landing -> First Corridor
    ((4, 0), EAST,  (5, 0),  "d02"),  # First Corridor -> Missile Chamber
    ((0, 0), NORTH, (0, -1), "d03"),  # Landing -> Save Alpha
    ((1, 0), NORTH, (1, -1), "d04"),  # Landing -> Morph Ball Tunnel
    ((4, 0), NORTH, (4, -1), "d05"),  # First Corridor -> Blue Shaft

    # === BLUE -> GREEN ===
    ((4, 0), SOUTH, (4, 1),  "d06"),  # First Corridor -> Main Shaft top

    # === GREEN BRINSTAR ===
    ((4, 1), WEST,  (3, 1),  "d07"),  # Shaft -> Map Station
    ((4, 1), EAST,  (5, 1),  "d08"),  # Shaft -> Save Beta
    ((4, 2), EAST,  (5, 2),  "d09"),  # Shaft -> Upper Corridor
    ((4, 3), EAST,  (5, 3),  "d10"),  # Shaft -> Fireflea
    ((4, 4), EAST,  (5, 4),  "d11"),  # Shaft -> Green Corridor E
    ((4, 2), WEST,  (3, 2),  "d12"),  # Shaft -> Green Corridor W
    ((4, 3), WEST,  (3, 3),  "d13"),  # Shaft -> Etecoon
    ((4, 4), WEST,  (3, 4),  "d14"),  # Shaft -> Reserve Tank
    ((4, 5), SOUTH, (4, 6),  "d16"),  # Shaft -> Elevator South
    ((2, 3), SOUTH, (2, 4),  "d17"),  # Etecoon -> Green Junction

    # === GREEN -> RED ===
    ((6, 2), EAST,  (7, 2),  "d18"),  # Upper Corridor -> Red Tower
    ((6, 4), EAST,  (7, 4),  "d19"),  # Green Corridor E -> Red Tower

    # === GREEN -> PINK ===
    ((2, 2), WEST,  (1, 2),  "d20"),  # Green Corridor W -> Dachora's Room

    # === GREEN -> KRAID ===
    ((4, 6), SOUTH, (4, 7),  "d21"),  # Elevator South -> Warehouse Entrance

    # === PINK BRINSTAR ===
    ((0, 2), WEST,  (-1, 2), "d22"),  # Dachora -> Big Pink Shaft
    ((-1, 3), WEST, (-2, 3), "d23"),  # Pink Shaft -> Spore Ante
    ((-3, 3), WEST, (-4, 3), "d24"),  # Spore Ante -> Spore Spawn Arena
    ((-1, 5), WEST, (-2, 5), "d25"),  # Pink Shaft -> Pink Corridor A
    ((-1, 2), WEST, (-2, 2), "d26"),  # Pink Shaft -> Pink Corridor B
    ((-1, 5), SOUTH, (-1, 6), "d27"), # Pink Shaft -> Waterway Access
    ((-1, 6), WEST, (-2, 6), "d28"),  # Waterway Access -> Waterway
    ((-3, 2), WEST, (-4, 2), "d29"),  # Pink Corridor B -> Pipeworks
    ((-1, 4), WEST, (-2, 4), "d30"),  # Pink Shaft -> Save Gamma
    ((0, 3), SOUTH, (0, 4),  "d31"),  # Dachora -> Pink Junction
    ((0, 4), EAST,  (1, 4),  "d32"),  # Pink Junction -> Overgrown Alcove
    ((-5, 3), WEST, (-6, 3), "d33"),  # Spore Spawn -> Spore Reward
    ((0, 4), SOUTH, (0, 5),  "d34"),  # Pink Junction -> Crumble Descent
    ((-5, 4), WEST, (-6, 4), "d35"),  # Spore Spawn -> Pink Dead End

    # === RED BRINSTAR ===
    ((7, 3), EAST,  (8, 3),  "d36"),  # Red Tower -> Flooded Hall A
    ((10, 3), EAST, (11, 3), "d37"),  # Flooded Hall A -> Red Dead End
    ((10, 3), SOUTH,(10, 4), "d38"),  # Flooded Hall A -> Spazer (not adjacent? 10,3 S -> 10,4 yes)
    ((7, 2), EAST,  (8, 2),  "d39"),  # Red Tower -> X-Ray Scope
    ((7, 5), EAST,  (8, 5),  "d40"),  # Red Tower -> Red Corridor
    ((10, 5), EAST, (11, 5), "d41"),  # Red Corridor -> Hidden Alcove
    ((7, 1), EAST,  (8, 1),  "d42"),  # Save Delta -> Red Junction
    ((8, 1), EAST,  (9, 1),  "d43"),  # Red Junction -> Bat Cave
    ((10, 4), EAST, (11, 4), "d44"),  # Spazer -> Crumble Gauntlet

    # === KRAID'S LAIR ===
    ((4, 7), EAST,  (5, 7),  "d45"),  # Warehouse Entrance -> Lair Junction
    ((4, 8), EAST,  (5, 8),  "d46"),  # Warehouse Scaffold -> Boss Corridor
    ((7, 8), SOUTH, (7, 9),  "d47"),  # Boss Corridor -> Kraid's Room
    ((7, 9), EAST,  (8, 9),  "d48"),  # Kraid's Room -> Kraid Reward
    ((3, 8), WEST,  (2, 8),  "d49"),  # Warehouse Scaffold -> Save Epsilon
    ((2, 8), SOUTH, (2, 9),  "d50"),  # Save Epsilon -> Lair Shaft
    ((7, 8), EAST,  (8, 8),  "d51"),  # Boss Corridor -> Trap Room
    ((5, 7), SOUTH, (5, 8),  "d52"),  # Lair Junction -> Boss Corridor
    ((3, 9), SOUTH, (3, 10), "d53"),  # Warehouse Scaffold -> Lair Corridor
    ((4, 9), SOUTH, (4, 10), "d54"),  # Warehouse Scaffold -> Lair Corridor (2nd cell)
]


def build_all_edges():
    """Create all open edges within rooms and all door edges between rooms."""
    # First: open edges within each multi-cell room
    for room in ROOMS:
        coords = room["coords"]
        coord_set = set(coords)
        for coord in coords:
            for direction in ALL_DIRECTIONS:
                dx, dy = DIRECTION_DELTA[direction]
                neighbor = (coord[0] + dx, coord[1] + dy)
                if neighbor in coord_set:
                    _ensure(coord)
                    if direction not in all_edges[coord]:
                        add_open_pair(coord, direction, neighbor)
    
    # Second: door edges between rooms
    for a, direction, b, door_id in DOORS:
        add_door_pair(a, direction, b, door_id)


def generate_tiles_for_cell(coord: GridCoord):
    """Generate tiles for a cell based on its room membership and edges."""
    tiles = solid_cell_tiles()
    edges = all_edges.get(coord, {})
    
    # Carve edges
    for direction, edge in edges.items():
        if edge.edge_type == EDGE_OPEN:
            carve_gap(tiles, direction, full=True)
        elif edge.edge_type == EDGE_DOOR:
            carve_gap(tiles, direction, full=False)
    
    # Find which room this cell belongs to
    room_info = None
    for room in ROOMS:
        if coord in room["coords"]:
            room_info = room
            break
    
    if room_info is None:
        return tiles
    
    # Add floor platform (important: every room needs solid ground!)
    # But only if bottom edge is not open (floor would interfere with vertical open seam)
    has_south_open = edges.get(SOUTH, Edge(direction=SOUTH, edge_type=EDGE_WALL)).edge_type == EDGE_OPEN
    has_south_door = edges.get(SOUTH, Edge(direction=SOUTH, edge_type=EDGE_WALL)).edge_type == EDGE_DOOR
    has_north_door = edges.get(NORTH, Edge(direction=NORTH, edge_type=EDGE_WALL)).edge_type == EDGE_DOOR
    if not has_south_open:
        add_floor(tiles, CH - 2)
    if has_south_door:
        for x in range(DOOR_COL - SEAM_SPAN // 2, DOOR_COL - SEAM_SPAN // 2 + SEAM_SPAN):
            tiles[CH - 2][x] = TILE_EMPTY
    if has_north_door:
        for x in range(DOOR_COL - SEAM_SPAN // 2, DOOR_COL - SEAM_SPAN // 2 + SEAM_SPAN):
            tiles[1][x] = TILE_EMPTY
    
    # Add room-specific features
    name = room_info["name"]
    
    if "Shaft" in name or "Tower" in name:
        # Vertical shafts: zig-zag platforms for climbing
        cy = coord[1]
        if cy % 2 == 0:
            add_platform(tiles, CH - 4, 2, 7, TILE_SOLID)
            add_platform(tiles, CH - 7, 9, 14, TILE_ONEWAY_PLAT)
        else:
            add_platform(tiles, CH - 4, 9, 14, TILE_SOLID)
            add_platform(tiles, CH - 7, 2, 7, TILE_ONEWAY_PLAT)
    
    elif "Corridor" in name or "Tunnel" in name:
        # Horizontal corridors with platforms
        add_platform(tiles, CH - 5, 4, 10, TILE_ONEWAY_PLAT)
    
    elif "Arena" in name or "Kraid" in name and "Room" in name:
        # Boss rooms: open with minimal platforms
        if not has_south_open:
            add_platform(tiles, CH - 5, 2, 5, TILE_SOLID)
            add_platform(tiles, CH - 5, 11, 14, TILE_SOLID)
    
    elif room_info.get("springs"):
        # Spring room
        if not has_south_open:
            tiles[CH - 3][CW // 2] = TILE_SPRING
            tiles[CH - 3][CW // 2 + 1] = TILE_SPRING
            add_platform(tiles, 3, 4, 12, TILE_SOLID)
    
    elif "Crumble" in name or "Gauntlet" in name:
        # Crumble block rooms
        add_platform(tiles, CH - 4, 2, CW - 2, TILE_CRUMBLE)
    
    elif "Waterway" in name:
        # Water rooms with springs for traversal
        tiles[CH - 3][CW // 2] = TILE_SPRING
        tiles[CH - 3][CW // 2 - 2] = TILE_SPRING
    
    elif "Fireflea" in name:
        # Dark room with crumble platforms
        add_platform(tiles, CH - 5, 2, 6, TILE_CRUMBLE)
        add_platform(tiles, CH - 5, 8, 13, TILE_CRUMBLE)
    
    elif "Bat Cave" in name:
        # Multi-level platforming
        cy = coord[1]
        if cy % 2 == 0:
            add_platform(tiles, CH - 4, 3, 8, TILE_SOLID)
            add_platform(tiles, 3, 8, 13, TILE_ONEWAY_PLAT)
        else:
            add_platform(tiles, CH - 4, 8, 13, TILE_SOLID)
            add_platform(tiles, 3, 3, 8, TILE_ONEWAY_PLAT)
    
    elif "Scaffold" in name:
        # Platforming with mixed tile types
        cy = coord[1]
        if cy % 2 == 0:
            add_platform(tiles, CH - 4, 2, 7, TILE_SOLID)
            add_platform(tiles, 4, 9, 14, TILE_ONEWAY_PLAT)
        else:
            add_platform(tiles, CH - 4, 9, 14, TILE_SOLID)
            add_platform(tiles, 4, 2, 7, TILE_CRUMBLE)
    
    elif "Trap" in name:
        # One-way platforms above springs
        add_platform(tiles, CH - 5, 3, 12, TILE_ONEWAY_PLAT)
        tiles[CH - 3][CW // 2] = TILE_SPRING
    
    return tiles


def validate_world():
    """Check door symmetry and edge consistency."""
    errors = []
    for coord in cell_registry:
        edges = all_edges.get(coord, {})
        for direction in ALL_DIRECTIONS:
            edge = edges.get(direction)
            if edge is None:
                continue
            dx, dy = DIRECTION_DELTA[direction]
            neighbor = (coord[0] + dx, coord[1] + dy)
            opp = OPPOSITE_DIRECTION[direction]
            
            if edge.edge_type == EDGE_DOOR:
                if neighbor not in cell_registry:
                    errors.append(f"{coord} door {direction} -> {neighbor} (nonexistent)")
                else:
                    n_edges = all_edges.get(neighbor, {})
                    n_edge = n_edges.get(opp)
                    if n_edge is None or n_edge.edge_type != EDGE_DOOR:
                        errors.append(f"Asymmetric door: {coord}/{direction} but {neighbor}/{opp} is {n_edge}")
                    elif n_edge.target_cell != coord:
                        errors.append(f"Door target mismatch: {neighbor}/{opp} -> {n_edge.target_cell} not {coord}")
            
            elif edge.edge_type == EDGE_OPEN:
                if neighbor not in cell_registry:
                    errors.append(f"{coord} open {direction} -> {neighbor} (nonexistent)")
                else:
                    n_edges = all_edges.get(neighbor, {})
                    n_edge = n_edges.get(opp)
                    if n_edge is None or n_edge.edge_type != EDGE_OPEN:
                        errors.append(f"Asymmetric open: {coord}/{direction} but {neighbor}/{opp} is {n_edge}")
    
    if errors:
        for e in errors:
            print(f"  ERROR: {e}")
        raise ValueError(f"{len(errors)} validation errors")
    print("  Validation passed: all edges symmetric.")


def count_rooms_via_flood_fill():
    """Count distinct rooms by flood-filling through open edges."""
    visited = set()
    rooms = []
    for coord in cell_registry:
        if coord in visited:
            continue
        # BFS through open edges
        queue = [coord]
        room_cells = set()
        while queue:
            c = queue.pop(0)
            if c in visited:
                continue
            visited.add(c)
            room_cells.add(c)
            edges = all_edges.get(c, {})
            for direction in ALL_DIRECTIONS:
                edge = edges.get(direction)
                if edge and edge.edge_type == EDGE_OPEN:
                    dx, dy = DIRECTION_DELTA[direction]
                    neighbor = (c[0] + dx, c[1] + dy)
                    if neighbor in cell_registry and neighbor not in visited:
                        queue.append(neighbor)
        rooms.append(room_cells)
    return rooms


def main():
    # Check for cell collisions first
    print("Checking cell placement...")
    check_no_cell_collisions()
    
    # Build all edges
    print("Building edges...")
    build_all_edges()
    
    # Generate tiles for each cell and register
    print("Generating tiles...")
    for room in ROOMS:
        for coord in room["coords"]:
            tiles = generate_tiles_for_cell(coord)
            entities = ()
            if room.get("spawn") == coord:
                entities = ({"type": "spawn_point", "tile_x": CW // 2, "tile_y": CH - 3},)
            elif room.get("save"):
                entities = ({"type": "save_station", "tile_x": CW // 2, "tile_y": CH - 3},)
            elif room.get("item"):
                entities = ({"type": f"item_{room['item']}", "tile_x": CW // 2, "tile_y": CH - 3},)
            cell_registry[coord] = {
                "tiles": tiles, "zone": room["zone"],
                "bg_color": room["bg"], "entities": entities,
            }
    
    # Validate
    print("Validating world...")
    validate_world()
    
    # Count rooms
    rooms = count_rooms_via_flood_fill()
    zones = set(cell_registry[list(r)[0]]["zone"] for r in rooms)
    print(f"  {len(rooms)} distinct rooms, {len(zones)} zones, {len(cell_registry)} cells")
    
    # Check no room is too large
    max_room = max(len(r) for r in rooms)
    print(f"  Largest room: {max_room} cells")
    assert max_room <= 12, f"Room too large: {max_room} cells (max {12})"
    
    # Write files
    CELLS_DIR.mkdir(parents=True, exist_ok=True)
    # Remove old cell files
    for f in CELLS_DIR.glob("*.json"):
        f.unlink()
    
    cell_files = {}
    for coord, info in cell_registry.items():
        edges = all_edges.get(coord, {})
        cell = Cell(
            coord=coord,
            tiles=tuple(tuple(row) for row in info["tiles"]),
            edges=edges,
            zone=info["zone"],
            background_color=info["bg_color"],
            entities=info["entities"],
        )
        filename = f"{coord[0]}_{coord[1]}.json"
        save_cell(cell, CELLS_DIR / filename)
        cell_files[coord] = filename
    
    index = WorldIndex(cell_files=cell_files, start_cell=(0, 0))
    save_world_index(index, WORLD_INDEX_PATH)
    print(f"Wrote {len(cell_files)} cell files to {CELLS_DIR}")
    print(f"Wrote world index to {WORLD_INDEX_PATH}")


if __name__ == "__main__":
    main()
