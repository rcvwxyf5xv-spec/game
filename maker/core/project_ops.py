"""Creating, exporting and playtesting whole projects.

create_project builds a brand-new game folder on disk from a template
(no model involved); export_game bundles the fixed engine plus one
project's data into a self-contained playable folder; playtest_command
returns the argv used to launch the game for a project. None of these
change the engine's source.

The exported runtime is exactly the repo's src/ package (minus the demo
data) plus the project's world/palettes/textures/sprites/zones, so the
exported game plays identically to the in-repo demo.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from maker.core import validate as validator
from maker.core.errors import OpError
from maker.core.ops import DEFAULT_ENTITY_TYPES
from src.engine.constants import CELL_HEIGHT_TILES, CELL_WIDTH_TILES
from src.project import DEFAULT_LAYOUT, ENGINE_ROOT, Project

EXPORT_MARKER = ".metroidvania_export"
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


def _require_name(name) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise OpError(f"project name {name!r} must match ^[a-z0-9_]+$")
    return name


# ---------------------------------------------------------------------------
# create_project
# ---------------------------------------------------------------------------


def create_project(dest, name, title=None, template="starter",
                   source=None) -> Path:
    """Create a new project at `dest` with the default layout.

    `dest` must not exist or be an empty directory. Templates:
      * "starter": one walled cell at (0,0) with a spawn_point, palette
        "default", zone "Start", background [18,18,28]; palettes,
        textures, sprite sources, built sprites and zones are copied from
        `source` (default: the engine-root demo project) and a "Start"
        zone (from src.gfx DEFAULT_PALETTE) is added to zones.json;
      * "copy": the source project's world and all assets, re-laid out
        into the default layout.
    """
    _require_name(name)
    if template not in ("starter", "copy"):
        raise OpError(
            f"unknown template {template!r} (expected 'starter' or 'copy')")
    dest = Path(dest)
    if dest.exists():
        if not dest.is_dir() or any(dest.iterdir()):
            raise OpError(
                f"destination {dest} must not exist or be an empty directory")
    src = Project.load(Path(source) if source is not None else ENGINE_ROOT)
    dest.mkdir(parents=True, exist_ok=True)
    _write_project_json(dest, name, title)
    if template == "copy":
        _copy_world(src, dest)
    else:
        _write_starter_world(dest)
    _copy_assets(src, dest)
    if template == "starter":
        _add_start_zone(dest)
    return dest


def _write_project_json(dest: Path, name: str, title: Optional[str]) -> None:
    manifest = {
        "format": 1,
        "name": name,
        "title": title or name,
        "entity_types": list(DEFAULT_ENTITY_TYPES),
    }
    (dest / "project.json").write_text(
        json.dumps(manifest, indent=2) + "\n")


def _write_starter_world(dest: Path) -> None:
    world_dir = dest / "world"
    cells_dir = world_dir / "cells"
    cells_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for y in range(CELL_HEIGHT_TILES):
        row = []
        for x in range(CELL_WIDTH_TILES):
            border = (y == 0 or y == CELL_HEIGHT_TILES - 1
                      or x == 0 or x == CELL_WIDTH_TILES - 1)
            row.append({"kind": "solid", "palette": "default"}
                       if border else {"kind": "empty"})
        rows.append(row)
    cell = {
        "coord": [0, 0],
        "zone": "Start",
        "tiles": rows,
        "edges": {},
        "entities": [{"type": "spawn_point", "tile_x": 3, "tile_y": 8}],
        "background_color": [18, 18, 28],
    }
    (cells_dir / "0_0.json").write_text(json.dumps(cell, indent=2) + "\n")
    index = {"start_cell": [0, 0], "cells": {"0,0": "0_0.json"}}
    (world_dir / "world_index.json").write_text(
        json.dumps(index, indent=2) + "\n")


def _add_start_zone(dest: Path) -> None:
    from src.gfx.palette import DEFAULT_PALETTE

    def lists(value):
        if isinstance(value, (tuple, list)):
            return [lists(v) for v in value]
        return value

    zones_path = dest / DEFAULT_LAYOUT["zones"]
    zones = (json.loads(zones_path.read_text())
             if zones_path.exists() else {})
    zones["Start"] = {k: lists(v) for k, v in DEFAULT_PALETTE.items()}
    zones_path.write_text(json.dumps(zones, indent=2) + "\n")


def _copy_world(src: Project, dest: Path) -> None:
    world_dir = dest / "world"
    shutil.copytree(src.path("cells_dir"), world_dir / "cells")
    shutil.copy2(src.world_index_path, world_dir / "world_index.json")


def _copy_assets(src: Project, dest: Path) -> None:
    for key in ("palettes_dir", "textures_dir", "sprite_src_dir",
                "sprites_dir", "zones"):
        s = src.path(key)
        d = dest / DEFAULT_LAYOUT[key]
        if s.is_dir():
            shutil.copytree(s, d)
        elif s.exists():
            os.makedirs(d.parent, exist_ok=True)
            shutil.copy2(s, d)


# ---------------------------------------------------------------------------
# export_game
# ---------------------------------------------------------------------------


def export_game(model, out_dir, force=False, clean=True) -> dict:
    """Bundle the fixed engine + this project's data into out_dir.

    Refuses when the model has unsaved changes, or when validation finds
    errors (unless `force`). With `clean`, an existing out_dir is removed
    only if it carries the .metroidvania_export marker (a non-marked
    non-empty dir is an error). Returns {"out_dir", "files", "warnings"}.
    """
    if model.is_dirty:
        raise OpError(
            "project has unsaved changes; save it first (model.save() or "
            "run the CLI, which saves mutating commands automatically)")
    issues = validator.validate(model)
    errors = [i for i in issues if i.severity == "error"]
    if errors and not force:
        head = "\n".join(
            f"  {i.cell or 'world'} {i.code}: {i.message}"
            for i in errors[:10])
        more = "" if len(errors) <= 10 else f"\n  ... and {len(errors) - 10} more"
        raise OpError(
            f"validation found {len(errors)} error(s); pass force=True to "
            f"export anyway:\n{head}{more}")

    out_dir = Path(out_dir)
    if out_dir.exists():
        if clean and not (out_dir / EXPORT_MARKER).exists():
            raise OpError(
                f"refusing to delete {out_dir}: it is not a previous export "
                f"(no {EXPORT_MARKER} marker)")
        if clean:
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    files: List[Path] = []

    def copy_one(src, dst) -> None:
        os.makedirs(dst.parent, exist_ok=True)
        shutil.copy2(src, dst)
        files.append(dst)

    main_text = (ENGINE_ROOT / "src" / "main.py").read_text()
    if re.search(r"(?m)^\s*(from|import)\s+(tools|maker)\b", main_text):
        raise OpError(
            "engine src/main.py imports tools.* or maker.*; the exporter "
            "refuses to ship a runtime that depends on editor code")

    # engine runtime (no src/data/cells, no world_index, no __pycache__)
    for f in ("__init__.py", "main.py", "debug_window.py", "project.py"):
        copy_one(ENGINE_ROOT / "src" / f, out_dir / "src" / f)
    for f in sorted((ENGINE_ROOT / "src" / "engine").glob("*.py")):
        copy_one(f, out_dir / "src" / "engine" / f.name)
    for f in ("__init__.py", "schema.py", "serialize.py"):
        copy_one(ENGINE_ROOT / "src" / "data" / f, out_dir / "src" / "data" / f)
    for f in sorted((ENGINE_ROOT / "src" / "gfx").glob("*.py")):
        copy_one(f, out_dir / "src" / "gfx" / f.name)

    # project data (into the default layout)
    proj = model.project
    index = json.loads(proj.world_index_path.read_text())
    for fname in index["cells"].values():
        copy_one(proj.cells_dir / fname, out_dir / "world" / "cells" / fname)
    copy_one(proj.world_index_path, out_dir / "world" / "world_index.json")
    for p in sorted(proj.palettes_dir.glob("*.json")):
        copy_one(p, out_dir / "palettes" / p.name)
    for p in sorted(proj.palettes_dir.glob("*.json")):
        tdir = proj.textures_dir / p.stem
        if tdir.is_dir():
            shutil.copytree(tdir, out_dir / "textures" / p.stem,
                            dirs_exist_ok=True)
            files.append(out_dir / "textures" / p.stem)
    if proj.sprites_dir.is_dir():
        for f in sorted(proj.sprites_dir.iterdir()):
            if f.is_file():
                copy_one(f, out_dir / "sprites" / f.name)
    copy_one(proj.zones_path, out_dir / "zones.json")

    manifest = copy.deepcopy(model._manifest)
    manifest.pop("paths", None)
    (out_dir / "project.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    files.append(out_dir / "project.json")

    script = ("#!/bin/bash\n"
              'cd "$(dirname "$0")"\n'
              'exec python3 -m src.main "$@"\n')
    for name in ("play.sh", "play.command"):
        p = out_dir / name
        p.write_text(script)
        p.chmod(0o755)
        files.append(p)

    controls = _main_controls(main_text)
    title = model.manifest.get("title", model.manifest.get("name", "Game"))
    (out_dir / "README.txt").write_text(
        f"{title}\n\n"
        "A game exported with Metroidvania Maker (fixed engine).\n\n"
        "Requires Python 3.9+ and pygame:  pip install pygame\n\n"
        "Run:  python3 -m src.main   (or double-click play.command / run "
        "play.sh)\n\n"
        f"Controls:\n  {controls}\n")
    files.append(out_dir / "README.txt")

    (out_dir / EXPORT_MARKER).write_text("metroidvania maker export\n")
    files.append(out_dir / EXPORT_MARKER)

    return {"out_dir": str(out_dir), "files": len(files),
            "warnings": sum(1 for i in issues if i.severity == "warning")}


def _main_controls(main_text: str) -> str:
    lines = []
    started = False
    for line in main_text.splitlines():
        if line.startswith("Controls:"):
            started = True
            lines.append(line[len("Controls:"):].strip())
            continue
        if started:
            if not line.strip():
                break
            lines.append(line.strip())
    return "\n  ".join(lines)


# ---------------------------------------------------------------------------
# playtest_command
# ---------------------------------------------------------------------------


def playtest_command(model, cell=None, tile=None) -> List[str]:
    """argv to launch this project's game in the current interpreter.

    `cell` is a cell coord and `tile` a tile coord inside it (see
    src/main.py --start-cell/--start-tile).
    """
    argv = [sys.executable, "-m", "src.main",
            "--project", str(model.project.root)]
    if cell is not None:
        argv += ["--start-cell", f"{int(cell[0])},{int(cell[1])}"]
        if tile is not None:
            argv += ["--start-tile", f"{int(tile[0])},{int(tile[1])}"]
    return argv
