"""
Pure (de)serialization between the dataclasses in schema.py and plain
JSON-friendly dicts. Kept separate from schema.py so that schema.py stays
a clean data model, and separate from world_loader.py so that tools
(procedural generators, a future level editor, mod loaders) can produce
world data without importing any file-system/caching logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from src.data.schema import (
    ALL_DIRECTIONS,
    TILE_KINDS,
    Cell,
    Edge,
    Tile,
    WorldIndex,
    tile_from_legacy_id,
)


def edge_to_dict(edge: Edge) -> Dict[str, Any]:
    data: Dict[str, Any] = {"type": edge.edge_type}
    if edge.edge_type == "door":
        data["door_id"] = edge.door_id
        data["target_cell"] = list(edge.target_cell) if edge.target_cell else None
        data["target_edge"] = edge.target_edge
    return data


def edge_from_dict(direction: str, data: Dict[str, Any]) -> Edge:
    target_cell = data.get("target_cell")
    return Edge(
        direction=direction,
        edge_type=data.get("type", "wall"),
        door_id=data.get("door_id", ""),
        target_cell=tuple(target_cell) if target_cell else None,
        target_edge=data.get("target_edge", ""),
    )


def tile_to_dict(tile: Tile) -> Dict[str, Any]:
    if isinstance(tile, int):  # legacy generator output (plain tile ids)
        tile = tile_from_legacy_id(tile)
    data: Dict[str, Any] = {"kind": tile.kind}
    if tile.palette:
        data["palette"] = tile.palette
    return data


def tile_from_dict(data) -> Tile:
    """Build a Tile from a JSON node: either a per-tile dict or a legacy
    integer tile id (old cell files)."""
    if isinstance(data, int):
        return tile_from_legacy_id(data)
    return Tile(
        kind=data.get("kind", "empty"),
        palette=data.get("palette", ""),
    )


def cell_to_dict(cell: Cell) -> Dict[str, Any]:
    return {
        "coord": list(cell.coord),
        "zone": cell.zone,
        "tiles": [[tile_to_dict(tile) for tile in row] for row in cell.tiles],
        "edges": {
            direction: edge_to_dict(cell.edge(direction))
            for direction in ALL_DIRECTIONS
            if direction in cell.edges
        },
        "entities": [dict(e) for e in cell.entities],
        "background_color": list(cell.background_color),
    }


def cell_from_dict(data: Dict[str, Any]) -> Cell:
    edges_data = data.get("edges", {})
    edges = {
        direction: edge_from_dict(direction, edges_data[direction])
        for direction in ALL_DIRECTIONS
        if direction in edges_data
    }
    coord = tuple(data["coord"])
    tiles = []
    for y, row in enumerate(data["tiles"]):
        tile_row = []
        for x, raw in enumerate(row):
            tile = tile_from_dict(raw)
            if tile.kind not in TILE_KINDS:
                raise ValueError(
                    f"Cell {coord}, tile ({x}, {y}): unknown tile kind "
                    f"{tile.kind!r} (expected one of {TILE_KINDS})"
                )
            tile_row.append(tile)
        tiles.append(tuple(tile_row))
    return Cell(
        coord=coord,
        tiles=tuple(tiles),
        edges=edges,
        zone=data.get("zone", ""),
        entities=tuple(data.get("entities", [])),
        background_color=tuple(data.get("background_color", [18, 18, 28])),
    )


def world_index_to_dict(index: WorldIndex) -> Dict[str, Any]:
    return {
        "start_cell": list(index.start_cell),
        "cells": {
            f"{x},{y}": filename for (x, y), filename in index.cell_files.items()
        },
    }


def world_index_from_dict(data: Dict[str, Any]) -> WorldIndex:
    cell_files = {}
    for key, filename in data["cells"].items():
        x_str, y_str = key.split(",")
        cell_files[(int(x_str), int(y_str))] = filename
    return WorldIndex(
        cell_files=cell_files,
        start_cell=tuple(data["start_cell"]),
    )


def save_cell(cell: Cell, path: Path) -> None:
    """Write `cell` as JSON with each `tiles` row on a single line.

    Per-tile dicts would make plain `json.dumps(..., indent=2)` extremely
    verbose (~100 lines for one 16-tile row), so rows are serialized
    compactly and assembled by hand. Top-level keys stay indented; the
    output is still plain JSON that `load_cell`'s `json.loads` reads fine.
    Deterministic: built purely from dict insertion order.
    """
    data = cell_to_dict(cell)
    lines = ["{"]
    count = len(data)
    for idx, (key, value) in enumerate(data.items()):
        comma = "," if idx < count - 1 else ""
        if key == "tiles":
            lines.append('  "tiles": [')
            rows = value
            for row_idx, row in enumerate(rows):
                suffix = "," if row_idx < len(rows) - 1 else ""
                lines.append(
                    f"    {json.dumps(row, separators=(', ', ': '))}{suffix}"
                )
            lines.append(f"  ]{comma}")
        else:
            serialized = json.dumps(value, indent=2)
            serialized = serialized.replace("\n", "\n  ")
            lines.append(f'  "{key}": {serialized}{comma}')
    lines.append("}")
    path.write_text("\n".join(lines))


def load_cell(path: Path) -> Cell:
    return cell_from_dict(json.loads(path.read_text()))


def save_world_index(index: WorldIndex, path: Path) -> None:
    path.write_text(json.dumps(world_index_to_dict(index), indent=2))


def load_world_index(path: Path) -> WorldIndex:
    return world_index_from_dict(json.loads(path.read_text()))
