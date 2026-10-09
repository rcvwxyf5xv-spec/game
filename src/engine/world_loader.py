"""
Runtime loader that sits between the data layer (src/data/*) and the
engine. This is the ONLY place that knows how to go from "the player is
standing in grid cell (x, y)" to actual tile data to collide against.

Two responsibilities, kept distinct:
  1. Loading/caching individual Cells from disk, bounded in count (an
     LRU cache), so the authored world can grow arbitrarily large
     without the whole thing sitting in memory.
  2. Delegating to engine.room_assembler to flood-fill a group of
     contiguous cells (connected by `open` edges) into one playable
     AssembledRoom whenever the player enters a new cell.

Swapping in a mod/DLC/expansion, or a procedurally generated world, is
just a matter of pointing `WorldLoader` at a different world_index.json
(or a different `cells_dir`) -- nothing in game/physics code needs to
change.
"""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import List, Optional

from src.data.schema import Cell, GridCoord, WorldIndex
from src.data.serialize import load_cell, load_world_index
from src.engine.room_assembler import AssembledRoom, assemble


class WorldLoader:
    def __init__(
        self,
        world_index_path: Path,
        cells_dir: Path,
        cache_size: int = 16,
    ) -> None:
        self.world_index_path = world_index_path
        self.cells_dir = cells_dir
        self.cache_size = cache_size
        self.index: WorldIndex = load_world_index(world_index_path)
        # LRU cache of individual loaded Cell objects, keyed by coord.
        # Bounding this is what proves cells are not all kept in memory
        # at once: loading cells beyond cache_size evicts the oldest.
        self._cache: "OrderedDict[GridCoord, Cell]" = OrderedDict()

    def cell_exists(self, coord: GridCoord) -> bool:
        return coord in self.index.cell_files

    def loaded_cell_coords(self) -> List[GridCoord]:
        return list(self._cache.keys())

    def get_cell(self, coord: GridCoord) -> Optional[Cell]:
        if coord in self._cache:
            self._cache.move_to_end(coord)
            return self._cache[coord]

        filename = self.index.cell_files.get(coord)
        if filename is None:
            return None

        cell = load_cell(self.cells_dir / filename)
        self._cache[coord] = cell
        self._cache.move_to_end(coord)
        if len(self._cache) > self.cache_size:
            evicted_coord, _ = self._cache.popitem(last=False)
            print(f"[world_loader] evicted cell {evicted_coord} from memory")
        return cell

    def assemble_from(self, coord: GridCoord) -> AssembledRoom:
        """Load `coord` and flood-fill its contiguous open-edge group into
        one AssembledRoom. This is the sole entry point game_world.py uses
        to turn 'the player is at this cell' into playable tile data."""
        return assemble(coord, self.get_cell)

    def start_cell_coord(self) -> GridCoord:
        return self.index.start_cell

    def start_assembled_room(self) -> AssembledRoom:
        return self.assemble_from(self.index.start_cell)
