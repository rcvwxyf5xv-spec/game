"""JSON command line for Metroidvania Maker.

Run:  .venv/bin/python -m maker.cli [--project DIR] [--dry-run]
      [--no-validate] [--pretty] COMMAND ...

Every successful run prints exactly one JSON object on stdout and exits 0:
    {"ok": true, "result": ..., "issues": [...]}
where `issues` lists the new error/warning validation issues this
mutating command introduced. OpError failures print
    {"ok": false, "error": "...", "type": "OpError"}
and exit 1 (batch failures also carry failed_index/op); argparse usage
errors exit 2. Nothing else is ever written to stdout (pygame's support
banner is suppressed and tool chatter is routed to stderr).

Mutating commands save the model automatically on success; --dry-run
applies the change in memory, validates, and writes nothing. Coordinates
are "cx,cy" strings; lists of points are "x,y;x,y".
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path
from typing import List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from src.engine.constants import CELL_HEIGHT_TILES, CELL_WIDTH_TILES  # noqa: E402
from maker.state import resolve_startup_project  # noqa: E402

from maker.core import assets, ops, project_ops, render, validate  # noqa: E402
from maker.core.errors import OpError  # noqa: E402
from maker.core.model import ProjectModel  # noqa: E402
from maker.core.validate import Issue  # noqa: E402

_RANK = {"error": 0, "warning": 1, "info": 2}
_OVERLAY_NAMES = ("grid", "cells", "edges", "entities", "kinds")
_batch_fail: Optional[dict] = None


# ---------------------------------------------------------------------------
# argument helpers
# ---------------------------------------------------------------------------


def _pair(s, label="coordinate"):
    """Parse "x,y" into [x, y]; accepts non-string int pairs too."""
    if isinstance(s, (list, tuple)) and len(s) == 2:
        return [int(s[0]), int(s[1])]
    parts = str(s).split(",")
    if len(parts) != 2:
        raise OpError(f"{label}: expected 'x,y', got {s!r}")
    try:
        return [int(parts[0]), int(parts[1])]
    except ValueError:
        raise OpError(f"{label}: expected two integers, got {s!r}") from None


def _points(s, label="point list"):
    """Parse "x,y;x,y" into [[x, y], ...]."""
    if isinstance(s, list):
        return [_pair(p, label) for p in s]
    if not s:
        raise OpError(f"{label}: got an empty string")
    return [_pair(part, label) for part in str(s).split(";")]


def _rgb(s, label="color"):
    if isinstance(s, (list, tuple)) and len(s) == 3:
        return [int(c) for c in s]
    parts = str(s).split(",")
    if len(parts) != 3:
        raise OpError(f"{label}: expected 'r,g,b', got {s!r}")
    try:
        return [int(c) for c in parts]
    except ValueError:
        raise OpError(f"{label}: expected three integers, got {s!r}") from None


def _props(pairs):
    """--prop k=v ... with JSON-parsed values where possible."""
    out = {}
    for item in pairs or []:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise OpError(f"--prop expects k=v, got {item!r}")
        try:
            out[key] = json.loads(val)
        except ValueError:
            out[key] = val
    return out


def _read_text(source):
    if source in (None, "-"):
        return sys.stdin.read()
    return Path(source).read_text()


def _read_json(source, label):
    """`source` is '-' (stdin), inline JSON (starts with { or [) or a path."""
    stripped = str(source).lstrip() if source is not None else ""
    if stripped[:1] in ("{", "["):
        text = stripped
    else:
        try:
            text = _read_text(source)
        except OSError as exc:
            raise OpError(f"{label}: cannot read {source!r}: "
                          f"{exc.strerror or exc}") from None
    try:
        return json.loads(text)
    except ValueError as exc:
        raise OpError(f"{label}: invalid JSON: {exc}") from None


def _overlay_list(s):
    if not s:
        return ()
    names = tuple(part.strip() for part in str(s).split(","))
    bad = [n for n in names if n not in _OVERLAY_NAMES]
    if bad:
        raise OpError(
            f"unknown overlay(s) {bad}; expected one of {list(_OVERLAY_NAMES)}")
    return names


def _room_count(model) -> int:
    visited = set()
    count = 0
    for coord in sorted(model.cells):
        if coord in visited:
            continue
        count += 1
        queue = [coord]
        visited.add(coord)
        while queue:
            cur = queue.pop(0)
            for direction, (dx, dy) in (
                    ("north", (0, -1)), ("south", (0, 1)),
                    ("west", (-1, 0)), ("east", (1, 0))):
                edge = model.cell(cur).get("edges", {}).get(direction)
                if not edge or edge.get("type") != "open":
                    continue
                nbr = (cur[0] + dx, cur[1] + dy)
                if nbr in model.cells and nbr not in visited:
                    visited.add(nbr)
                    queue.append(nbr)
    return count


def _room_ascii(model, room) -> list:
    legend = ops.DEFAULT_LEGEND
    char_for = {}
    for ch, kind in legend.items():
        char_for.setdefault(kind, ch)
    members = set(room.cells)
    lines = []
    n_csv = room.width // CELL_WIDTH_TILES
    n_csy = room.height // CELL_HEIGHT_TILES
    for cyi in range(n_csy):
        cy = room.origin[1] + cyi
        for ty in range(CELL_HEIGHT_TILES):
            parts = []
            for cxi in range(n_csv):
                cx = room.origin[0] + cxi
                if (cx, cy) not in members:
                    parts.append("!" * CELL_WIDTH_TILES)
                    continue
                row = model.cell((cx, cy))["tiles"][ty]
                chars = [char_for.get(tile.get("kind", "empty"), "?")
                         for tile in row]
                parts.append("".join(chars))
            lines.append("|".join(parts))
    return lines


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def h_info(args, model):
    bounds = model.bounds()
    usage = model.palette_usage()
    entity_counts = {}
    for coord in sorted(model.cells):
        for e in model.cell(coord).get("entities", []):
            etype = e.get("type", "")
            entity_counts[etype] = entity_counts.get(etype, 0) + 1
    return {
        "name": model.manifest.get("name"),
        "title": model.manifest.get("title", model.manifest.get("name")),
        "root": str(model.project.root),
        "format": model.manifest.get("format"),
        "cells": len(model.cells),
        "rooms": _room_count(model),
        "bounds": [[bounds[0], bounds[1]], [bounds[2], bounds[3]]]
        if bounds else None,
        "start_cell": list(model.start_cell) if model.start_cell else None,
        "zones": model.zone_names(),
        "palettes": dict(sorted(usage.items())),
        "sprites": sorted(model.sprites),
        "entities": entity_counts,
        "dirty": model.is_dirty,
    }


def h_validate(args, model):
    issues = validate.validate(model)
    if args.min_severity:
        rank = _RANK[args.min_severity]
        issues = [i for i in issues if _RANK.get(i.severity, 3) <= rank]
    return {"issues": [i.to_dict() for i in issues], "count": len(issues)}


def h_ops(args, model):
    out = []
    for name in sorted(ops.OPS):
        fn = ops.OPS[name]
        out.append({
            "name": name,
            "signature": str(inspect.signature(fn)),
            "doc": (inspect.getdoc(fn) or "").strip(),
        })
    return {"ops": out}


def h_help_agent(args, model):
    return {
        "about": "Metroidvania Maker CLI: edit project (game) data through "
                 "the same headless core as the GUI.",
        "usage": "python -m maker.cli [--project DIR] [--dry-run] "
                 "[--no-validate] [--pretty] COMMAND ...",
        "global_flags": [
            "--project DIR   project folder (default: METROIDVANIA_PROJECT "
            "env, the last opened project, or the first project in "
            "projects/)",
            "--dry-run       apply in memory + validate, do not save",
            "--no-validate   skip the before/after validation report",
            "--pretty        indent the JSON output",
        ],
        "commands": {
            "info": "project summary (cells, rooms, zones, palettes, "
                    "sprites, entities, dirty flag)",
            "validate": "run every rule; --min-severity error|warning|info",
            "ops": "list every op with signature and docstring (agent "
                   "self-discovery)",
            "cell list|get|new|delete|move|set|props|ascii": "cell CRUD "
                    "and 16x10 ascii editing",
            "cells move|duplicate": "translate or copy a set of cells",
            "room get": "room members, origin, size, door crossings, "
                        "room-wide ascii",
            "tiles set|rect|fill|replace": "paint tiles by kind/palette",
            "edge set / door link|unlink": "connect or break cell edges",
            "entity list|add|move|set|remove|types": "entity CRUD",
            "start set": "choose the start cell",
            "zone list|get|set|rename|delete|color|palette|apply-palette":
                    "zone palette data and default palette (re-skin)",
            "palette list|get|create|set|colors|color|rename|delete|rebuild":
                    "palette data and texture generation",
            "texture import|unlock|export": "import PNG sheets or rebuild "
                    "procedurally",
            "sprite list|get|set|build": "sprite sources and sheet builds",
            "render": "render a room/cell or the world map to a PNG",
            "new": "create a new project (starter or copy template)",
            "export": "export a playable standalone game folder",
            "playtest": "launch the game for this project (returns pid)",
            "batch": "run a JSON list of ops in one transaction",
        },
        "examples": [
            "python -m maker.cli info",
            "python -m maker.cli validate",
            "python -m maker.cli cell get 0,0 --ascii",
            "python -m maker.cli cell new 2,0 --template walled",
            "python -m maker.cli tile ... set 0,0 --at '3,4;5,6' --kind spring",
            "python -m maker.cli door link 0,0 east --to 1,0 --carve",
            "python -m maker.cli entity add 0,0 save_station --at 5,3",
            "python -m maker.cli render map -o map.png",
            "python -m maker.cli render 0,0 --room --overlay "
            "grid,cells,edges,entities -o room.png",
            "python -m maker.cli new my_game --name my_game --template starter",
            "python -m maker.cli export build/MyGame",
            "python -m maker.cli batch commands.json",
        ],
        "glossary": {
            "Cell": "one world-map square, 16x10 tiles, one JSON file",
            "Tile": "one 16x16 game-px square inside a cell (kind+palette)",
            "Room": "cells joined by open edges (computed, not authored)",
            "Edge": "one side of a cell: wall / open / door",
            "Door": "an edge linking to another cell's edge",
            "Frame": "one sub-image of a texture or sprite sheet",
            "Entity": "object placed at tile coords inside a cell",
            "Zone": "cosmetic area label (visual theme, title card)",
            "Palette": "named tile texture set",
            "Project": "one complete game's data folder",
        },
    }


def h_cell_list(args, model):
    cells = []
    for coord in sorted(model.cells):
        data = model.cell(coord)
        if args.zone and data.get("zone", "") != args.zone:
            continue
        cells.append({
            "coord": list(coord),
            "zone": data.get("zone", ""),
            "entities": len(data.get("entities", [])),
            "edges": sorted(data.get("edges", {})),
        })
    return {"cells": cells}


def h_cell_get(args, model):
    coord = _pair(args.CX, "cell coord")
    if not model.has_cell(coord):
        raise OpError(f"no cell at {tuple(coord)}")
    result = {"cell": model.cell(coord), "coord": coord}
    if args.ascii:
        result["ascii"] = ops.cell_ascii(model, coord)
        if args.legend:
            result["legend"] = ops.DEFAULT_LEGEND
    return result


def h_cell_new(args, model):
    coord = _pair(args.CX, "cell coord")
    kwargs = {}
    if args.zone is not None:
        kwargs["zone"] = args.zone
    if args.template is not None:
        kwargs["template"] = args.template
    if args.palette is not None:
        kwargs["palette"] = args.palette
    if args.bg is not None:
        kwargs["background_color"] = _rgb(args.bg, "background")
    return ops.new_cell(model, coord, **kwargs)


def h_cell_delete(args, model):
    return ops.delete_cell(model, _pair(args.CX, "cell coord"),
                           force=args.force)


def h_cell_move(args, model):
    coord = _pair(args.CX, "cell coord")
    to = _pair(args.to, "destination")
    return ops.move_cells(model, [coord], to[0] - coord[0], to[1] - coord[1])


def h_cells_move(args, model):
    coords = _points(args.cells, "cells")
    dx, dy = _pair(args.by, "offset")
    return ops.move_cells(model, coords, dx, dy)


def h_cells_duplicate(args, model):
    coords = _points(args.cells, "cells")
    dx, dy = _pair(args.by, "offset")
    return ops.duplicate_cells(model, coords, dx, dy)


def h_cell_set(args, model):
    return ops.set_cell_data(model, _pair(args.CX, "cell coord"),
                             _read_json(args.json, "cell data"))


def h_cell_props(args, model):
    kwargs = {}
    if args.zone is not None:
        kwargs["zone"] = args.zone
    if args.bg is not None:
        kwargs["background_color"] = _rgb(args.bg, "background")
    return ops.set_cell_props(model, _pair(args.CX, "cell coord"), **kwargs)


def h_cell_ascii(args, model):
    coord = _pair(args.CX, "cell coord")
    rows = [line.rstrip("\r\n") for line in _read_text(args.file).splitlines()]
    return ops.set_cell_ascii(model, coord, rows, palette=args.palette)


def h_room_get(args, model):
    coord = _pair(args.CX, "cell coord")
    try:
        room = model.assemble(coord)
    except KeyError as exc:
        raise OpError(str(exc)) from None
    result = {
        "coord": coord,
        "zone": room.zone,
        "members": [list(c) for c in room.cells],
        "origin": list(room.origin),
        "size_tiles": [room.width, room.height],
        "background_color": list(room.background_color),
        "truncated": room.truncated,
        "door_crossings": [
            {"direction": d.direction,
             "cell_rect": [int(v) for v in d.cell_rect],
             "door_id": d.door_id,
             "target_cell": list(d.target_cell),
             "target_edge": d.target_edge}
            for d in room.door_crossings],
    }
    if args.ascii:
        result["ascii"] = _room_ascii(model, room)
    return result


def h_tiles_set(args, model):
    return ops.set_tiles(model, _pair(args.CX, "cell coord"),
                         _points(args.at, "points"), args.kind,
                         palette=args.palette)


def h_tiles_rect(args, model):
    from_x, from_y = _pair(args.from_, "corner")
    to_x, to_y = _pair(args.to, "corner")
    return ops.fill_rect(model, _pair(args.CX, "cell coord"),
                         from_x, from_y, to_x, to_y, args.kind,
                         palette=args.palette)


def h_tiles_fill(args, model):
    tx, ty = _pair(args.at, "seed tile")
    return ops.flood_fill(model, _pair(args.CX, "cell coord"),
                          tx, ty, args.kind, palette=args.palette)


def h_tiles_replace(args, model):
    cells = _points(args.cells, "cells") if args.cells else None
    return ops.replace_tiles(model, cells=cells, from_kind=args.from_kind,
                             from_palette=args.from_palette,
                             to_kind=args.to_kind,
                             to_palette=args.to_palette)


def h_edge_set(args, model):
    return ops.set_edge(model, _pair(args.CX, "cell coord"), args.DIR,
                        args.edge_type, carve=args.carve)


def h_door_link(args, model):
    return ops.link_door(model, _pair(args.CX, "cell coord"), args.DIR,
                         _pair(args.to, "target cell"),
                         target_edge=args.target_edge, door_id=args.door_id,
                         carve=args.carve)


def h_door_unlink(args, model):
    return ops.unlink_door(model, _pair(args.CX, "cell coord"), args.DIR)


def h_entity_list(args, model):
    coords = [_pair(args.CX, "cell coord")] if getattr(args, "CX", None) \
        else sorted(model.cells)
    out = []
    for coord in coords:
        data = model.cell(coord)
        if data is None:
            raise OpError(f"no cell at {tuple(coord)}")
        for i, entity in enumerate(data.get("entities", [])):
            out.append({"cell": list(coord), "index": i, "entity": entity})
    return {"entities": out}


def h_entity_add(args, model):
    tx, ty = _pair(args.at, "tile")
    return ops.add_entity(model, _pair(args.CX, "cell coord"), args.TYPE,
                          tx, ty, props=_props(args.prop))


def h_entity_move(args, model):
    tx, ty = _pair(args.at, "tile")
    to_coord = _pair(args.to_cell, "target cell") if args.to_cell else None
    return ops.move_entity(model, _pair(args.CX, "cell coord"), args.INDEX,
                           tx, ty, to_coord=to_coord)


def h_entity_set(args, model):
    return ops.update_entity(model, _pair(args.CX, "cell coord"),
                             args.INDEX, _props(args.prop))


def h_entity_remove(args, model):
    return ops.remove_entity(model, _pair(args.CX, "cell coord"), args.INDEX)


def h_entity_types(args, model):
    return {"types": ops.entity_types(model)}


def h_start_set(args, model):
    return ops.set_start_cell(model, _pair(args.CX, "cell coord"))


def h_zone_list(args, model):
    return {"zones": model.zone_names()}


def h_zone_get(args, model):
    if args.NAME not in model.zones:
        raise OpError(f"no zone {args.NAME!r}")
    return {"zone": args.NAME, "data": model.zones[args.NAME]}


def h_zone_set(args, model):
    return ops.set_zone(model, args.NAME, _read_json(args.json, "zone data"))


def h_zone_rename(args, model):
    return ops.rename_zone(model, args.OLD, args.NEW)


def h_zone_delete(args, model):
    return ops.delete_zone(model, args.NAME, force=args.force)


def h_zone_color(args, model):
    return ops.set_zone_color(model, args.NAME, args.KEY,
                              _rgb(args.rgb, "color"), index=args.index)


def h_zone_palette(args, model):
    return ops.set_zone_palette(
        model, args.NAME,
        None if args.PALETTE in ("-", "none") else args.PALETTE)


def h_zone_apply_palette(args, model):
    return ops.apply_zone_palette(model, args.NAME,
                                  palette=args.palette,
                                  only_unset=args.only_unset)


def h_palette_list(args, model):
    usage = model.palette_usage()
    out = []
    for name in sorted(model.palettes):
        pal = model.palettes[name]
        out.append({
            "name": name,
            "display_name": pal.get("display_name", name),
            "generator": pal.get("build", {}).get("generator", ""),
            "used_by_tiles": usage.get(name, 0),
        })
    return {"palettes": out}


def h_palette_get(args, model):
    if args.NAME not in model.palettes:
        raise OpError(f"no palette {args.NAME!r}")
    return {"palette": args.NAME, "data": model.palettes[args.NAME]}


def h_palette_create(args, model):
    base = args.base or "default"
    result = ops.create_palette(model, args.NAME, base=base,
                                display_name=args.display_name)
    if not args.dry_run:
        assets.create_palette_assets(model, args.NAME, base=base)
    return result


def _rebuild_palette(args, model, name) -> None:
    if args.dry_run or getattr(args, "no_rebuild", False):
        return
    assets.rebuild_textures(model, [name])


def h_palette_set(args, model):
    result = ops.set_palette_data(model, args.NAME,
                                  _read_json(args.json, "palette data"))
    _rebuild_palette(args, model, args.NAME)
    return result


def h_palette_colors(args, model):
    result = ops.set_palette_colors(model, args.NAME,
                                    _read_json(args.json, "colors"))
    _rebuild_palette(args, model, args.NAME)
    return result


def h_palette_color(args, model):
    result = ops.set_palette_color(model, args.NAME, args.RAMP, args.INDEX,
                                   _rgb(args.rgb, "color"))
    _rebuild_palette(args, model, args.NAME)
    return result


def h_palette_rename(args, model):
    return ops.rename_palette(model, args.OLD, args.NEW)


def h_palette_delete(args, model):
    return ops.delete_palette(model, args.NAME,
                              replace_with=args.replace_with)


def h_palette_rebuild(args, model):
    names = args.NAMES or None
    written = assets.rebuild_textures(model, names,
                                      write=not args.dry_run)
    return {"rebuilt": [str(p) for p in written]}


def h_texture_import(args, model):
    return assets.import_texture(
        model, args.PALETTE, args.KIND, args.PNG, mode=args.mode,
        frames=args.frames, frame_ticks=args.frame_ticks,
        write=not args.dry_run)


def h_texture_unlock(args, model):
    written = assets.unlock_texture(model, args.PALETTE, args.KIND,
                                    write=not args.dry_run)
    return {"unlocked": args.KIND, "rebuilt": [str(p) for p in written]}


def h_texture_export(args, model):
    if args.frame is not None:
        out = assets.export_texture_tile(model, args.PALETTE, args.KIND,
                                         args.OUT, frame=args.frame)
    else:
        out = assets.export_texture(model, args.PALETTE, args.KIND, args.OUT)
    return {"out": str(out)}


def h_sprite_list(args, model):
    return {"sprites": sorted(model.sprites)}


def h_sprite_get(args, model):
    if args.NAME not in model.sprites:
        raise OpError(f"no sprite {args.NAME!r}")
    return {"sprite": args.NAME, "data": model.sprites[args.NAME]}


def h_sprite_set(args, model):
    return ops.set_sprite_data(model, args.NAME,
                               _read_json(args.json, "sprite data"))


def h_sprite_build(args, model):
    names = args.NAMES or None
    written = assets.build_sprites(model, names, write=not args.dry_run)
    return {"built": [str(p) for p in written]}


def h_render(args, model):
    if args.target == "map":
        surf = render.render_world_map(model, px_per_cell=args.px)
        render.save_png(surf, args.out)
        return {"out": str(args.out), "kind": "map",
                "size": list(surf.get_size()), "px_per_cell": args.px}
    coord = _pair(args.target, "cell coord")
    overlays = _overlay_list(args.overlay)
    lighting = not args.no_lighting
    if args.mode == "cell":
        surf = render.render_cell(model, coord, lighting=lighting,
                                  overlays=overlays)
    else:
        surf = render.render_room(model, coord, scale=args.scale,
                                  lighting=lighting, overlays=overlays)
    render.save_png(surf, args.out)
    return {"out": str(args.out), "kind": args.mode, "coord": coord,
            "size": list(surf.get_size())}


def h_new(args, model):
    source = args.from_src
    if args.template == "copy" and not source:
        if model is not None:
            source = model.project.root
        else:
            raise OpError(
                "template 'copy' needs a source project (--from DIR)")
    dest = project_ops.create_project(
        args.DEST, args.name, title=args.title, template=args.template,
        source=source)
    return {"created": str(dest), "name": args.name,
            "template": args.template}


def h_export(args, model):
    return project_ops.export_game(model, args.OUT_DIR, force=args.force)


def h_playtest(args, model):
    cell = _pair(args.cell, "cell coord") if args.cell else None
    tile = _pair(args.tile, "tile") if args.tile else None
    if tile is not None and cell is None:
        raise OpError("--tile requires --cell")
    argv = project_ops.playtest_command(model, cell=cell, tile=tile)
    proc = subprocess.Popen(argv)
    return {"pid": proc.pid, "argv": argv}


def h_batch(args, model):
    global _batch_fail
    _batch_fail = None
    reqs = _read_json(args.file, "batch input")
    if not isinstance(reqs, list):
        raise OpError("batch input must be a JSON list of op requests")
    results = []
    with model.transaction("batch"):
        for i, req in enumerate(reqs):
            if not isinstance(req, dict):
                _batch_fail = {"failed_index": i, "op": None,
                               "error": f"batch item {i}: expected an "
                                        f"object with an 'op' key"}
                raise OpError(_batch_fail["error"])
            name = req.get("op")
            fn = ops.OPS.get(name) if name else None
            if fn is None:
                _batch_fail = {"failed_index": i, "op": name,
                               "error": f"batch item {i}: unknown op "
                                        f"{name!r}"}
                raise OpError(_batch_fail["error"])
            kwargs = {k: v for k, v in req.items() if k != "op"}
            try:
                results.append(fn(model, **kwargs))
            except OpError as exc:
                _batch_fail = {"failed_index": i, "op": name,
                               "error": str(exc)}
                raise
    return {"count": len(results), "results": results}


def _set_reg(name, p, func, mutates):
    p.set_defaults(func=func, mutates=mutates)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maker.cli",
        description="JSON command line for Metroidvania Maker",
        add_help=True)
    parser.add_argument("--project", metavar="DIR",
                        help="project folder (default: METROIDVANIA_PROJECT "
                             "env or the engine-root demo)")
    parser.add_argument("--dry-run", action="store_true",
                        help="apply + validate in memory, do not save")
    parser.add_argument("--no-validate", action="store_true",
                        help="skip the before/after validation report")
    parser.add_argument("--pretty", action="store_true",
                        help="indent the JSON output")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    def cmd(name, func, mutates, help=None, needs_model=True, **kw):
        p = sub.add_parser(name, help=help, description=help, **kw)
        _set_reg(name, p, func, mutates)
        p.set_defaults(needs_model=needs_model)
        return p

    cmd("info", h_info, False, "project summary")
    cmd("validate", h_validate, False, "run every validation rule").add_argument(
        "--min-severity", choices=["error", "warning", "info"])
    cmd("ops", h_ops, False, "list every op with signature + docstring")
    cmd("help-agent", h_help_agent, False, "compact usage guide for agents",
        needs_model=False)

    p = cmd("cell", None, False, "cell subcommands")
    cell_sub = p.add_subparsers(dest="cell_command", metavar="C")
    cl = cell_sub.add_parser("list"); _set_reg("cell-list", cl, h_cell_list, False)
    cl.add_argument("--zone")
    cg = cell_sub.add_parser("get"); _set_reg("cell-get", cg, h_cell_get, False)
    cg.add_argument("CX"); cg.add_argument("--ascii", action="store_true")
    cg.add_argument("--legend", action="store_true")
    cn = cell_sub.add_parser("new"); _set_reg("cell-new", cn, h_cell_new, True)
    cn.add_argument("CX"); cn.add_argument("--zone"); cn.add_argument(
        "--template", choices=["walled", "empty", "solid"])
    cn.add_argument("--palette"); cn.add_argument("--bg", metavar="r,g,b")
    cd = cell_sub.add_parser("delete"); _set_reg("cell-delete", cd, h_cell_delete, True)
    cd.add_argument("CX"); cd.add_argument("--force", action="store_true")
    cm = cell_sub.add_parser("move"); _set_reg("cell-move", cm, h_cell_move, True)
    cm.add_argument("CX"); cm.add_argument("--to", required=True)
    cs = cell_sub.add_parser("set"); _set_reg("cell-set", cs, h_cell_set, True)
    cs.add_argument("CX"); cs.add_argument("--json", required=True, help="inline JSON, a file path, or - for stdin")
    cp = cell_sub.add_parser("props"); _set_reg("cell-props", cp, h_cell_props, True)
    cp.add_argument("CX"); cp.add_argument("--zone"); cp.add_argument("--bg", metavar="r,g,b")
    ca = cell_sub.add_parser("ascii"); _set_reg("cell-ascii", ca, h_cell_ascii, True)
    ca.add_argument("CX"); ca.add_argument("--file", default="-")
    ca.add_argument("--palette")
    for s in ("list", "get", "new", "delete", "move", "set", "props", "ascii"):
        cell_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("cells", None, True, "move/duplicate a set of cells")
    cs2 = p.add_subparsers(dest="cells_command", metavar="C")
    mv = cs2.add_parser("move"); _set_reg("cells-move", mv, h_cells_move, True)
    mv.add_argument("--cells", required=True)
    mv.add_argument("--by", required=True, metavar="dx,dy")
    du = cs2.add_parser("duplicate"); _set_reg("cells-duplicate", du, h_cells_duplicate, True)
    du.add_argument("--cells", required=True)
    du.add_argument("--by", required=True, metavar="dx,dy")
    for s in ("move", "duplicate"):
        cs2.choices[s].set_defaults(needs_model=True)

    p = cmd("room", None, False, "room subcommands")
    room_sub = p.add_subparsers(dest="room_command", metavar="C")
    rg = room_sub.add_parser("get"); _set_reg("room-get", rg, h_room_get, False)
    rg.add_argument("CX"); rg.add_argument("--ascii", action="store_true")
    room_sub.choices["get"].set_defaults(needs_model=True)

    p = cmd("tiles", None, True, "paint tiles by kind/palette")
    tile_sub = p.add_subparsers(dest="tiles_command", metavar="C")
    ts = tile_sub.add_parser("set"); _set_reg("tiles-set", ts, h_tiles_set, True)
    ts.add_argument("CX"); ts.add_argument("--at", required=True)
    ts.add_argument("--kind", required=True); ts.add_argument("--palette")
    tr = tile_sub.add_parser("rect"); _set_reg("tiles-rect", tr, h_tiles_rect, True)
    tr.add_argument("CX"); tr.add_argument("--from", dest="from_", required=True)
    tr.add_argument("--to", required=True); tr.add_argument("--kind", required=True)
    tr.add_argument("--palette")
    tf = tile_sub.add_parser("fill"); _set_reg("tiles-fill", tf, h_tiles_fill, True)
    tf.add_argument("CX"); tf.add_argument("--at", required=True)
    tf.add_argument("--kind", required=True); tf.add_argument("--palette")
    trp = tile_sub.add_parser("replace"); _set_reg("tiles-replace", trp, h_tiles_replace, True)
    trp.add_argument("--cells"); trp.add_argument("--from-kind")
    trp.add_argument("--from-palette"); trp.add_argument("--to-kind")
    trp.add_argument("--to-palette")
    for s in ("set", "rect", "fill", "replace"):
        tile_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("edge", None, True, "edge subcommands")
    edge_sub = p.add_subparsers(dest="edge_command", metavar="C")
    es_ = edge_sub.add_parser("set"); _set_reg("edge-set", es_, h_edge_set, True)
    es_.add_argument("CX"); es_.add_argument("DIR")
    es_.add_argument("edge_type", choices=["wall", "open"])
    es_.add_argument("--carve", action="store_true")
    edge_sub.choices["set"].set_defaults(needs_model=True)

    p = cmd("door", None, True, "door subcommands")
    door_sub = p.add_subparsers(dest="door_command", metavar="C")
    dl = door_sub.add_parser("link"); _set_reg("door-link", dl, h_door_link, True)
    dl.add_argument("CX"); dl.add_argument("DIR"); dl.add_argument("--to", required=True)
    dl.add_argument("--target-edge"); dl.add_argument("--id", dest="door_id");
    dl.add_argument("--carve", action="store_true")
    du = door_sub.add_parser("unlink"); _set_reg("door-unlink", du, h_door_unlink, True)
    du.add_argument("CX"); du.add_argument("DIR")
    for s in ("link", "unlink"):
        door_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("entity", None, False, "entity subcommands")
    ent_sub = p.add_subparsers(dest="entity_command", metavar="C")
    el = ent_sub.add_parser("list"); _set_reg("entity-list", el, h_entity_list, False)
    el.add_argument("CX", nargs="?")
    ea = ent_sub.add_parser("add"); _set_reg("entity-add", ea, h_entity_add, True)
    ea.add_argument("CX"); ea.add_argument("TYPE"); ea.add_argument("--at", required=True)
    ea.add_argument("--prop", action="append", default=[])
    em = ent_sub.add_parser("move"); _set_reg("entity-move", em, h_entity_move, True)
    em.add_argument("CX"); em.add_argument("INDEX", type=int)
    em.add_argument("--at", required=True); em.add_argument("--to-cell")
    es = ent_sub.add_parser("set"); _set_reg("entity-set", es, h_entity_set, True)
    es.add_argument("CX"); es.add_argument("INDEX", type=int)
    es.add_argument("--prop", action="append", required=True, default=[])
    er = ent_sub.add_parser("remove"); _set_reg("entity-remove", er, h_entity_remove, True)
    er.add_argument("CX"); er.add_argument("INDEX", type=int)
    et = ent_sub.add_parser("types"); _set_reg("entity-types", et, h_entity_types, False)
    for s in ("list", "add", "move", "set", "remove", "types"):
        ent_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("start", None, True, "start subcommands")
    start_sub = p.add_subparsers(dest="start_command", metavar="C")
    ss_ = start_sub.add_parser("set"); _set_reg("start-set", ss_, h_start_set, True)
    ss_.add_argument("CX")
    start_sub.choices["set"].set_defaults(needs_model=True)

    p = cmd("zone", None, False, "zone subcommands")
    zone_sub = p.add_subparsers(dest="zone_command", metavar="C")
    zl = zone_sub.add_parser("list"); _set_reg("zone-list", zl, h_zone_list, False)
    zg = zone_sub.add_parser("get"); _set_reg("zone-get", zg, h_zone_get, False)
    zg.add_argument("NAME")
    zs = zone_sub.add_parser("set"); _set_reg("zone-set", zs, h_zone_set, True)
    zs.add_argument("NAME"); zs.add_argument("--json", required=True, help="inline JSON, a file path, or - for stdin")
    zr = zone_sub.add_parser("rename"); _set_reg("zone-rename", zr, h_zone_rename, True)
    zr.add_argument("OLD"); zr.add_argument("NEW")
    zd = zone_sub.add_parser("delete"); _set_reg("zone-delete", zd, h_zone_delete, True)
    zd.add_argument("NAME"); zd.add_argument("--force", action="store_true")
    zc = zone_sub.add_parser("color"); _set_reg("zone-color", zc, h_zone_color, True)
    zc.add_argument("NAME"); zc.add_argument("KEY")
    zc.add_argument("rgb", metavar="r,g,b"); zc.add_argument("--index", type=int)
    zp = zone_sub.add_parser("palette"); _set_reg("zone-palette", zp, h_zone_palette, True)
    zp.add_argument("NAME")
    zp.add_argument("PALETTE", metavar="PALETTE|-|none",
                    help="palette name; '-' or 'none' clears the default")
    za = zone_sub.add_parser("apply-palette"); _set_reg("zone-apply-palette", za, h_zone_apply_palette, True)
    za.add_argument("NAME")
    za.add_argument("--palette", help="palette name (default: the zone's default_palette)")
    za.add_argument("--only-unset", action="store_true",
                    help="only tiles that have no palette yet")
    for s in ("list", "get", "set", "rename", "delete", "color",
              "palette", "apply-palette"):
        zone_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("palette", None, False, "palette subcommands")
    pal_sub = p.add_subparsers(dest="palette_command", metavar="C")
    pl = pal_sub.add_parser("list"); _set_reg("palette-list", pl, h_palette_list, False)
    pg = pal_sub.add_parser("get"); _set_reg("palette-get", pg, h_palette_get, False)
    pg.add_argument("NAME")
    pc = pal_sub.add_parser("create"); _set_reg("palette-create", pc, h_palette_create, True)
    pc.add_argument("NAME"); pc.add_argument("--from", dest="base")
    pc.add_argument("--display-name")
    ps = pal_sub.add_parser("set"); _set_reg("palette-set", ps, h_palette_set, True)
    ps.add_argument("NAME"); ps.add_argument("--json", required=True, help="inline JSON, a file path, or - for stdin")
    ps.add_argument("--no-rebuild", action="store_true")
    pcl = pal_sub.add_parser("colors"); _set_reg("palette-colors", pcl, h_palette_colors, True)
    pcl.add_argument("NAME"); pcl.add_argument("--json", required=True, help="inline JSON, a file path, or - for stdin")
    pcl.add_argument("--no-rebuild", action="store_true")
    pco = pal_sub.add_parser("color"); _set_reg("palette-color", pco, h_palette_color, True)
    pco.add_argument("NAME"); pco.add_argument("RAMP", choices=["rock", "cap"])
    pco.add_argument("INDEX", type=int); pco.add_argument("rgb", metavar="r,g,b")
    pco.add_argument("--no-rebuild", action="store_true")
    pr = pal_sub.add_parser("rename"); _set_reg("palette-rename", pr, h_palette_rename, True)
    pr.add_argument("OLD"); pr.add_argument("NEW")
    pd = pal_sub.add_parser("delete"); _set_reg("palette-delete", pd, h_palette_delete, True)
    pd.add_argument("NAME"); pd.add_argument("--replace-with")
    pb = pal_sub.add_parser("rebuild"); _set_reg("palette-rebuild", pb, h_palette_rebuild, True)
    pb.add_argument("NAMES", nargs="*")
    for s in ("list", "get", "create", "set", "colors", "color",
              "rename", "delete", "rebuild"):
        pal_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("texture", None, False, "texture subcommands")
    tex_sub = p.add_subparsers(dest="texture_command", metavar="C")
    ti = tex_sub.add_parser("import"); _set_reg("texture-import", ti, h_texture_import, True)
    ti.add_argument("PALETTE"); ti.add_argument("KIND")
    ti.add_argument("PNG")
    ti.add_argument("--mode", choices=["auto", "single", "full", "strip"],
                    default="auto")
    ti.add_argument("--frames", type=int)
    ti.add_argument("--frame-ticks", type=int)
    tu = tex_sub.add_parser("unlock"); _set_reg("texture-unlock", tu, h_texture_unlock, True)
    tu.add_argument("PALETTE"); tu.add_argument("KIND")
    te = tex_sub.add_parser("export"); _set_reg("texture-export", te, h_texture_export, False)
    te.add_argument("PALETTE"); te.add_argument("KIND"); te.add_argument("OUT")
    te.add_argument("--frame", type=int)
    for s in ("import", "unlock", "export"):
        tex_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("sprite", None, False, "sprite subcommands")
    spr_sub = p.add_subparsers(dest="sprite_command", metavar="C")
    sl = spr_sub.add_parser("list"); _set_reg("sprite-list", sl, h_sprite_list, False)
    sg = spr_sub.add_parser("get"); _set_reg("sprite-get", sg, h_sprite_get, False)
    sg.add_argument("NAME")
    ss = spr_sub.add_parser("set"); _set_reg("sprite-set", ss, h_sprite_set, True)
    ss.add_argument("NAME"); ss.add_argument("--json", required=True, help="inline JSON, a file path, or - for stdin")
    sb = spr_sub.add_parser("build"); _set_reg("sprite-build", sb, h_sprite_build, True)
    sb.add_argument("NAMES", nargs="*")
    for s in ("list", "get", "set", "build"):
        spr_sub.choices[s].set_defaults(needs_model=True)

    p = cmd("render", h_render, False, "render a room, cell or the map")
    p.add_argument("target", help="CX,CY or 'map'")
    p.add_argument("-o", "--out", required=True)
    rm = p.add_mutually_exclusive_group()
    rm.add_argument("--room", dest="mode", action="store_const", const="room")
    rm.add_argument("--cell", dest="mode", action="store_const", const="cell")
    p.set_defaults(mode="room")
    p.add_argument("--overlay", default="")
    p.add_argument("--scale", type=float, default=1.0)
    p.add_argument("--no-lighting", action="store_true")
    p.add_argument("--px", type=int, default=24,
                   help="world-map px per cell")

    p = cmd("new", h_new, False, "create a new project", needs_model=False)
    p.add_argument("DEST"); p.add_argument("--name", required=True)
    p.add_argument("--title"); p.add_argument(
        "--template", choices=["starter", "copy"], default="starter")
    p.add_argument("--from", dest="from_src", metavar="SRC")

    p = cmd("export", h_export, False, "export a standalone game folder")
    p.add_argument("OUT_DIR"); p.add_argument("--force", action="store_true")

    p = cmd("playtest", h_playtest, False, "launch the game (non-blocking)")
    p.add_argument("--cell"); p.add_argument("--tile")

    p = cmd("batch", h_batch, True, "run a JSON list of ops in one transaction")
    p.add_argument("file", nargs="?", default="-")

    return parser


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------


def _issue_signature(issue) -> tuple:
    return (issue.severity, issue.code, issue.cell, issue.message)


def _load_model(args) -> ProjectModel:
    root = resolve_startup_project(explicit=args.project, create_missing=False)
    if root is None:
        raise OpError("no project; pass --project DIR or run `new`")
    return ProjectModel.open(root)


def _run(args):
    needs_model = getattr(args, "needs_model", True)
    model = None
    if needs_model:
        model = _load_model(args)
    mutates = getattr(args, "mutates", False)
    baseline = None
    if mutates and not args.no_validate:
        baseline = {_issue_signature(i) for i in validate.validate(model)}
    result = args.func(args, model)
    issues = []
    if mutates and not args.no_validate:
        after = validate.validate(model)
        issues = [i for i in after
                  if _issue_signature(i) not in baseline
                  and i.severity in ("error", "warning")]
    if mutates and not args.dry_run and model is not None:
        model.save()
    return result, [i.to_dict() for i in issues]


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help(sys.stderr)
        return 2
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result, issues = _run(args)
    except OpError as exc:
        fail = {"ok": False, "error": str(exc), "type": "OpError"}
        if _batch_fail is not None:
            fail["failed_index"] = _batch_fail["failed_index"]
            fail["op"] = _batch_fail["op"]
        print(json.dumps(fail))
        return 1
    except Exception as exc:  # keep stdout JSON-clean for unexpected errors
        traceback.print_exc()
        print(json.dumps({"ok": False, "error": str(exc),
                          "type": type(exc).__name__}))
        return 1
    payload = {"ok": True, "result": result, "issues": issues}
    print(json.dumps(payload, indent=2) if args.pretty else json.dumps(payload))
    return 0


if __name__ == "__main__":
    sys.exit(main())
