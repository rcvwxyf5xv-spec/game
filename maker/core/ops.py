"""Every edit the editor can do.

Each op takes `model` first, validates its arguments (raising
maker.core.errors.OpError with a clear human message on bad input),
wraps itself in model.transaction() and returns a small
JSON-serialisable dict describing what changed. OPS maps op name ->
callable for a CLI/batch runner; inspect.signature/docstrings work on
the registered callables.

Coordinates may be given as (x, y) tuples or [x, y] lists and are
normalised internally. Cell "tiles" grids are 10 rows (y) of 16 tiles
(x); "world tile coords" (wtx, wty) = cx * 16 + tx, cy * 10 + ty.
"""

from __future__ import annotations

import copy
import functools
import re
from typing import Dict, List, Optional, Tuple

from src.data.schema import (
    ALL_DIRECTIONS,
    DIRECTION_DELTA,
    OPPOSITE_DIRECTION,
    TILE_KINDS,
)
from src.data.serialize import cell_from_dict, cell_to_dict
from src.engine.constants import CELL_HEIGHT_TILES, CELL_WIDTH_TILES
from src.project import DEFAULT_LAYOUT

from maker.core.errors import OpError
from maker.core.model import _coord

# One-char -> tile kind map used by set_cell_ascii / cell_ascii.
DEFAULT_LEGEND = {
    ".": "empty",
    "#": "solid",
    "^": "spring",
    "=": "oneway",
    "%": "crumble",
}

_NON_PASSABLE = ("solid", "crumble", "spring")

DEFAULT_ENTITY_TYPES = [
    "spawn_point", "save_station", "item_missile",
    "item_super_missile", "item_power_bomb", "item_energy",
    "item_reserve_tank", "item_varia_suit", "item_spazer",
    "item_xray", "item_map",
]

OPS: Dict[str, callable] = {}


def op(fn):
    """Register fn in OPS and wrap it in a transaction of its own name."""
    @functools.wraps(fn)
    def wrapper(model, *args, **kwargs):
        with model.transaction(fn.__name__):
            return fn(model, *args, **kwargs)
    OPS[fn.__name__] = wrapper
    return wrapper


# -- shared validation helpers -------------------------------------------


def _require_cell(model, coord) -> dict:
    coord = _coord(coord)
    data = model.cell(coord)
    if data is None:
        raise OpError(f"no cell at {coord}")
    return data


def _require_kind(kind: str) -> str:
    if kind not in TILE_KINDS:
        raise OpError(
            f"unknown tile kind {kind!r} (expected one of {list(TILE_KINDS)})")
    return kind


def _require_palette(model, name: str) -> None:
    if name not in model.palettes:
        raise OpError(f"unknown palette {name!r}")


def _require_direction(direction: str) -> str:
    if direction not in ALL_DIRECTIONS:
        raise OpError(
            f"unknown direction {direction!r} "
            f"(expected one of {list(ALL_DIRECTIONS)})")
    return direction


def _check_tile_bounds(tx, ty, coord) -> None:
    if not (0 <= tx < CELL_WIDTH_TILES and 0 <= ty < CELL_HEIGHT_TILES):
        raise OpError(
            f"tile ({tx}, {ty}) is outside cell {coord} "
            f"(grid is {CELL_WIDTH_TILES}x{CELL_HEIGHT_TILES})")


def _check_entity_bounds(tx, ty, coord) -> None:
    if not (0 <= tx < CELL_WIDTH_TILES and 0 <= ty < CELL_HEIGHT_TILES):
        raise OpError(
            f"entity tile ({tx}, {ty}) is outside cell {coord} "
            f"(grid is {CELL_WIDTH_TILES}x{CELL_HEIGHT_TILES})")


def _validate_rgb(value, label: str) -> list:
    try:
        rgb = [int(c) for c in value]
    except (TypeError, ValueError):
        raise OpError(f"{label}: expected an RGB triple of 3 ints") from None
    if len(rgb) != 3 or any(c < 0 or c > 255 for c in rgb):
        raise OpError(f"{label}: expected an RGB triple of 3 ints 0..255")
    return rgb


def _validate_ramp(value, label: str) -> list:
    if not isinstance(value, (list, tuple)) or len(value) != 8:
        raise OpError(f"{label}: expected a ramp of 8 RGB triples")
    return [_validate_rgb(c, f"{label}[{i}]") for i, c in enumerate(value)]


def _validate_colors(colors, name: str) -> None:
    if not isinstance(colors, dict):
        raise OpError(f"palette {name!r} build.colors must be a dict")
    if "rock" not in colors or "cap" not in colors:
        raise OpError(
            f"palette {name!r} build.colors needs 'rock' and 'cap' ramps")
    _validate_ramp(colors["rock"], f"palette {name!r} rock")
    _validate_ramp(colors["cap"], f"palette {name!r} cap")
    if "accent" not in colors:
        raise OpError(f"palette {name!r} build.colors needs an 'accent'")
    _validate_rgb(colors["accent"], f"palette {name!r} accent")


def _dominant_palette(tiles_grid) -> str:
    counts: Dict[str, int] = {}
    for row in tiles_grid:
        for tile in row:
            pal = tile.get("palette", "") if isinstance(tile, dict) else ""
            if pal:
                counts[pal] = counts.get(pal, 0) + 1
    if not counts:
        return ""
    return max(sorted(counts), key=counts.__getitem__)


def _resolve_tile(kind: str, palette_arg, cur: dict, dominant: str,
                  keep_existing_only_if_kind_unchanged: bool = False) -> dict:
    """Palette semantics: kind "empty" drops the palette; an explicit
    palette wins; otherwise keep the tile's existing palette if it has
    one (only when its kind is unchanged, for ascii edits), else the
    cell's dominant palette."""
    if kind == "empty":
        return {"kind": "empty"}
    if palette_arg is not None:
        return {"kind": kind, "palette": palette_arg}
    if keep_existing_only_if_kind_unchanged and cur.get("kind") != kind:
        pal = dominant
    else:
        pal = cur.get("palette") or dominant
    if pal:
        return {"kind": kind, "palette": pal}
    return {"kind": kind}


def _world_to_cell(value: int, cell_size: int) -> Tuple[int, int]:
    """World tile coord -> (cell coord, local tile); floor division so
    negative coords land in the correct cell."""
    return divmod(value, cell_size)


def _neighbour_cells(model, coord) -> List[Tuple[int, int]]:
    out = []
    for direction in ALL_DIRECTIONS:
        dx, dy = DIRECTION_DELTA[direction]
        nbr = (coord[0] + dx, coord[1] + dy)
        if model.has_cell(nbr):
            out.append(nbr)
    return out


def _default_new_cell_palette(model, coord) -> str:
    """Most-used palette among neighbours, else overall, else 'default'."""
    neighbour_tiles = []
    all_tiles = []
    for c in _neighbour_cells(model, coord):
        neighbour_tiles.extend(model.cell(c)["tiles"])
    for c in sorted(model.cells):
        all_tiles.extend(model.cell(c)["tiles"])

    def most(grid) -> str:
        counts: Dict[str, int] = {}
        for row in grid:
            for tile in row:
                pal = tile.get("palette", "") if isinstance(tile, dict) else ""
                if pal:
                    counts[pal] = counts.get(pal, 0) + 1
        if not counts:
            return ""
        return max(sorted(counts), key=counts.__getitem__)

    return most(neighbour_tiles) or most(all_tiles) or "default"


def _template_tiles(template: str, palette: str) -> list:
    rows = []
    for y in range(CELL_HEIGHT_TILES):
        row = []
        for x in range(CELL_WIDTH_TILES):
            if template == "solid":
                kind = "solid"
            elif template == "empty":
                kind = "empty"
            else:  # walled: solid border ring, empty interior
                border = (y == 0 or y == CELL_HEIGHT_TILES - 1
                          or x == 0 or x == CELL_WIDTH_TILES - 1)
                kind = "solid" if border else "empty"
            tile: dict = {"kind": kind}
            if kind != "empty" and palette:
                tile["palette"] = palette
            row.append(tile)
        rows.append(row)
    return rows


def _next_door_id(model, used: Optional[set] = None) -> str:
    """Auto door id: 'd' + next free number after the max existing,
    zero-padded to 2 digits like the existing 'd01'..'d54'."""
    ids = set() if used is None else used
    if used is None:
        for data in model.cells.values():
            for edge in data.get("edges", {}).values():
                m = re.match(r"^d(\d+)$", str(edge.get("door_id", "")))
                if m:
                    ids.add(int(m.group(1)))
    nxt = (max(ids) + 1) if ids else 1
    ids.add(nxt)
    return f"d{nxt:02d}"


def _set_edge_wall(model, cell_coord, direction) -> None:
    """Set one edge to wall (used to dissolve door/open leftovers)."""
    data = model.cell(cell_coord)
    if direction in data.get("edges", {}):
        data["edges"][direction] = {"type": "wall"}


def _close_door_other_end(model, cell_coord, direction) -> None:
    """If (cell_coord, direction) is a door, its target-side edge -> wall."""
    data = model.cell(cell_coord)
    edge = data.get("edges", {}).get(direction)
    if not edge or edge.get("type") != "door":
        return
    target = edge.get("target_cell")
    te = edge.get("target_edge", "")
    if not target or te not in ALL_DIRECTIONS:
        return
    t = tuple(target)
    if not model.has_cell(t):
        return
    model._touch_cell(t)
    _set_edge_wall(model, t, te)


def _wall_open_neighbor(model, cell_coord, direction) -> None:
    """The edge (cell_coord, direction) is being replaced: if it is
    currently open, the cell across it loses its matching open edge."""
    data = model.cell(cell_coord)
    edge = data.get("edges", {}).get(direction)
    if not edge or edge.get("type") != "open":
        return
    dx, dy = DIRECTION_DELTA[direction]
    nbr = (cell_coord[0] + dx, cell_coord[1] + dy)
    if not model.has_cell(nbr):
        return
    opp = OPPOSITE_DIRECTION[direction]
    ndata = model.cell(nbr)
    if ndata.get("edges", {}).get(opp, {}).get("type") == "open":
        model._touch_cell(nbr)
        _set_edge_wall(model, nbr, opp)


def _carve_gap(cell_data, direction) -> bool:
    """Open a gap in a fully solid boundary tile line on one side:
    east/west -> rows 6,7,8 of the boundary column; north/south ->
    columns 7,8 of the boundary row. Returns True if a gap was carved."""
    tiles = cell_data.get("tiles")
    if not tiles or len(tiles) != CELL_HEIGHT_TILES:
        return False
    if direction in ("east", "west"):
        col = CELL_WIDTH_TILES - 1 if direction == "east" else 0
        line = [tiles[y][col] for y in range(CELL_HEIGHT_TILES)]
        if any((t.get("kind") if isinstance(t, dict) else None) != "solid"
               for t in line):
            return False
        for y in (6, 7, 8):
            tiles[y][col] = {"kind": "empty"}
        return True
    row = 0 if direction == "north" else CELL_HEIGHT_TILES - 1
    line = tiles[row]
    if len(line) != CELL_WIDTH_TILES or any(
            (t.get("kind") if isinstance(t, dict) else None) != "solid"
            for t in line):
        return False
    for x in (7, 8):
        tiles[row][x] = {"kind": "empty"}
    return True


def _zone_default() -> dict:
    """src.gfx DEFAULT_PALETTE with tuples converted to lists."""
    from src.gfx.palette import DEFAULT_PALETTE

    def lists(value):
        if isinstance(value, tuple):
            return [lists(v) for v in value]
        return value

    return copy.deepcopy({k: lists(v) for k, v in DEFAULT_PALETTE.items()})


def _validate_zone(zone, name: str) -> None:
    if not isinstance(zone, dict):
        raise OpError(f"zone {name!r}: data must be a dict")
    for key in ("rock", "cap", "accent"):
        if key not in zone:
            raise OpError(f"zone {name!r}: missing key {key!r}")
    _validate_ramp(zone["rock"], f"zone {name!r} rock")
    _validate_ramp(zone["cap"], f"zone {name!r} cap")
    _validate_rgb(zone["accent"], f"zone {name!r} accent")
    for key in ("fog_top", "fog_bottom", "ambient"):
        if key in zone and key not in ("rock", "cap"):
            _validate_rgb(zone[key], f"zone {name!r} {key}")


def _require_zone_name(name) -> str:
    if not isinstance(name, str) or not name:
        raise OpError("zone name must be a non-empty string")
    return name


_PALETTE_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _require_palette_name(name) -> str:
    if not isinstance(name, str) or not _PALETTE_NAME_RE.match(name):
        raise OpError(
            f"palette name {name!r} must match ^[a-z0-9_]+$")
    return name


# =========================================================================
# Cells
# =========================================================================


@op
def new_cell(model, coord, zone="", template="walled", palette=None,
             background_color=None):
    """Create a new cell at `coord` (errors if one already exists).

    Args:
        coord: cell coordinate (tuple or [x, y]).
        zone: cosmetic zone label; defaults to a neighbour's zone.
        template: "walled" (solid ring, empty interior), "empty" or
            "solid"; default "walled".
        palette: tile palette for solid tiles; defaults to the most-used
            palette among neighbours, else overall, else "default".
        background_color: [r, g, b]; defaults to a neighbour's.
    """
    coord = _coord(coord)
    with model.transaction("new_cell"):
        if model.has_cell(coord):
            raise OpError(f"a cell already exists at {coord}")
        if template not in ("walled", "empty", "solid"):
            raise OpError(
                f"unknown template {template!r} "
                f"(expected 'walled', 'empty' or 'solid')")
        if palette is not None:
            _require_palette(model, palette)
        neighbours = _neighbour_cells(model, coord)
        model._touch_index()
        if not zone and neighbours:
            zone = model.cell(neighbours[0]).get("zone", "")
        if background_color is None:
            if neighbours:
                background_color = model.cell(neighbours[0]).get(
                    "background_color")
            background_color = (background_color
                                if background_color is not None
                                else [18, 18, 28])
        if palette is None:
            palette = _default_new_cell_palette(model, coord)
        cell = {
            "coord": list(coord),
            "zone": zone or "",
            "tiles": _template_tiles(template, palette),
            "edges": {},
            "entities": [],
            "background_color": [int(c) for c in background_color],
        }
        model._touch_cell(coord)
        model._cells[coord] = cell_to_dict(cell_from_dict(cell))
        model._cell_objects.pop(coord, None)
        return {"created": list(coord), "palette": palette, "zone": zone}


@op
def delete_cell(model, coord, force=False):
    """Delete the cell at `coord`; neighbours' open edges towards it and
    any door targeting it become walls. Errors on the start cell unless
    force=True.

    Args:
        coord: cell coordinate.
        force: allow deleting the start cell.
    """
    coord = _coord(coord)
    with model.transaction("delete_cell"):
        if not model.has_cell(coord):
            raise OpError(f"no cell at {coord}")
        if model.start_cell == coord and not force:
            raise OpError(
                f"{coord} is the start cell; pass force=True to delete it")
        model._touch_index()
        model._touch_cell(coord)
        # doors anywhere targeting this cell dissolve (both ends -> wall)
        for other in sorted(model.cells):
            data = model.cell(other)
            for direction, edge in list(data.get("edges", {}).items()):
                if edge.get("type") == "door" and tuple(
                        edge.get("target_cell") or []) == coord:
                    model._touch_cell(other)
                    _set_edge_wall(model, other, direction)
        # neighbours' open edges towards this cell -> wall
        for direction in ALL_DIRECTIONS:
            dx, dy = DIRECTION_DELTA[direction]
            nbr = (coord[0] + dx, coord[1] + dy)
            if model.has_cell(nbr):
                opp = OPPOSITE_DIRECTION[direction]
                nedge = model.cell(nbr).get("edges", {}).get(opp)
                if nedge and nedge.get("type") == "open":
                    model._touch_cell(nbr)
                    _set_edge_wall(model, nbr, opp)
        del model._cells[coord]
        model._cell_objects.pop(coord, None)
        return {"deleted": list(coord)}


@op
def move_cells(model, coords, dx, dy):
    """Translate a set of cells by (dx, dy).

    Args:
        coords: list of cell coords to move together.
        dx, dy: whole-cell translation. Errors if a destination is
            occupied by a cell outside the set. Door targets of moved
            cells are remapped; open edges between a moved and a
            non-moved cell become walls on both sides; the start cell
            moves with the set if it is part of it.
    """
    coords = [_coord(c) for c in coords]
    dx, dy = int(dx), int(dy)
    with model.transaction("move_cells"):
        if not coords:
            raise OpError("move_cells requires at least one coord")
        move_set = set(coords)
        if dx == 0 and dy == 0:
            raise OpError("move_cells with dx=dy=0 changes nothing")
        for c in coords:
            if not model.has_cell(c):
                raise OpError(f"no cell at {c} (cannot move it)")
        for c in coords:
            dest = (c[0] + dx, c[1] + dy)
            if model.has_cell(dest) and dest not in move_set:
                raise OpError(
                    f"destination {dest} is occupied by another cell")
        model._touch_index()
        for c in coords:
            model._touch_cell(c)
        for c in coords:
            # destinations too: undo must also remove the moved copies
            # (before-image of a free destination is None)
            model._touch_cell((c[0] + dx, c[1] + dy))
        # remap door targets everywhere (moved cells + everyone pointing
        # at them)
        for other in sorted(model.cells):
            data = model.cell(other)
            for edge in data.get("edges", {}).values():
                t = edge.get("target_cell")
                if t and tuple(t) in move_set:
                    src = tuple(t)
                    edge["target_cell"] = [src[0] + dx, src[1] + dy]
        # open edges between moved and non-moved cells -> wall (both sides)
        for c in coords:
            data = model.cell(c)
            for direction in ALL_DIRECTIONS:
                edge = data.get("edges", {}).get(direction)
                if not edge or edge.get("type") != "open":
                    continue
                ddx, ddy = DIRECTION_DELTA[direction]
                nbr = (c[0] + ddx, c[1] + ddy)
                if nbr in move_set or not model.has_cell(nbr):
                    continue
                model._touch_cell(nbr)
                opp = OPPOSITE_DIRECTION[direction]
                _set_edge_wall(model, nbr, opp)
                _set_edge_wall(model, c, direction)
        # move the actual cells
        for c in coords:
            data = model._cells.pop(c)
            data["coord"] = [c[0] + dx, c[1] + dy]
            model._cell_objects.pop(c, None)
            model._cells[(c[0] + dx, c[1] + dy)] = data
        if model.start_cell in move_set:
            sx, sy = model.start_cell
            model._start_cell = (sx + dx, sy + dy)
        return {"moved": len(coords), "dx": dx, "dy": dy}


@op
def duplicate_cells(model, coords, dx, dy):
    """Copy a set of cells translated by (dx, dy).

    Args:
        coords: list of cell coords to duplicate.
        dx, dy: where the copies go. Errors if a destination is
            occupied. Edges/doors among the copies are preserved (doors
            get fresh ids); edges/doors pointing outside become walls.
    """
    coords = [_coord(c) for c in coords]
    dx, dy = int(dx), int(dy)
    with model.transaction("duplicate_cells"):
        if not coords:
            raise OpError("duplicate_cells requires at least one coord")
        src_set = set(coords)
        dest_of = {c: (c[0] + dx, c[1] + dy) for c in coords}
        dest_set = set(dest_of.values())
        if len(dest_set) != len(coords):
            raise OpError("duplicate destinations overlap each other")
        for c in coords:
            if not model.has_cell(c):
                raise OpError(f"no cell at {c} (cannot duplicate it)")
        for d in sorted(dest_set):
            if model.has_cell(d):
                raise OpError(f"destination {d} is occupied")
        if dx == 0 and dy == 0:
            raise OpError("duplicate_cells with dx=dy=0 changes nothing")
        model._touch_index()
        for d in sorted(dest_set):
            model._touch_cell(d)
        dest_to_src = {v: k for k, v in dest_of.items()}
        copies: Dict[Tuple[int, int], dict] = {}
        for c in coords:
            new = copy.deepcopy(model.cell(c))
            new["coord"] = list(dest_of[c])
            for direction, edge in list(new.get("edges", {}).items()):
                ddx, ddy = DIRECTION_DELTA[direction]
                nbr = (c[0] + ddx, c[1] + ddy)
                if nbr in src_set:
                    continue  # preserved; door targets remapped below
                edge["type"] = "wall"
                edge.pop("door_id", None)
                edge.pop("target_cell", None)
                edge.pop("target_edge", None)
            copies[dest_of[c]] = new
        # doors among copies: remap targets to the copies, fresh ids
        used = set()
        for data in model.cells.values():
            for edge in data.get("edges", {}).values():
                m = re.match(r"^d(\d+)$", str(edge.get("door_id", "")))
                if m:
                    used.add(int(m.group(1)))
        done = set()
        for d in sorted(copies):
            for direction, edge in copies[d].get("edges", {}).items():
                if edge.get("type") != "door":
                    continue
                if (d, direction) in done:
                    continue
                t_src = tuple(edge.get("target_cell") or ())
                if t_src not in src_set:
                    continue  # non-copy door targets were walled already
                t = dest_of[t_src]
                fresh = _next_door_id(model, used)
                edge["target_cell"] = [t[0], t[1]]
                edge["door_id"] = fresh
                done.add((d, direction))
                te = edge.get("target_edge", "")
                partner = copies[t].get("edges", {}).get(te)
                if partner and partner.get("type") == "door":
                    partner["target_cell"] = [d[0], d[1]]
                    partner["door_id"] = fresh
                    done.add((t, te))
        for d, new in copies.items():
            model._cells[d] = new
        return {"created": [list(d) for d in sorted(copies)]}


@op
def set_cell_props(model, coord, zone=None, background_color=None):
    """Update a cell's zone and/or background_color (None = unchanged).

    Args:
        coord: cell coordinate.
        zone: new zone label, or None to keep.
        background_color: [r, g, b] or None to keep.
    """
    coord = _coord(coord)
    with model.transaction("set_cell_props"):
        data = _require_cell(model, coord)
        model._touch_cell(coord)
        if zone is not None:
            data["zone"] = zone
        if background_color is not None:
            data["background_color"] = _validate_rgb(
                background_color, f"cell {coord} background_color")
        return {"cell": list(coord)}


@op
def set_cell_data(model, coord, data):
    """Replace a cell wholesale from a raw cell dict (the raw JSON
    editor).

    Args:
        coord: the cell to replace (must match data["coord"] if present).
        data: full cell dict; validated by running it through
            cell_from_dict (including the 16x10 tile dims check) and
            canonicalised.
    """
    coord = _coord(coord)
    with model.transaction("set_cell_data"):
        if not isinstance(data, dict):
            raise OpError("set_cell_data expects a cell dict")
        raw = copy.deepcopy(data)
        dcoord = raw.get("coord")
        if dcoord is None:
            raw["coord"] = list(coord)
        else:
            if _coord(dcoord) != coord:
                raise OpError(
                    f"cell data coord {_coord(dcoord)} does not match {coord}")
        try:
            parsed = cell_from_dict(raw)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise OpError(f"invalid cell data for {coord}: {exc}") from None
        if (len(parsed.tiles) != CELL_HEIGHT_TILES
                or any(len(row) != CELL_WIDTH_TILES
                       for row in parsed.tiles)):
            raise OpError(
                f"cell {coord} tiles must be "
                f"{CELL_WIDTH_TILES}x{CELL_HEIGHT_TILES} "
                f"(got {len(parsed.tiles)} rows)")
        model._touch_cell(coord)
        model._cells[coord] = cell_to_dict(parsed)
        model._cell_objects.pop(coord, None)
        return {"replaced": list(coord)}


@op
def set_start_cell(model, coord):
    """Set which cell the player starts in.

    Args:
        coord: an existing cell coordinate.
    """
    coord = _coord(coord)
    with model.transaction("set_start_cell"):
        _require_cell(model, coord)
        model._touch_index()
        model._start_cell = coord
        return {"start_cell": list(coord)}


# =========================================================================
# Tiles
# =========================================================================


@op
def set_tiles(model, coord, points, kind, palette=None):
    """Set a list of tiles to a kind (and palette).

    Args:
        coord: cell coordinate.
        points: list of [tx, ty] tile coords inside the cell.
        kind: one of the tile kinds (empty/solid/spring/oneway/crumble).
        palette: palette name; None keeps each tile's existing palette
            (else the cell's dominant palette). Empty tiles drop their
            palette.
    """
    coord = _coord(coord)
    with model.transaction("set_tiles"):
        data = _require_cell(model, coord)
        kind = _require_kind(kind)
        if palette is not None:
            _require_palette(model, palette)
        pts = []
        for p in points:
            tx, ty = int(p[0]), int(p[1])
            _check_tile_bounds(tx, ty, coord)
            pts.append((tx, ty))
        if not pts:
            raise OpError("set_tiles requires at least one point")
        model._touch_cell(coord)
        dominant = _dominant_palette(data["tiles"])
        for tx, ty in pts:
            data["tiles"][ty][tx] = _resolve_tile(
                kind, palette, data["tiles"][ty][tx], dominant)
        return {"cell": list(coord), "points": len(pts)}


@op
def fill_rect(model, coord, x0, y0, x1, y1, kind, palette=None):
    """Fill an inclusive tile rectangle (corners in any order); clipped
    to the cell, errors if fully outside.

    Args:
        coord: cell coordinate.
        x0, y0, x1, y1: inclusive rect corners in tile coords.
        kind, palette: as for set_tiles.
    """
    coord = _coord(coord)
    with model.transaction("fill_rect"):
        data = _require_cell(model, coord)
        kind = _require_kind(kind)
        if palette is not None:
            _require_palette(model, palette)
        x0, x1, y0, y1 = int(x0), int(x1), int(y0), int(y1)
        if x0 > x1:
            x0, x1 = x1, x0
        if y0 > y1:
            y0, y1 = y1, y0
        if (x1 < 0 or x0 >= CELL_WIDTH_TILES
                or y1 < 0 or y0 >= CELL_HEIGHT_TILES):
            raise OpError(
                f"rect ({x0},{y0})-({x1},{y1}) lies fully outside cell "
                f"{coord}")
        x0, y0 = max(0, x0), max(0, y0)
        x1 = min(CELL_WIDTH_TILES - 1, x1)
        y1 = min(CELL_HEIGHT_TILES - 1, y1)
        model._touch_cell(coord)
        dominant = _dominant_palette(data["tiles"])
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                data["tiles"][ty][tx] = _resolve_tile(
                    kind, palette, data["tiles"][ty][tx], dominant)
        return {"cell": list(coord), "tiles": (x1 - x0 + 1) * (y1 - y0 + 1)}


@op
def flood_fill(model, coord, tx, ty, kind, palette=None):
    """Flood-fill a contiguous 4-neighbour same-(kind, palette) region.

    Args:
        coord: cell coordinate.
        tx, ty: seed tile.
        kind, palette: as for set_tiles.
    """
    coord = _coord(coord)
    with model.transaction("flood_fill"):
        data = _require_cell(model, coord)
        kind = _require_kind(kind)
        if palette is not None:
            _require_palette(model, palette)
        tx, ty = int(tx), int(ty)
        _check_tile_bounds(tx, ty, coord)
        model._touch_cell(coord)
        dominant = _dominant_palette(data["tiles"])
        target = data["tiles"][ty][tx]
        target_key = (target.get("kind"),
                      target.get("palette", ""))
        new = _resolve_tile(kind, palette, target, dominant)
        new_key = (new["kind"], new.get("palette", ""))
        if target_key == new_key:
            return {"cell": list(coord), "filled": 0}
        stack = [(tx, ty)]
        filled = 0
        while stack:
            x, y = stack.pop()
            cur = data["tiles"][y][x]
            if (cur.get("kind"), cur.get("palette", "")) != target_key:
                continue
            data["tiles"][y][x] = dict(new)
            filled += 1
            for ddx, ddy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + ddx, y + ddy
                if 0 <= nx < CELL_WIDTH_TILES and 0 <= ny < CELL_HEIGHT_TILES:
                    stack.append((nx, ny))
        return {"cell": list(coord), "filled": filled}


@op
def paint_world(model, points, kind, palette=None):
    """Set tiles at WORLD tile coords, spanning cells.

    Args:
        points: list of [wtx, wty] world tile coords.
        kind, palette: as for set_tiles; points in non-existent cells
            are reported as skipped.
    """
    with model.transaction("paint_world"):
        kind = _require_kind(kind)
        if palette is not None:
            _require_palette(model, palette)
        if not points:
            raise OpError("paint_world requires at least one point")
        grouped: Dict[Tuple[int, int], list] = {}
        skipped = []
        for p in points:
            wtx, wty = int(p[0]), int(p[1])
            cx, tx = _world_to_cell(wtx, CELL_WIDTH_TILES)
            cy, ty = _world_to_cell(wty, CELL_HEIGHT_TILES)
            if not model.has_cell((cx, cy)):
                skipped.append([wtx, wty])
                continue
            grouped.setdefault((cx, cy), []).append((tx, ty))
        for c in grouped:
            model._touch_cell(c)
        painted = 0
        for (cx, cy), pts in grouped.items():
            data = model.cell((cx, cy))
            dominant = _dominant_palette(data["tiles"])
            for tx, ty in pts:
                data["tiles"][ty][tx] = _resolve_tile(
                    kind, palette, data["tiles"][ty][tx], dominant)
                painted += 1
        return {"painted": painted, "skipped": skipped}


def get_region(model, wx, wy, w, h):
    """Read-only: h rows x w cols of tile dicts (world tile coords from
    (wx, wy)), None where the cell is missing. Copies; not an op."""
    rows = []
    for j in range(int(h)):
        row = []
        for i in range(int(w)):
            wtx, wty = int(wx) + i, int(wy) + j
            cx, tx = _world_to_cell(wtx, CELL_WIDTH_TILES)
            cy, ty = _world_to_cell(wty, CELL_HEIGHT_TILES)
            data = model.cell((cx, cy))
            if data is None:
                row.append(None)
            else:
                row.append(copy.deepcopy(data["tiles"][ty][tx]))
        rows.append(row)
    return rows


@op
def put_region(model, wx, wy, region, skip_empty=False):
    """Paste a tile region at world tile coords (wx, wy).

    Args:
        wx, wy: world tile coord of the top-left of the region.
        region: h rows of w tile dicts (None entries skipped).
        skip_empty: skip tiles whose kind is "empty".
    """
    if not isinstance(region, list) or not region:
        raise OpError("put_region expects a non-empty list of rows")
    width = None
    for row in region:
        if not isinstance(row, list):
            raise OpError("put_region rows must be lists")
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise OpError("put_region rows must all be the same length")
    if width == 0:
        raise OpError("put_region rows must not be empty")
    with model.transaction("put_region"):
        touched: set = set()
        for j, row in enumerate(region):
            for i, tile in enumerate(row):
                if tile is None:
                    continue
                if not isinstance(tile, dict):
                    raise OpError(
                        f"put_region tile ({i},{j}) must be a tile dict "
                        f"or None")
                if tile.get("kind") not in TILE_KINDS:
                    raise OpError(
                        f"put_region tile ({i},{j}) has unknown kind "
                        f"{tile.get('kind')!r}")
                pal = tile.get("palette")
                if pal:
                    _require_palette(model, pal)
                if skip_empty and tile.get("kind") == "empty":
                    continue
                wtx, wty = int(wx) + i, int(wy) + j
                cx, tx = _world_to_cell(wtx, CELL_WIDTH_TILES)
                cy, ty = _world_to_cell(wty, CELL_HEIGHT_TILES)
                if model.has_cell((cx, cy)):
                    touched.add((cx, cy))
        for c in sorted(touched):
            model._touch_cell(c)
        pasted = 0
        for j, row in enumerate(region):
            for i, tile in enumerate(row):
                if tile is None:
                    continue
                if skip_empty and tile.get("kind") == "empty":
                    continue
                wtx, wty = int(wx) + i, int(wy) + j
                cx, tx = _world_to_cell(wtx, CELL_WIDTH_TILES)
                cy, ty = _world_to_cell(wty, CELL_HEIGHT_TILES)
                if not model.has_cell((cx, cy)):
                    continue
                data = model.cell((cx, cy))
                if tile.get("kind") == "empty":
                    data["tiles"][ty][tx] = {"kind": "empty"}
                else:
                    new: dict = {"kind": tile["kind"]}
                    if tile.get("palette"):
                        new["palette"] = tile["palette"]
                    data["tiles"][ty][tx] = new
                pasted += 1
        return {"pasted": pasted}


def _validate_ascii_rows(rows, coord) -> None:
    if not isinstance(rows, (list, tuple)) or len(rows) != CELL_HEIGHT_TILES:
        raise OpError(
            f"cell {coord} ascii needs exactly {CELL_HEIGHT_TILES} rows, "
            f"got {len(rows) if isinstance(rows, (list, tuple)) else '?'}")
    for y, row in enumerate(rows):
        if not isinstance(row, str) or len(row) != CELL_WIDTH_TILES:
            got = len(row) if isinstance(row, str) else "not a string"
            raise OpError(
                f"cell {coord} ascii row {y + 1}: expected "
                f"{CELL_WIDTH_TILES} chars, got {got}")


@op
def set_cell_ascii(model, coord, rows, legend=None, palette=None):
    """Set a cell from 10 strings of 16 chars.

    Args:
        coord: cell coordinate.
        rows: list of 10 strings, 16 chars each; DEFAULT_LEGEND is
            {'.': empty, '#': solid, '^': spring, '=': oneway,
            '%': crumble}.
        legend: optional char -> kind map overriding DEFAULT_LEGEND.
        palette: optional palette for non-empty chars; else keep the
            tile's palette when its kind is unchanged, else the cell's
            dominant palette.
    """
    legend = legend or DEFAULT_LEGEND
    coord = _coord(coord)
    with model.transaction("set_cell_ascii"):
        data = _require_cell(model, coord)
        _validate_ascii_rows(rows, coord)
        if palette is not None:
            _require_palette(model, palette)
        chars = {}
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == ".":
                    continue
                if ch not in legend:
                    raise OpError(
                        f"cell {coord} ascii row {y + 1} col {x + 1}: "
                        f"unknown legend char {ch!r}")
                chars[(x, y)] = legend[ch]
        model._touch_cell(coord)
        dominant = _dominant_palette(data["tiles"])
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == ".":
                    data["tiles"][y][x] = {"kind": "empty"}
                else:
                    kind = legend[ch]
                    data["tiles"][y][x] = _resolve_tile(
                        kind, palette, data["tiles"][y][x], dominant,
                        keep_existing_only_if_kind_unchanged=True)
        return {"cell": list(coord)}


def cell_ascii(model, coord, legend=None):
    """Read-only: 10 strings of 16 chars encoding each tile's kind.
    Raises OpError if a tile kind is not representable in the legend.
    Not an op."""
    legend = legend or DEFAULT_LEGEND
    coord = _coord(coord)
    data = model.cell(coord)
    if data is None:
        raise OpError(f"no cell at {coord}")
    char_for: Dict[str, str] = {}
    for ch, kind in legend.items():
        char_for.setdefault(kind, ch)
    lines = []
    for y, row in enumerate(data["tiles"]):
        chars = []
        for x, tile in enumerate(row):
            kind = tile.get("kind", "empty")
            if kind not in char_for:
                raise OpError(
                    f"cell {coord} tile ({x},{y}) kind {kind!r} is not "
                    f"representable in the legend")
            chars.append(char_for[kind])
        lines.append("".join(chars))
    return lines


@op
def replace_tiles(model, cells=None, from_kind=None, from_palette=None,
                  to_kind=None, to_palette=None):
    """Bulk-replace tiles across cells (None = all cells).

    Args:
        cells: optional list of cell coords to restrict to.
        from_kind/from_palette: match filter (None = any; a tile without
            a palette has palette "").
        to_kind/to_palette: what matching tiles become; at least one
            required. Setting to_kind to "empty" drops palettes.
    """
    with model.transaction("replace_tiles"):
        if to_kind is None and to_palette is None:
            raise OpError(
                "replace_tiles needs at least one of to_kind/to_palette")
        if to_kind is not None:
            _require_kind(to_kind)
        if to_palette is not None:
            _require_palette(model, to_palette)
        if from_kind is not None:
            _require_kind(from_kind)
        if cells is None:
            scope = sorted(model.cells)
        else:
            scope = [_coord(c) for c in cells]
            for c in scope:
                _require_cell(model, c)
        matching: Dict[Tuple[int, int], list] = {}
        for c in scope:
            data = model.cell(c)
            pts = []
            for ty, row in enumerate(data["tiles"]):
                for tx, tile in enumerate(row):
                    if (from_kind is not None
                            and tile.get("kind", "empty") != from_kind):
                        continue
                    if (from_palette is not None
                            and tile.get("palette", "") != from_palette):
                        continue
                    pts.append((tx, ty))
            if pts:
                matching[c] = pts
        for c in matching:
            model._touch_cell(c)
        replaced = 0
        for c, pts in matching.items():
            data = model.cell(c)
            for tx, ty in pts:
                cur = data["tiles"][ty][tx]
                kind = (to_kind if to_kind is not None
                        else cur.get("kind", "empty"))
                if kind == "empty":
                    data["tiles"][ty][tx] = {"kind": "empty"}
                else:
                    pal = (to_palette if to_palette is not None
                           else cur.get("palette", ""))
                    new: dict = {"kind": kind}
                    if pal:
                        new["palette"] = pal
                    data["tiles"][ty][tx] = new
                replaced += 1
        return {"replaced": replaced}


# =========================================================================
# Edges & doors
# =========================================================================


@op
def set_edge(model, coord, direction, edge_type, carve=False):
    """Set an edge type to "wall" or "open" on BOTH sides of the
    boundary.

    Args:
        coord: cell coordinate.
        direction: north/south/east/west.
        edge_type: "wall" or "open" (opening requires the neighbour cell
            to exist).
        carve: if True and the boundary tile line is fully solid, carve
            a gap (east/west -> rows 6,7,8 of the column; north/south ->
            columns 7,8 of the row) on both cells.
    """
    coord = _coord(coord)
    direction = _require_direction(direction)
    if edge_type not in ("wall", "open"):
        raise OpError(f"edge type must be 'wall' or 'open', got {edge_type!r}")
    with model.transaction("set_edge"):
        _require_cell(model, coord)
        dx, dy = DIRECTION_DELTA[direction]
        nbr = (coord[0] + dx, coord[1] + dy)
        if edge_type == "open" and not model.has_cell(nbr):
            raise OpError(
                f"cannot open the {direction} edge of {coord}: no cell at "
                f"{nbr}")
        model._touch_cell(coord)
        if model.has_cell(nbr):
            model._touch_cell(nbr)
        opp = OPPOSITE_DIRECTION[direction]
        _close_door_other_end(model, coord, direction)
        if model.has_cell(nbr):
            _close_door_other_end(model, nbr, opp)
        model.cell(coord)["edges"][direction] = {"type": edge_type}
        if model.has_cell(nbr):
            model.cell(nbr)["edges"][opp] = {"type": edge_type}
        carved = False
        if carve:
            carved = _carve_gap(model.cell(coord), direction)
            if model.has_cell(nbr):
                carved = _carve_gap(model.cell(nbr), opp) or carved
        return {
            "cell": list(coord), "direction": direction,
            "edge": edge_type,
            "neighbor": list(nbr) if model.has_cell(nbr) else None,
            "carved": carved,
        }


@op
def link_door(model, coord, direction, target, target_edge=None,
              door_id=None, carve=False):
    """Link a door between (coord, direction) and
    (target, target_edge=opposite).

    Args:
        coord: cell coordinate of one end.
        direction: which edge of `coord`.
        target: cell coordinate of the other end (must exist).
        target_edge: edge of the target; defaults to the opposite
            direction.
        door_id: shared door id; auto-generated ("d" + next free number,
            zero-padded to 2) when None.
        carve: carve a passable gap in the boundary lines (see set_edge)
            when True.
    """
    coord = _coord(coord)
    direction = _require_direction(direction)
    target = _coord(target)
    with model.transaction("link_door"):
        _require_cell(model, coord)
        if not model.has_cell(target):
            raise OpError(f"no cell at {target} (door target missing)")
        if target_edge is None:
            target_edge = OPPOSITE_DIRECTION[direction]
        target_edge = _require_direction(target_edge)
        if target == coord and target_edge == direction:
            raise OpError(
                f"door on {coord}/{direction} cannot target the same edge")
        if door_id is None:
            door_id = _next_door_id(model)
        elif not isinstance(door_id, str) or not door_id:
            raise OpError("door_id must be a non-empty string")
        model._touch_cell(coord)
        model._touch_cell(target)
        _close_door_other_end(model, coord, direction)
        _close_door_other_end(model, target, target_edge)
        _wall_open_neighbor(model, coord, direction)
        _wall_open_neighbor(model, target, target_edge)
        model.cell(coord)["edges"][direction] = {
            "type": "door", "door_id": door_id,
            "target_cell": list(target), "target_edge": target_edge,
        }
        model.cell(target)["edges"][target_edge] = {
            "type": "door", "door_id": door_id,
            "target_cell": list(coord), "target_edge": direction,
        }
        carved = False
        if carve:
            carved = (_carve_gap(model.cell(coord), direction)
                      or _carve_gap(model.cell(target), target_edge))
        return {
            "cell": list(coord), "direction": direction,
            "target": list(target), "target_edge": target_edge,
            "door_id": door_id, "carved": carved,
        }


@op
def unlink_door(model, coord, direction):
    """Break a door: both ends become walls. No-op when the edge is not
    a door.

    Args:
        coord: cell coordinate of one end.
        direction: which edge.
    """
    coord = _coord(coord)
    direction = _require_direction(direction)
    with model.transaction("unlink_door"):
        data = _require_cell(model, coord)
        edge = data.get("edges", {}).get(direction)
        if not edge or edge.get("type") != "door":
            return {"changed": False}
        model._touch_cell(coord)
        target = edge.get("target_cell")
        te = edge.get("target_edge", "")
        other = None
        if target is not None:
            t = tuple(target)
            if t == coord:
                other = coord
            elif model.has_cell(t):
                other = t
        if other is not None and te in ALL_DIRECTIONS:
            model._touch_cell(other)
            _set_edge_wall(model, other, te)
        _set_edge_wall(model, coord, direction)
        return {
            "changed": True, "cell": list(coord),
            "target": list(other) if other else None,
        }


# =========================================================================
# Entities
# =========================================================================


@op
def add_entity(model, coord, type, tx, ty, props=None):
    """Add an entity at tile (tx, ty) in a cell; returns its index.

    Args:
        coord: cell coordinate.
        type: entity type string (e.g. "save_station").
        tx, ty: tile coords inside the cell.
        props: optional extra entity fields merged in.
        Adding a "spawn_point" removes any other spawn_point in the
        world and sets the start cell to `coord`.
    """
    coord = _coord(coord)
    with model.transaction("add_entity"):
        data = _require_cell(model, coord)
        if not isinstance(type, str) or not type:
            raise OpError("entity type must be a non-empty string")
        tx, ty = int(tx), int(ty)
        _check_entity_bounds(tx, ty, coord)
        if props is None:
            props = {}
        if not isinstance(props, dict):
            raise OpError("props must be a dict")
        if type == "spawn_point":
            model._touch_index()
            model._start_cell = coord
        model._touch_cell(coord)
        if type == "spawn_point":
            for other in sorted(model.cells):
                odata = model.cell(other)
                idxs = [i for i, e in enumerate(odata.get("entities", []))
                        if e.get("type") == "spawn_point"]
                if idxs:
                    model._touch_cell(other)
                    for i in reversed(idxs):
                        del odata["entities"][i]
        entity = {"type": type, "tile_x": tx, "tile_y": ty}
        entity.update(copy.deepcopy(props))
        data.setdefault("entities", []).append(entity)
        return {"index": len(data["entities"]) - 1}


@op
def move_entity(model, coord, index, tx, ty, to_coord=None):
    """Move an entity to new tile coords, optionally into another cell.

    Args:
        coord: cell currently containing the entity.
        index: position in that cell's entities list.
        tx, ty: destination tile coords (inside to_coord).
        to_coord: destination cell; defaults to `coord`.
    """
    coord = _coord(coord)
    with model.transaction("move_entity"):
        data = _require_cell(model, coord)
        index = int(index)
        if index < 0 or index >= len(data.get("entities", [])):
            raise OpError(f"no entity index {index} in cell {coord}")
        to_coord = _coord(to_coord) if to_coord is not None else coord
        tx, ty = int(tx), int(ty)
        _check_entity_bounds(tx, ty, to_coord)
        if not model.has_cell(to_coord):
            raise OpError(f"no cell at {to_coord}")
        model._touch_cell(coord)
        if to_coord != coord:
            model._touch_cell(to_coord)
        entity = data["entities"].pop(index)
        entity["tile_x"], entity["tile_y"] = tx, ty
        model.cell(to_coord).setdefault("entities", []).append(entity)
        return {"index": index, "cell": list(to_coord)}


@op
def update_entity(model, coord, index, props):
    """Merge props into an entity (type/tile_x/tile_y are validated).

    Args:
        coord: cell coordinate.
        index: entity position in the cell.
        props: dict merged into the entity (overwrites existing keys).
    """
    coord = _coord(coord)
    with model.transaction("update_entity"):
        data = _require_cell(model, coord)
        index = int(index)
        if index < 0 or index >= len(data.get("entities", [])):
            raise OpError(f"no entity index {index} in cell {coord}")
        if not isinstance(props, dict):
            raise OpError("props must be a dict")
        entity = data["entities"][index]
        for key, val in props.items():
            if key == "type" and (not isinstance(val, str) or not val):
                raise OpError("entity type must be a non-empty string")
            if key == "tile_x":
                _check_entity_bounds(int(val), entity["tile_y"], coord)
            if key == "tile_y":
                _check_entity_bounds(entity["tile_x"], int(val), coord)
        model._touch_cell(coord)
        entity.update(copy.deepcopy(props))
        return {"index": index}


@op
def remove_entity(model, coord, index):
    """Remove an entity by index.

    Args:
        coord: cell coordinate.
        index: entity position in the cell.
    """
    coord = _coord(coord)
    with model.transaction("remove_entity"):
        data = _require_cell(model, coord)
        index = int(index)
        if index < 0 or index >= len(data.get("entities", [])):
            raise OpError(f"no entity index {index} in cell {coord}")
        model._touch_cell(coord)
        data["entities"].pop(index)
        return {"index": index}


def entity_types(model) -> List[str]:
    """All known entity types: manifest "entity_types" if present, else
    the default list, plus any types actually found in cells. Helper,
    not an op."""
    defaults = list(DEFAULT_ENTITY_TYPES)
    listed = model.manifest.get("entity_types")
    if isinstance(listed, list):
        types = [str(t) for t in listed]
    else:
        types = list(defaults)
    seen = set(types)
    for data in model.cells.values():
        for entity in data.get("entities", []):
            t = entity.get("type")
            if t and t not in seen:
                seen.add(t)
                types.append(t)
    return types


# =========================================================================
# Zones
# =========================================================================


@op
def set_zone(model, name, data):
    """Create/replace a zone's palette data.

    Args:
        name: zone name.
        data: zone data dict; missing keys are filled from a built-in
            default. Validated: rock/cap ramps of 8 RGB triples, accent
            and optional fog_top/fog_bottom/ambient as single RGB.
    """
    with model.transaction("set_zone"):
        name = _require_zone_name(name)
        if not isinstance(data, dict):
            raise OpError("zone data must be a dict")
        zone = _zone_default()
        zone.update(copy.deepcopy(data))
        _validate_zone(zone, name)
        model._touch_zones()
        model._zones[name] = zone
        return {"zone": name}


@op
def rename_zone(model, old, new, update_cells=True):
    """Rename a zone; optionally update cells using it.

    Args:
        old: existing zone name.
        new: replacement zone name.
        update_cells: also update the zone label of cells using it.
    """
    with model.transaction("rename_zone"):
        old = _require_zone_name(old)
        new = _require_zone_name(new)
        if old not in model._zones:
            raise OpError(f"no zone {old!r}")
        if old == new:
            return {"renamed": False}
        if new in model._zones:
            raise OpError(f"zone {new!r} already exists")
        model._touch_zones()
        model._zones[new] = model._zones.pop(old)
        updated = 0
        if update_cells:
            users = [c for c in sorted(model.cells)
                     if model.cell(c).get("zone", "") == old]
            for c in users:
                model._touch_cell(c)
            for c in users:
                model.cell(c)["zone"] = new
                updated += 1
        return {"renamed": True, "old": old, "new": new,
                "cells_updated": updated}


@op
def delete_zone(model, name, force=False):
    """Delete a zone; errors if cells still use it unless force=True.

    Args:
        name: zone name.
        force: delete even if cells reference it (their label becomes
            undefined; validate reports it).
    """
    with model.transaction("delete_zone"):
        name = _require_zone_name(name)
        if name not in model._zones:
            raise OpError(f"no zone {name!r}")
        users = [c for c in sorted(model.cells)
                 if model.cell(c).get("zone", "") == name]
        if users and not force:
            raise OpError(
                f"zone {name!r} is used by {len(users)} cells "
                f"(pass force=True to delete it anyway)")
        model._touch_zones()
        del model._zones[name]
        return {"deleted": name, "unset_cells": len(users) if force else 0}


@op
def set_zone_color(model, name, key, value, index=None):
    """Convenience setter for one zone color.

    Args:
        name: zone name.
        key: rock/cap (ramp, needs index), accent/fog_top/fog_bottom/
            ambient (single RGB).
        value: [r, g, b].
        index: 0..7 for ramps; must be None for single colors.
    """
    with model.transaction("set_zone_color"):
        name = _require_zone_name(name)
        if name not in model._zones:
            raise OpError(f"no zone {name!r}")
        zone = model._zones[name]
        if key in ("rock", "cap"):
            if index is None:
                raise OpError(
                    f"zone {name!r} {key} is a ramp; pass index 0..7")
            index = int(index)
            if not 0 <= index < 8:
                raise OpError(
                    f"zone {name!r} {key} index {index} out of range 0..7")
            rgb = _validate_rgb(value, f"zone {name!r} {key}[{index}]")
            model._touch_zones()
            zone[key][index] = rgb
        elif key in ("accent", "fog_top", "fog_bottom", "ambient"):
            if index is not None:
                raise OpError(
                    f"zone {name!r} {key} is a single color; index must "
                    f"be None")
            rgb = _validate_rgb(value, f"zone {name!r} {key}")
            model._touch_zones()
            zone[key] = rgb
        else:
            raise OpError(
                f"unknown zone color key {key!r} (expected rock, cap, "
                f"accent, fog_top, fog_bottom or ambient)")
        return {"zone": name, "key": key, "index": index}


# =========================================================================
# Palettes
# =========================================================================


@op
def create_palette(model, name, base="default", display_name=None):
    """Create a new palette by copying an existing one (name changed).

    Args:
        name: new palette name, must match ^[a-z0-9_]+$.
        base: palette to copy (default "default").
        display_name: optional display title override.
    """
    with model.transaction("create_palette"):
        name = _require_palette_name(name)
        if name in model._palettes:
            raise OpError(f"palette {name!r} already exists")
        if base not in model._palettes:
            raise OpError(f"base palette {base!r} not found")
        new = copy.deepcopy(model._palettes[base])
        new["name"] = name
        if display_name is not None:
            new["display_name"] = display_name
        model._touch_palette(name)
        model._palettes[name] = new
        return {"created": name}


@op
def set_palette_data(model, name, data):
    """Replace a palette's JSON wholesale (raw editor), validated.

    Args:
        name: palette name.
        data: palette dict; must include name/display_name/tile_px/build
            (with colors rock/cap/accent)/textures. Stored under `name`.
    """
    with model.transaction("set_palette_data"):
        if not isinstance(data, dict):
            raise OpError("palette data must be a dict")
        new = copy.deepcopy(data)
        missing = [k for k in ("display_name", "tile_px", "build",
                               "textures") if k not in new]
        if missing:
            raise OpError(f"palette {name!r} data missing keys: {missing}")
        build = new.get("build")
        if not isinstance(build, dict) or "colors" not in build:
            raise OpError(f"palette {name!r} data needs build.colors")
        _validate_colors(build["colors"], name)
        new["name"] = name
        model._touch_palette(name)
        model._palettes[name] = new
        return {"replaced": name}


@op
def set_palette_colors(model, name, colors):
    """Replace a palette's build.colors (rock/cap ramps + accent).

    Args:
        name: palette name.
        colors: {"rock": 8 RGB, "cap": 8 RGB, "accent": RGB}.
    """
    with model.transaction("set_palette_colors"):
        _require_palette(model, name)
        if not isinstance(colors, dict):
            raise OpError("colors must be a dict")
        _validate_colors(colors, name)
        build = model._palettes[name].get("build")
        if not isinstance(build, dict):
            raise OpError(f"palette {name!r} has no build section")
        model._touch_palette(name)
        build["colors"] = copy.deepcopy(colors)
        return {"replaced": name}


@op
def set_palette_color(model, name, ramp, index, rgb):
    """Set one swatch of a palette color ramp.

    Args:
        name: palette name.
        ramp: "rock" or "cap".
        index: 0..7.
        rgb: [r, g, b].
    """
    with model.transaction("set_palette_color"):
        _require_palette(model, name)
        if ramp not in ("rock", "cap"):
            raise OpError(f"palette ramp must be 'rock' or 'cap', "
                          f"got {ramp!r}")
        index = int(index)
        if not 0 <= index < 8:
            raise OpError(f"ramp index {index} out of range 0..7")
        rgb = _validate_rgb(rgb, f"palette {name!r} {ramp}[{index}]")
        colors = model._palettes[name].get("build", {}).get("colors")
        if not colors or ramp not in colors:
            raise OpError(f"palette {name!r} has no {ramp} color ramp")
        model._touch_palette(name)
        colors[ramp][index] = rgb
        return {"ramp": ramp, "index": index}


@op
def rename_palette(model, old, new):
    """Rename a palette, re-pointing every tile that uses it.

    Args:
        old: existing palette name.
        new: replacement name (^[a-z0-9_]+$). On save, textures/<old>
            is renamed to textures/<new> if it exists.
    """
    with model.transaction("rename_palette"):
        if old not in model._palettes:
            raise OpError(f"no palette {old!r}")
        new = _require_palette_name(new)
        if old == new:
            return {"renamed": False}
        if new in model._palettes:
            raise OpError(f"palette {new!r} already exists")
        model._touch_texture_renames()
        model._touch_palette(old)
        model._touch_palette(new)
        users = [c for c in sorted(model.cells)
                 if any(t.get("palette") == old
                        for row in model.cell(c)["tiles"] for t in row)]
        for c in users:
            model._touch_cell(c)
        data = model._palettes.pop(old)
        data["name"] = new
        model._palettes[new] = data
        model._pending_texture_renames.append((old, new))
        for c in users:
            for row in model.cell(c)["tiles"]:
                for t in row:
                    if t.get("palette") == old:
                        t["palette"] = new
        return {"renamed": True, "old": old, "new": new,
                "tiles_updated": len(users)}


@op
def delete_palette(model, name, replace_with=None):
    """Delete a palette; used palettes need replace_with.

    Args:
        name: palette name.
        replace_with: optional palette to re-point using tiles to; error
            listing usage count when the palette is used and this is
            missing.
    """
    with model.transaction("delete_palette"):
        _require_palette(model, name)
        if replace_with is not None:
            _require_palette(model, replace_with)
        users = [c for c in sorted(model.cells)
                 if any(t.get("palette") == name
                        for row in model.cell(c)["tiles"] for t in row)]
        if users and replace_with is None:
            raise OpError(
                f"palette {name!r} is used by {len(users)} cells; pass "
                f"replace_with to delete it")
        model._touch_palette(name)
        for c in users:
            model._touch_cell(c)
        if replace_with:
            for c in users:
                for row in model.cell(c)["tiles"]:
                    for t in row:
                        if t.get("palette") == name:
                            t["palette"] = replace_with
        del model._palettes[name]
        return {"deleted": name, "repointed_cells": len(users)}


# =========================================================================
# Sprites
# =========================================================================


@op
def set_sprite_data(model, name, data):
    """Replace a sprite source definition, validated against the build
    pipeline's rules.

    Args:
        name: sprite name.
        data: sprite source dict (name/colors/heights/voxel_px/anchor/
            animations with frames, as in assets/sprite_src/*.json).
    """
    with model.transaction("set_sprite_data"):
        if not isinstance(data, dict):
            raise OpError("sprite data must be a dict")
        try:
            from tools.build_sprites import _validate
        except ImportError:
            raise OpError(
                "sprite validation helper not importable "
                "(tools.build_sprites)") from None
        spec = copy.deepcopy(data)
        try:
            _validate(spec)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OpError(f"invalid sprite data for {name!r}: {exc}") from None
        spec["name"] = name
        model._touch_sprite(name)
        model._sprites[name] = spec
        return {"replaced": name}


@op
def delete_sprite(model, name):
    """Delete a sprite source definition.

    Args:
        name: sprite name.
    """
    with model.transaction("delete_sprite"):
        if name not in model._sprites:
            raise OpError(f"no sprite {name!r}")
        model._touch_sprite(name)
        del model._sprites[name]
        return {"deleted": name}


# =========================================================================
# Project manifest
# =========================================================================


@op
def set_manifest_value(model, key, value):
    """Set one project manifest value.

    Args:
        key: manifest key. "format" is protected; "paths" must be a
            dict of known layout keys and is merged into the existing
            paths.
        value: new value.
    """
    with model.transaction("set_manifest_value"):
        if key == "format":
            raise OpError(
                "'format' is managed by the project format; it cannot "
                "be edited")
        if key == "paths":
            if not isinstance(value, dict):
                raise OpError("'paths' must be a dict of known layout keys")
            unknown = set(value) - set(DEFAULT_LAYOUT)
            if unknown:
                raise OpError(
                    f"unknown path keys: {sorted(unknown)} (expected one "
                    f"of {sorted(DEFAULT_LAYOUT)})")
            model._touch_manifest()
            model._manifest.setdefault("paths", {})
            model._manifest["paths"].update(copy.deepcopy(value))
        else:
            model._touch_manifest()
            model._manifest[key] = copy.deepcopy(value)
        return {"key": key}
