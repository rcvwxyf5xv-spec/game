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

from src.data.schema import ALL_DIRECTIONS, Cell, Edge, WorldIndex


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


def cell_to_dict(cell: Cell) -> Dict[str, Any]:
    return {
        "coord": list(cell.coord),
        "zone": cell.zone,
        "tiles": [list(row) for row in cell.tiles],
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
    return Cell(
        coord=tuple(data["coord"]),
        tiles=tuple(tuple(row) for row in data["tiles"]),
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
    path.write_text(json.dumps(cell_to_dict(cell), indent=2))


def load_cell(path: Path) -> Cell:
    return cell_from_dict(json.loads(path.read_text()))


def save_world_index(index: WorldIndex, path: Path) -> None:
    path.write_text(json.dumps(world_index_to_dict(index), indent=2))


def load_world_index(path: Path) -> WorldIndex:
    return world_index_from_dict(json.loads(path.read_text()))
