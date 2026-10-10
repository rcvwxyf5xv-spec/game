"""Rule checks over a ProjectModel, returning sorted Issues.

Severity levels: "error" (breaks the game if shipped), "warning"
(unusual or unpolished, usually fine to ship), "info" (cosmetic /
informational). Results are sorted errors -> warnings -> info, then by
cell.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from src.data.schema import (
    ALL_DIRECTIONS,
    DIRECTION_DELTA,
    OPPOSITE_DIRECTION,
    TILE_KINDS,
)
from src.engine.constants import (
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    MAX_ASSEMBLED_CELLS,
)

_NON_PASSABLE = ("solid", "crumble", "spring")
_RANK = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Issue:
    """One validation finding."""

    severity: str
    code: str
    message: str
    cell: Optional[Tuple[int, int]] = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "cell": list(self.cell) if self.cell is not None else None,
            "extra": self.extra,
        }


def _line_kinds(cell_data, direction):
    """The kinds along the boundary tile line for one side, or None when
    the grid is malformed (caller skips)."""
    tiles = cell_data.get("tiles")
    if not isinstance(tiles, list):
        return None
    if direction in ("east", "west"):
        if any(not isinstance(row, list) or len(row) != CELL_WIDTH_TILES
               for row in tiles):
            return None
        col = CELL_WIDTH_TILES - 1 if direction == "east" else 0
        return [tiles[y][col].get("kind") if isinstance(tiles[y][col], dict)
                else None for y in range(CELL_HEIGHT_TILES)]
    row = 0 if direction == "north" else CELL_HEIGHT_TILES - 1
    if len(tiles) <= row or not isinstance(tiles[row], list):
        return None
    out = []
    for x in range(CELL_WIDTH_TILES):
        t = tiles[row][x]
        out.append(t.get("kind") if isinstance(t, dict) else None)
    if len(tiles) != CELL_HEIGHT_TILES:
        return None
    return out


def validate(model) -> list:
    """Run every rule; returns a list of Issue sorted by severity then
    cell. Never raises on malformed data (malformed items are reported,
    not crashed on)."""
    issues = []
    cells = model.cells

    # -- errors ------------------------------------------------------------

    start = model.start_cell
    if start is None:
        issues.append(Issue(
            "error", "start_cell_missing", "no start cell defined", None))
    elif not model.has_cell(start):
        issues.append(Issue(
            "error", "start_cell_missing",
            f"start cell {tuple(start)} does not exist", None))

    spawns = []
    for coord in sorted(cells):
        for entity in cells[coord].get("entities", []):
            if entity.get("type") == "spawn_point":
                spawns.append(coord)
    if not spawns:
        issues.append(Issue(
            "error", "no_spawn_point",
            "no spawn_point entity anywhere in the world", None))
    elif len(spawns) > 1:
        issues.append(Issue(
            "error", "multiple_spawn_points",
            f"{len(spawns)} spawn_point entities: {spawns}", None))

    for coord in sorted(cells):
        data = cells[coord]
        tiles = data.get("tiles")
        if not isinstance(tiles, list) or len(tiles) != CELL_HEIGHT_TILES \
                or any(not isinstance(row, list)
                       or len(row) != CELL_WIDTH_TILES for row in tiles):
            issues.append(Issue(
                "error", "bad_dimensions",
                f"tile grid is not {CELL_WIDTH_TILES}x{CELL_HEIGHT_TILES} "
                f"(10 rows of 16)", coord))
            continue
        for ty, row in enumerate(tiles):
            for tx, tile in enumerate(row):
                kind = tile.get("kind") if isinstance(tile, dict) else None
                if kind not in TILE_KINDS:
                    issues.append(Issue(
                        "error", "unknown_tile_kind",
                        f"tile ({tx},{ty}) has unknown kind {kind!r}",
                        coord))

    # open edges: neighbour must exist and its opposite edge must be open
    for coord in sorted(cells):
        data = cells[coord]
        for direction in ALL_DIRECTIONS:
            edge = data.get("edges", {}).get(direction)
            if not edge or edge.get("type") != "open":
                continue
            dx, dy = DIRECTION_DELTA[direction]
            nbr = (coord[0] + dx, coord[1] + dy)
            if nbr not in cells:
                issues.append(Issue(
                    "error", "open_edge_missing_neighbor",
                    f"open {direction} edge has no cell at {nbr}", coord))
                continue
            opp = OPPOSITE_DIRECTION[direction]
            nedge = cells[nbr].get("edges", {}).get(opp)
            if not nedge or nedge.get("type") != "open":
                issues.append(Issue(
                    "error", "open_edge_unmatched",
                    f"open {direction} edge: {nbr} has no matching open "
                    f"{opp} edge back", coord))

    # doors: target exists and is a door back to this cell/edge
    for coord in sorted(cells):
        data = cells[coord]
        for direction in ALL_DIRECTIONS:
            edge = data.get("edges", {}).get(direction)
            if not edge or edge.get("type") != "door":
                continue
            door_id = edge.get("door_id")
            if not door_id:
                issues.append(Issue(
                    "error", "door_missing_id",
                    f"door {coord}/{direction} has no door_id", coord))
            target = edge.get("target_cell")
            te = edge.get("target_edge", "")
            if not target:
                issues.append(Issue(
                    "error", "door_target_missing",
                    f"door {coord}/{direction} has no target cell", coord))
                continue
            t = tuple(target)
            if t not in cells:
                issues.append(Issue(
                    "error", "door_target_missing",
                    f"door {coord}/{direction} targets missing cell {t}",
                    coord))
                continue
            if te not in ALL_DIRECTIONS:
                issues.append(Issue(
                    "error", "door_unmatched",
                    f"door {coord}/{direction} target edge {te!r} is not "
                    f"a direction", coord))
                continue
            ted = cells[t].get("edges", {}).get(te)
            ok = (ted and ted.get("type") == "door"
                  and tuple(ted.get("target_cell") or ()) == coord
                  and ted.get("target_edge") == direction)
            if not ok:
                issues.append(Issue(
                    "error", "door_unmatched",
                    f"door {coord}/{direction} points at {t}/{te}, but "
                    f"that edge is not a door back to "
                    f"{coord}/{direction} (the game crashes on this)",
                    coord))

    # -- warnings ----------------------------------------------------------

    for coord in sorted(cells):
        data = cells[coord]
        for direction in ALL_DIRECTIONS:
            edge = data.get("edges", {}).get(direction)
            if not edge or edge.get("type") == "wall":
                continue
            if edge.get("type") == "door":
                target = edge.get("target_cell")
                if target:
                    dx, dy = DIRECTION_DELTA[direction]
                    if tuple(target) != (coord[0] + dx, coord[1] + dy):
                        issues.append(Issue(
                            "warning", "door_teleport",
                            f"door {coord}/{direction} targets "
                            f"{tuple(target)}, which is not the cell "
                            f"directly {direction} (teleport door is "
                            f"allowed but unusual)", coord))
            kinds = _line_kinds(data, direction)
            if kinds is not None and all(
                    k in _NON_PASSABLE for k in kinds):
                issues.append(Issue(
                    "warning", "edge_blocked",
                    f"{edge.get('type')} {direction} edge's boundary "
                    f"tile line is entirely "
                    f"{sorted(set(kinds))} – the player cannot pass",
                    coord))

    door_id_counts = {}
    for coord in sorted(cells):
        for direction in ALL_DIRECTIONS:
            edge = cells[coord].get("edges", {}).get(direction)
            if edge and edge.get("type") == "door" and edge.get("door_id"):
                door_id_counts[edge["door_id"]] = (
                    door_id_counts.get(edge["door_id"], 0) + 1)
    for door_id, count in sorted(door_id_counts.items()):
        if count > 2:
            issues.append(Issue(
                "warning", "door_id_multiple",
                f"door_id {door_id!r} is used by {count} edges "
                f"(expected 2)", None,
                {"door_id": door_id, "edges": count}))

    known_types = _known_types(model)
    for coord in sorted(cells):
        data = cells[coord]
        for entity in data.get("entities", []):
            ex, ey = entity.get("tile_x"), entity.get("tile_y")
            if (not isinstance(ex, int) or not isinstance(ey, int)
                    or not (0 <= ex < CELL_WIDTH_TILES)
                    or not (0 <= ey < CELL_HEIGHT_TILES)):
                issues.append(Issue(
                    "warning", "entity_out_of_bounds",
                    f"entity {entity.get('type')!r} at tile "
                    f"({ex}, {ey}) is outside the cell", coord))
                continue
            if entity.get("type") not in known_types:
                issues.append(Issue(
                    "warning", "entity_unknown_type",
                    f"entity type {entity.get('type')!r} is not a known "
                    f"type", coord))
            tile = data["tiles"][ey][ex]
            if tile.get("kind") == "solid":
                issues.append(Issue(
                    "warning", "entity_in_solid",
                    f"entity {entity.get('type')!r} at tile "
                    f"({ex},{ey}) sits inside a solid tile", coord))

    # rooms larger than the assembler's flood-fill cap
    visited = set()
    for seed in sorted(cells):
        if seed in visited:
            continue
        group = []
        queue = [seed]
        visited.add(seed)
        while queue:
            c = queue.pop(0)
            group.append(c)
            for direction in ALL_DIRECTIONS:
                edge = cells[c].get("edges", {}).get(direction)
                if not edge or edge.get("type") != "open":
                    continue
                dx, dy = DIRECTION_DELTA[direction]
                nbr = (c[0] + dx, c[1] + dy)
                if nbr in cells and nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)
        if len(group) > MAX_ASSEMBLED_CELLS:
            issues.append(Issue(
                "warning", "room_too_large",
                f"open-edge room of {len(group)} cells exceeds "
                f"MAX_ASSEMBLED_CELLS={MAX_ASSEMBLED_CELLS}; slice it "
                f"with a wall+door pair", seed,
                {"cells": len(group)}))

    for coord in sorted(cells):
        data = cells[coord]
        for ty, row in enumerate(data.get("tiles", [])):
            for tile in row:
                pal = tile.get("palette") if isinstance(tile, dict) else ""
                if pal and pal not in model.palettes:
                    issues.append(Issue(
                        "warning", "unknown_palette",
                        f"tile palette {pal!r} is not a defined palette",
                        coord, {"palette": pal}))

    for name in sorted(model.palettes):
        palette = model.palettes[name]
        for kind, spec in (palette.get("textures") or {}).items():
            if not isinstance(spec, dict) or not spec.get("file"):
                continue
            texture = model.project.textures_dir / name / spec["file"]
            if not texture.exists():
                issues.append(Issue(
                    "warning", "palette_missing_texture",
                    f"palette {name!r} kind {kind!r} references "
                    f"{texture.relative_to(model.project.root)} which is "
                    f"missing", None,
                    {"palette": name, "kind": kind, "file": spec["file"]}))

    # -- info --------------------------------------------------------------

    for coord in sorted(cells):
        zone = cells[coord].get("zone", "")
        if zone and zone not in model.zones:
            issues.append(Issue(
                "info", "zone_undefined",
                f"cell zone {zone!r} is not defined in zones.json "
                f"(renders fall back to a built-in default)", coord,
                {"zone": zone}))

    usage = model.palette_usage()
    for name in sorted(model.palettes):
        if usage.get(name, 0) == 0:
            issues.append(Issue(
                "info", "palette_unused",
                f"palette {name!r} is not used by any tile", None,
                {"palette": name}))

    issues.sort(key=lambda i: (
        _RANK.get(i.severity, 3),
        i.cell if i.cell is not None else (),
        i.code,
        i.message,
    ))
    return issues


def _known_types(model) -> set:
    from maker.core.ops import entity_types
    return set(entity_types(model))
