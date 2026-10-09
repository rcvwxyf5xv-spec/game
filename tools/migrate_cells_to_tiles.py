"""
One-off migration: rewrite every cell file in src/data/cells so each tile
is a per-tile dict carrying its collision kind plus a palette label picked
from the cell's zone.

Old files store each tile as a legacy integer id (see LEGACY_TILE_IDS);
serialize.load_cell already accepts thosechers. Empty tiles get no palette
(""); every non-empty tile gets the zone's palette (or "default" for
unmapped zones).

Idempotent: re-running it leaves the files byte-identical, because the
palette assignment is a pure function of (kind, zone).

Run with:  python -m tools.migrate_cells_to_tiles
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.data.schema import TILE_KIND_EMPTY, Cell, Tile  # noqa: E402
from src.data.serialize import load_cell, save_cell  # noqa: E402

CELLS_DIR = ROOT / "src" / "data" / "cells"

ZONE_PALETTES = {
    "Blue Brinstar": "blue_brinstar",
    "Green Brinstar": "green_brinstar",
    "Pink Brinstar": "pink_brinstar",
    "Red Brinstar": "red_brinstar",
    "Kraid's Lair": "kraids_lair",
}


def zone_palette(zone: str) -> str:
    return ZONE_PALETTES.get(zone, "default")


def main() -> None:
    converted = 0
    for path in sorted(CELLS_DIR.glob("*.json")):
        cell = load_cell(path)
        palette = zone_palette(cell.zone)
        new_tiles = tuple(
            tuple(
                Tile(kind=tile.kind, palette=palette if tile.kind != TILE_KIND_EMPTY else "")
                for tile in row
            )
            for row in cell.tiles
        )
        if new_tiles == cell.tiles:
            continue
        converted_cell = Cell(
            coord=cell.coord,
            tiles=new_tiles,
            edges=cell.edges,
            zone=cell.zone,
            entities=cell.entities,
            background_color=cell.background_color,
        )
        save_cell(converted_cell, path)
        converted += 1
    print(f"Converted {converted} cells to per-tile data")


if __name__ == "__main__":
    main()
