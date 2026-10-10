"""ProjectModel: the in-memory mirror of one project's data.

The model loads a project the way the engine sees it (world index, every
cell canonicalised through cell_to_dict(cell_from_dict(raw)) into plain
dicts, palettes, zones, sprite sources, manifest) and tracks what has
been edited with respect to what is on disk. All edits go through the
ops in maker.core.ops, which run inside model.transaction() so undo/redo
and listeners behave identically for the editor window, the JSON command
line and the Python API.

Read-only accessors (cell(), cells, palettes, ...) return live dicts:
callers must not mutate them outside an op/transaction.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from types import MappingProxyType
from typing import Dict, List, Optional, Tuple

from src.data.schema import Cell, WorldIndex
from src.data.serialize import (
    cell_from_dict,
    cell_to_dict,
    save_cell,
    save_world_index,
)
from src.engine.room_assembler import AssembledRoom, assemble
from src.project import Project, active, invalidate, set_zone_override

from maker.core.history import ChangeSet, History


def _coord(value) -> Tuple[int, int]:
    """Normalise a [x, y] list or (x, y) tuple into a tuple."""
    return (int(value[0]), int(value[1]))


class ProjectModel:
    """One open project: loaded data + dirty tracking + save/undo/redo."""

    def __init__(self, project: Project) -> None:
        self.project = project
        # Live data (canonical raw dicts for cells; plain dicts otherwise).
        self._cells: Dict[Tuple[int, int], dict] = {}
        self._cell_objects: Dict[Tuple[int, int], Cell] = {}
        self._palettes: Dict[str, dict] = {}
        self._zones: Dict[str, dict] = {}
        self._sprites: Dict[str, dict] = {}
        self._manifest: dict = {}
        self._start_cell: Optional[Tuple[int, int]] = None
        # coord -> filename as it existed when loaded / last saved; new
        # coords get the default f"{x}_{y}.json" at save time.
        self._index_files: Dict[Tuple[int, int], str] = {}
        # Save-time texture directory renames queued by rename_palette.
        self._pending_texture_renames: List[Tuple[str, str]] = []

        # On-disk reference state (deep copies of what save() last wrote).
        self._saved_cells: Dict[Tuple[int, int], dict] = {}
        self._saved_palettes: Dict[str, dict] = {}
        self._saved_sprites: Dict[str, dict] = {}
        self._saved_zones: dict = {}
        self._saved_start_cell: Optional[Tuple[int, int]] = None
        self._saved_manifest: dict = {}

        # Dirty state: what differs from the on-disk reference above.
        self._changed_cells = set()
        self._deleted_cells = set()
        self._changed_palettes = set()
        self._deleted_palettes = set()
        self._changed_sprites = set()
        self._deleted_sprites = set()
        self._index_dirty = False
        self._zones_dirty = False
        self._manifest_dirty = False

        self._history = History(self)
        self._listeners: List[callable] = []
        self._cells_view = MappingProxyType(self._cells)

    # -- loading ---------------------------------------------------------

    @classmethod
    def open(cls, root) -> "ProjectModel":
        """Load the project at `root` (a directory or Path) into a model."""
        model = cls(Project.load(root))
        model._load()
        return model

    def _load(self) -> None:
        p = self.project
        raw_index = json.loads(p.world_index_path.read_text())
        self._start_cell = tuple(raw_index["start_cell"])
        for key, filename in raw_index["cells"].items():
            x_str, y_str = key.split(",")
            self._index_files[(int(x_str), int(y_str))] = filename
        for coord in sorted(self._index_files):
            path = p.cells_dir / self._index_files[coord]
            if not path.exists():
                raise ValueError(
                    f"{p.world_index_path} lists cell {coord} at "
                    f"{path.name}, but no such file exists")
            raw = json.loads(path.read_text())
            self._cells[coord] = cell_to_dict(cell_from_dict(raw))
        for path in sorted(p.palettes_dir.glob("*.json")):
            data = json.loads(path.read_text())
            name = data.get("name", path.stem)
            self._palettes[name] = data
        try:
            self._zones = json.loads(p.zones_path.read_text())
        except OSError:
            self._zones = {}
        for path in sorted(p.sprite_src_dir.glob("*.json")):
            data = json.loads(path.read_text())
            name = data.get("name", path.stem)
            self._sprites[name] = data
        self._manifest = copy.deepcopy(p.manifest)
        # Refresh the saved reference: nothing is dirty on a fresh open.
        self._refresh_saved()

    # -- accessors -------------------------------------------------------

    @property
    def cells(self):
        """Read-only view over raw cell dicts keyed by (x, y) tuple.

        The dict values are the live cell dicts: callers must not mutate
        them outside an op/transaction."""
        return self._cells_view

    def cell(self, coord) -> Optional[dict]:
        """The live raw dict for a cell, or None. Callers must not mutate
        it outside an op/transaction."""
        return self._cells.get(_coord(coord))

    def has_cell(self, coord) -> bool:
        return _coord(coord) in self._cells

    def cell_coords(self) -> List[Tuple[int, int]]:
        """All authored cell coords, sorted."""
        return sorted(self._cells)

    @property
    def palettes(self) -> dict:
        """name -> palette dict. Live; do not mutate outside ops."""
        return self._palettes

    @property
    def zones(self) -> dict:
        """zone name -> zone data. Live; do not mutate outside ops."""
        return self._zones

    @property
    def sprites(self) -> dict:
        """name -> sprite source dict. Live; do not mutate outside ops."""
        return self._sprites

    @property
    def manifest(self) -> dict:
        """The project manifest (project.json content). Live; do not
        mutate outside ops."""
        return self._manifest

    @property
    def start_cell(self) -> Optional[Tuple[int, int]]:
        return self._start_cell

    def cell_object(self, coord) -> Optional[Cell]:
        """The schema.Cell for a coord, cached (dropped when that cell
        changes)."""
        coord = _coord(coord)
        if coord not in self._cells:
            self._cell_objects.pop(coord, None)
            return None
        cached = self._cell_objects.get(coord)
        if cached is None:
            cached = cell_from_dict(self._cells[coord])
            self._cell_objects[coord] = cached
        return cached

    def assemble(self, coord) -> AssembledRoom:
        """Flood-fill the room containing `coord` (engine semantics)."""
        return assemble(_coord(coord), self.cell_object)

    def room_cells(self, coord) -> List[Tuple[int, int]]:
        """Coords of every cell in the same room as `coord` (open edges
        only), in flood-fill discovery order."""
        return list(self.assemble(coord).cells)

    def bounds(self) -> Optional[Tuple[int, int, int, int]]:
        """(minx, miny, maxx, maxy) over all cells, or None if empty."""
        if not self._cells:
            return None
        xs = [c[0] for c in self._cells]
        ys = [c[1] for c in self._cells]
        return (min(xs), min(ys), max(xs), max(ys))

    def palette_usage(self) -> Dict[str, int]:
        """Count of tiles labelled with each palette ("" skipped)."""
        counts: Dict[str, int] = {}
        for d in self._cells.values():
            for row in d["tiles"]:
                for tile in row:
                    pal = tile.get("palette", "")
                    if pal:
                        counts[pal] = counts.get(pal, 0) + 1
        return counts

    def zone_names(self) -> List[str]:
        """Union of zones.json keys and zones used by cells, sorted."""
        names = set(self._zones)
        for d in self._cells.values():
            zone = d.get("zone", "")
            if zone:
                names.add(zone)
        return sorted(names)

    # -- dirty state -----------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        """True if anything has been edited since open/last save."""
        return bool(
            self._changed_cells or self._deleted_cells
            or self._changed_palettes or self._deleted_palettes
            or self._changed_sprites or self._deleted_sprites
            or self._index_dirty or self._zones_dirty
            or self._manifest_dirty
        )

    # -- transactions / undo / redo --------------------------------------

    def transaction(self, label: str):
        """Context manager: all mutations inside run as one undoable
        step; an exception rolls everything back and re-raises."""
        return self._history.transaction(label)

    def _touch_cell(self, coord) -> None:
        self._history.touch("cell", _coord(coord))

    def _touch_palette(self, name: str) -> None:
        self._history.touch("palette", name)

    def _touch_sprite(self, name: str) -> None:
        self._history.touch("sprite", name)

    def _touch_zones(self) -> None:
        self._history.touch("zones")

    def _touch_index(self) -> None:
        """Snapshot start cell + set of coords for the current step."""
        self._history.touch("index")

    def _touch_manifest(self) -> None:
        self._history.touch("manifest")

    def _touch_texture_renames(self) -> None:
        self._history.touch("texture_renames")

    def _snapshot(self, kind: str, key):
        """Deep-copy pre/post state of one item (defined by kind+key)."""
        if kind == "cell":
            d = self._cells.get(key)
            return None if d is None else copy.deepcopy(d)
        if kind == "palette":
            d = self._palettes.get(key)
            return None if d is None else copy.deepcopy(d)
        if kind == "sprite":
            d = self._sprites.get(key)
            return None if d is None else copy.deepcopy(d)
        if kind == "zones":
            return copy.deepcopy(self._zones)
        if kind == "index":
            return {"start_cell": self._start_cell,
                    "coords": frozenset(self._cells)}
        if kind == "manifest":
            return copy.deepcopy(self._manifest)
        if kind == "texture_renames":
            return list(self._pending_texture_renames)
        raise KeyError(f"unknown snapshot kind {kind!r}")

    def _restore(self, snapshots: dict) -> None:
        """Apply stored pre/post images (undo/redo/rollback)."""
        for kind, items in snapshots.items():
            for key, value in items.items():
                if kind == "cell":
                    if value is None:
                        self._cells.pop(key, None)
                    else:
                        self._cells[key] = copy.deepcopy(value)
                    self._cell_objects.pop(key, None)
                elif kind == "palette":
                    if value is None:
                        self._palettes.pop(key, None)
                    else:
                        self._palettes[key] = copy.deepcopy(value)
                elif kind == "sprite":
                    if value is None:
                        self._sprites.pop(key, None)
                    else:
                        self._sprites[key] = copy.deepcopy(value)
                elif kind == "zones":
                    self._zones = copy.deepcopy(value)
                elif kind == "index":
                    self._start_cell = value["start_cell"]
                elif kind == "manifest":
                    self._manifest = copy.deepcopy(value)
                elif kind == "texture_renames":
                    self._pending_texture_renames = list(value)

    def _apply_changes(self, changes: ChangeSet) -> None:
        """Commit-time/undo/redo bookkeeping: re-derive dirty state from
        the changed items, fire listeners, refresh zone preview."""
        if changes.cells:
            # a move changes two coords under one item key, so scan the
            # whole cell set rather than per-key recomputation; drop the
            # schema.Cell cache too (it lazily rebuilds on demand).
            self._rebuild_cell_dirty()
            self._cell_objects.clear()
        for key in changes.palettes:
            self._recompute_palette_dirty(key)
        for key in changes.sprites:
            self._recompute_sprite_dirty(key)
        if changes.index:
            self._recompute_index_dirty()
        if changes.zones:
            self._recompute_zones_dirty()
            self.set_zone_preview()
        if changes.manifest:
            self._recompute_manifest_dirty()
        delivered = ChangeSet(
            cells=set(changes.cells), index=changes.index,
            zones=changes.zones, palettes=set(changes.palettes),
            sprites=set(changes.sprites), manifest=changes.manifest,
            label=changes.label,
        )
        for fn in list(self._listeners):
            fn(delivered)

    def undo(self) -> Optional[str]:
        return self._history.undo()

    def redo(self) -> Optional[str]:
        return self._history.redo()

    def history_mark(self) -> int:
        """Undo-stack position; pass to coalesce_history() later."""
        return self._history.mark()

    def coalesce_history(self, mark: int, label: Optional[str] = None) -> bool:
        """Merge all undo steps since `mark` into one (one drag = one
        undo). No listeners fire: the data is unchanged."""
        return self._history.coalesce_since(mark, label)

    @property
    def can_undo(self) -> bool:
        return self._history.can_undo

    @property
    def can_redo(self) -> bool:
        return self._history.can_redo

    @property
    def undo_label(self) -> Optional[str]:
        return self._history.undo_label

    @property
    def redo_label(self) -> Optional[str]:
        return self._history.redo_label

    def add_listener(self, fn) -> None:
        """Register fn(ChangeSet); called after every committed
        transaction / undo / redo."""
        self._listeners.append(fn)

    def set_zone_preview(self) -> None:
        """Push zones into the renderer if this model's project is the
        active project, so renders see unsaved zone edits."""
        if self.project is active():
            set_zone_override(self._zones)

    # -- dirty recomputation ----------------------------------------------

    def _rebuild_cell_dirty(self) -> None:
        """Full scan: marks every coord whose cur vs saved state differs.
        Used after any step touching cells, since one item key (a moved
        cell) can change two coords at once."""
        changed = set()
        deleted = set()
        for coord in set(self._cells) | set(self._saved_cells):
            cur = self._cells.get(coord)
            saved = self._saved_cells.get(coord)
            if cur == saved:
                continue
            if cur is None:
                deleted.add(coord)
            else:
                changed.add(coord)
        self._changed_cells = changed
        self._deleted_cells = deleted

    def _recompute_cell_dirty(self, coord) -> None:
        cur = self._cells.get(coord)
        saved = self._saved_cells.get(coord)
        if cur == saved:
            self._changed_cells.discard(coord)
            self._deleted_cells.discard(coord)
        elif cur is None:
            self._changed_cells.discard(coord)
            self._deleted_cells.add(coord)
        else:
            self._deleted_cells.discard(coord)
            self._changed_cells.add(coord)

    def _recompute_palette_dirty(self, name: str) -> None:
        cur = self._palettes.get(name)
        saved = self._saved_palettes.get(name)
        if cur == saved:
            self._changed_palettes.discard(name)
            self._deleted_palettes.discard(name)
        elif cur is None:
            self._changed_palettes.discard(name)
            self._deleted_palettes.add(name)
        else:
            self._deleted_palettes.discard(name)
            self._changed_palettes.add(name)

    def _recompute_sprite_dirty(self, name: str) -> None:
        cur = self._sprites.get(name)
        saved = self._saved_sprites.get(name)
        if cur == saved:
            self._changed_sprites.discard(name)
            self._deleted_sprites.discard(name)
        elif cur is None:
            self._changed_sprites.discard(name)
            self._deleted_sprites.add(name)
        else:
            self._deleted_sprites.discard(name)
            self._changed_sprites.add(name)

    def _recompute_index_dirty(self) -> None:
        cur = (self._start_cell, frozenset(self._cells))
        saved = (self._saved_start_cell, frozenset(self._saved_cells))
        self._index_dirty = cur != saved

    def _recompute_zones_dirty(self) -> None:
        self._zones_dirty = self._zones != self._saved_zones

    def _recompute_manifest_dirty(self) -> None:
        self._manifest_dirty = self._manifest != self._saved_manifest

    def mark_palette_saved(self, name: str) -> None:
        """Record that `name`'s palette JSON is already identical on disk.

        Used by maker.core.assets immediate file writes (texture rebuilds
        / imports): the palette file is written directly there, so it must
        not stay dirty.
        """
        data = self._palettes.get(name)
        if data is None:
            return
        self._saved_palettes[name] = copy.deepcopy(data)
        self._changed_palettes.discard(name)
        self._deleted_palettes.discard(name)

    def mark_sprite_saved(self, name: str) -> None:
        """Record that `name`'s sprite source JSON is already on disk (see
        mark_palette_saved; used by maker.core.assets.build_sprites)."""
        data = self._sprites.get(name)
        if data is None:
            return
        self._saved_sprites[name] = copy.deepcopy(data)
        self._changed_sprites.discard(name)
        self._deleted_sprites.discard(name)

    # -- saving -----------------------------------------------------------

    def save(self) -> List[Path]:
        """Write everything that changed, atomically; returns written
        paths. Clears dirty state and refreshes the saved reference."""
        written: List[Path] = []
        p = self.project

        # Resolve a filename for every current cell: existing ones keep
        # their loaded name, new ones get the default f"{x}_{y}.json".
        for coord in sorted(self._cells):
            self._index_files.setdefault(
                coord, f"{coord[0]}_{coord[1]}.json")

        cells_dir = p.cells_dir
        for coord in sorted(self._changed_cells):
            dest = cells_dir / self._index_files[coord]
            self._atomic_cell_write(cell_from_dict(self._cells[coord]), dest)
            written.append(dest)
        for coord in sorted(self._deleted_cells):
            fname = self._index_files.pop(coord, None)
            if fname:
                path = cells_dir / fname
                if path.exists():
                    path.unlink()
                    written.append(path)

        palettes_dir = p.palettes_dir
        for name in sorted(self._changed_palettes):
            dest = palettes_dir / f"{name}.json"
            self._atomic_json_write(
                dest, json.dumps(self._palettes[name], indent=2) + "\n")
            written.append(dest)
        for name in sorted(self._deleted_palettes):
            path = palettes_dir / f"{name}.json"
            if path.exists():
                path.unlink()
                written.append(path)

        for old, new in self._pending_texture_renames:
            src_dir = p.textures_dir / old
            dst_dir = p.textures_dir / new
            if src_dir.exists() and not dst_dir.exists():
                os.rename(str(src_dir), str(dst_dir))
                written.append(dst_dir)

        if self._zones_dirty:
            dest = p.zones_path
            self._atomic_json_write(
                dest, json.dumps(self._zones, indent=2) + "\n")
            written.append(dest)

        sprites_dir = p.sprite_src_dir
        for name in sorted(self._changed_sprites):
            dest = sprites_dir / f"{name}.json"
            self._atomic_json_write(
                dest, json.dumps(self._sprites[name], indent=2) + "\n")
            written.append(dest)
        for name in sorted(self._deleted_sprites):
            path = sprites_dir / f"{name}.json"
            if path.exists():
                path.unlink()
                written.append(path)

        if self._manifest_dirty:
            dest = p.root / "project.json"
            self._atomic_json_write(
                dest, json.dumps(self._manifest, indent=2) + "\n")
            written.append(dest)

        if self._index_dirty:
            cell_files = {
                coord: self._index_files[coord]
                for coord in sorted(self._cells)
            }
            index = WorldIndex(cell_files=cell_files,
                               start_cell=self._start_cell)
            self._atomic_world_index_write(index, p.world_index_path)
            written.append(p.world_index_path)

        if written and p.root == active().root:
            invalidate()
        self._refresh_saved()
        return written

    def _refresh_saved(self) -> None:
        """Record the just-written / just-loaded state as the on-disk
        reference and clear all dirty flags."""
        self._saved_cells = {
            k: copy.deepcopy(v) for k, v in self._cells.items()}
        self._saved_palettes = {
            k: copy.deepcopy(v) for k, v in self._palettes.items()}
        self._saved_sprites = {
            k: copy.deepcopy(v) for k, v in self._sprites.items()}
        self._saved_zones = copy.deepcopy(self._zones)
        self._saved_start_cell = self._start_cell
        self._saved_manifest = copy.deepcopy(self._manifest)
        self._pending_texture_renames = []
        self._changed_cells.clear()
        self._deleted_cells.clear()
        self._changed_palettes.clear()
        self._deleted_palettes.clear()
        self._changed_sprites.clear()
        self._deleted_sprites.clear()
        self._index_dirty = False
        self._zones_dirty = False
        self._manifest_dirty = False

    # -- atomic writes ----------------------------------------------------

    @staticmethod
    def _atomic(tmp_writer: callable, dest: Path) -> None:
        """Write via tmp_writer(tmp_path) then os.replace(tmp, dest)."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(dest.parent), prefix=".maker_tmp_", suffix=".json")
        os.close(fd)
        try:
            tmp_writer(Path(tmp))
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _atomic_cell_write(self, cell: Cell, dest: Path) -> None:
        self._atomic(lambda tmp: save_cell(cell, tmp), dest)

    def _atomic_json_write(self, dest: Path, text: str) -> None:
        self._atomic(lambda tmp: tmp.write_text(text), dest)

    def _atomic_world_index_write(self, index: WorldIndex,
                                  dest: Path) -> None:
        self._atomic(lambda tmp: save_world_index(index, tmp), dest)
