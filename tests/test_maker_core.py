"""Headless checks for the maker core (ProjectModel / ops / undo-redo /
validate).

Runs against a copy of the in-repo demo project under .maker_tmp (never
touches the real src/data or assets). Each section resets the copy so it
sees pristine demo data.

Run with:  .venv/bin/python -m tests.test_maker_core
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from maker.core import ops, validate  # noqa: E402
from maker.core.errors import OpError  # noqa: E402
from maker.core.model import ProjectModel  # noqa: E402
from src.data.serialize import cell_from_dict, save_cell  # noqa: E402

MAKER_TMP = ROOT / ".maker_tmp"
PROJ_COPY = MAKER_TMP / "proj_copy"
SCRATCH = MAKER_TMP / "scratch"

_COPY_PATHS = {
    "world_index": "world/world_index.json",
    "cells_dir": "world/cells",
    "palettes_dir": "palettes",
    "textures_dir": "textures",
    "sprite_src_dir": "sprite_src",
    "sprites_dir": "sprites",
    "zones": "zones.json",
}

failures: list = []


def check(label: str, cond: bool) -> bool:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {label}")
    if not cond:
        failures.append(label)
    return cond


def reset_copy() -> ProjectModel:
    """Re-create the pristine demo copy and open a fresh model on it."""
    if PROJ_COPY.exists():
        shutil.rmtree(PROJ_COPY)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / "project.json").read_text())
    manifest["paths"] = dict(_COPY_PATHS)
    PROJ_COPY.mkdir(parents=True)
    (PROJ_COPY / "project.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    (PROJ_COPY / "world").mkdir(parents=True)
    shutil.copy(ROOT / "src/data/world_index.json",
                PROJ_COPY / "world/world_index.json")
    shutil.copytree(ROOT / "src/data/cells", PROJ_COPY / "world/cells")
    shutil.copytree(ROOT / "assets/palettes", PROJ_COPY / "palettes")
    shutil.copy(ROOT / "assets/zones.json", PROJ_COPY / "zones.json")
    shutil.copytree(ROOT / "assets/sprite_src", PROJ_COPY / "sprite_src")
    shutil.copytree(ROOT / "assets/sprites", PROJ_COPY / "sprites")
    shutil.copytree(ROOT / "assets/textures", PROJ_COPY / "textures")
    return ProjectModel.open(PROJ_COPY)


def model_state(m: ProjectModel) -> dict:
    """Deep-copy everything the model owns, for before/after comparisons."""
    return {
        "cells": copy.deepcopy(dict(m.cells)),
        "start_cell": m.start_cell,
        "zones": copy.deepcopy(m.zones),
        "palettes": copy.deepcopy(m.palettes),
        "sprites": copy.deepcopy(m.sprites),
        "manifest": copy.deepcopy(m.manifest),
    }


def free_coord(m: ProjectModel, start: int = -20) -> tuple:
    """Any unoccupied coord not too close to existing cells."""
    cells = set(m.cells)
    for y in range(start, start + 40):
        for x in range(start, start + 40):
            if (x, y) not in cells:
                return (x, y)
    raise AssertionError("no free coordinate found")


def edge_error_codes(m: ProjectModel) -> list:
    """Codes of validate errors that concern edge/door pairing."""
    pairing = {"open_edge_missing_neighbor", "open_edge_unmatched",
               "door_target_missing", "door_unmatched", "door_missing_id"}
    return sorted({i.code for i in validate.validate(m)
                   if i.severity == "error" and i.code in pairing})


def test_open_demo() -> None:
    print("\n== 1. open demo project ==")
    m = reset_copy()
    check("111 cells", len(m.cells) == 111)
    check("start cell (0, 0)", m.start_cell == (0, 0))
    check("palettes include blue_brinstar",
          "blue_brinstar" in m.palettes)
    check("zones count 5", len(m.zones) == 5)
    check("not dirty on open", not m.is_dirty)
    check("can_undo False", not m.can_undo)
    written = m.save()
    check("saving with no changes writes nothing", written == [])
    check("still not dirty after no-op save", not m.is_dirty)

    print("\n== 1b. byte-identical cell round trip ==")
    index = json.loads((PROJ_COPY / _COPY_PATHS["world_index"])
                       .read_text())
    tmp_out = SCRATCH / "roundtrip.json"
    mismatches = []
    for key, fname in index["cells"].items():
        x_str, y_str = key.split(",")
        coord = (int(x_str), int(y_str))
        original = (PROJ_COPY / _COPY_PATHS["cells_dir"] / fname) \
            .read_bytes()
        cell = m.cell(coord)
        save_cell(cell_from_dict(cell), tmp_out)
        regenerated = tmp_out.read_bytes()
        if regenerated != original:
            mismatches.append((coord, fname))
    if mismatches:
        coord, fname = mismatches[0]
        print(f"  NOTE: {len(mismatches)}/{len(index['cells'])} cells "
              f"regenerate differently (first: {coord} {fname}); the "
              f"files were not modified, so this is a pre-existing "
              f"serialisation difference, not a maker defect")
    check("save_cell output == original bytes for every cell",
          len(mismatches) == 0)


def test_ops_and_errors() -> None:
    print("\n== 2. every op at least once + error cases ==")
    m = reset_copy()
    newc = free_coord(m)

    # -- cells --
    r = ops.new_cell(m, newc, template="walled")
    check("new_cell created", m.has_cell(newc)
          and m.cell(newc)["tiles"][0][0]["kind"] == "solid"
          and m.cell(newc)["tiles"][5][5]["kind"] == "empty")
    check("new_cell duplicate raises", _raises(OpError,
          ops.new_cell, m, newc))
    ops.set_cell_props(m, newc, zone="Blue Brinstar",
                       background_color=[1, 2, 3])
    check("set_cell_props", m.cell(newc)["zone"] == "Blue Brinstar"
          and m.cell(newc)["background_color"] == [1, 2, 3])
    data = copy.deepcopy(m.cell((0, 0)))
    data["coord"] = [newc[0], newc[1]]
    data["edges"] = {}
    data["entities"] = []
    for row in data["tiles"]:
        for t in row:
            t["kind"] = "solid"
    ops.set_cell_data(m, newc, data)
    check("set_cell_data replaced", all(
        t["kind"] == "solid" for row in m.cell(newc)["tiles"]
        for t in row))
    bad = copy.deepcopy(data)
    bad["coord"] = [99, 99]
    check("set_cell_data coord mismatch raises", _raises(
        OpError, ops.set_cell_data, m, newc, bad))
    ops.set_start_cell(m, newc)
    check("set_start_cell", m.start_cell == newc)
    check("set_start_cell missing cell raises", _raises(
        OpError, ops.set_start_cell, m, free_coord(m, 100)))
    check("delete_cell start cell raises", _raises(
        OpError, ops.delete_cell, m, newc))
    check("delete_cell missing cell raises", _raises(
        OpError, ops.delete_cell, m, (99, 99)))

    movable = _pick_cell_for_move(m, exclude={newc})
    r = ops.move_cells(m, [movable], 1, 0)
    check("move_cells", r["moved"] == 1 and not m.has_cell(movable)
          and m.has_cell((movable[0] + 1, movable[1])))
    check("move_cells occupied dest raises", _raises(
        OpError, ops.move_cells, m, [(0, 0)], 1, 0))

    ops.new_cell(m, free_coord(m), template="empty")
    # duplicate pair (0,0)+(1,0) down into free row-1 coords
    ops.duplicate_cells(m, [(0, 0), (1, 0)], 0, 1)
    check("duplicate_cells", m.has_cell((0, 1)) and m.has_cell((1, 1))
          and m.cell((0, 1))["edges"]["east"]["type"] == "open"
          and m.cell((0, 1))["edges"]["north"]["type"] == "wall")
    check("duplicate_cells occupied raises", _raises(
        OpError, ops.duplicate_cells, m, [(0, 0)], 0, 1))
    ops.delete_cell(m, (0, 1))
    check("delete_cell", not m.has_cell((0, 1))
          and m.cell((1, 1))["edges"]["west"]["type"] == "wall")
    # duplicate a *door* pair: (0,0).north <-> (0,-1).south, verifying
    # preserved door ids are fresh and shared by both copy ends
    d = m.cell((0, 0))["edges"]["north"]
    orig_id = d["door_id"]
    ex, ey = _free_offset(m, [(0, 0), (0, -1)])
    ops.duplicate_cells(m, [(0, 0), (0, -1)], ex, ey)
    c0 = m.cell((ex, ey))
    c1 = m.cell((ex, ey - 1))
    c0n = c0["edges"]["north"]
    c1s = c1["edges"]["south"]
    check("duplicate door pair with fresh ids",
          c0n["type"] == "door" and c1s["type"] == "door"
          and c0n["door_id"] == c1s["door_id"]
          and c0n["door_id"] != orig_id)

    # -- tiles --
    ops.set_tiles(m, (0, 0), [[7, 1], [8, 1]], "spring")
    check("set_tiles", m.cell((0, 0))["tiles"][1][7]["kind"] == "spring")
    ops.fill_rect(m, (0, 0), 2, 2, 5, 4, "solid", palette="blue_brinstar")
    check("fill_rect", m.cell((0, 0))["tiles"][4][5]["kind"] == "solid")
    check("fill_rect out of range raises", _raises(
        OpError, ops.fill_rect, m, (0, 0), -10, -10, -5, -5, "solid"))
    n = ops.flood_fill(m, (0, 0), 2, 2, "empty")
    check("flood_fill filled region", n["filled"] >= 5)
    check("flood_fill bad seed raises", _raises(
        OpError, ops.flood_fill, m, (0, 0), 99, 0, "empty"))
    r = ops.paint_world(m, [[1, 1], [313, 0]], "crumble")
    check("paint_world spans cells", m.cell((0, 0))["tiles"][1][1][
        "kind"] == "crumble" and r["skipped"] == [[313, 0]])
    region = ops.get_region(m, 0, 0, 4, 3)
    check("get_region shape", len(region) == 3
          and all(len(row) == 4 for row in region))
    pasted = ops.put_region(m, 4, 4, region, skip_empty=True)
    check("put_region pasted", pasted["pasted"] >= 1)
    ops.replace_tiles(m, cells=[(0, 0)], from_kind="crumble",
                      to_kind="solid", to_palette="blue_brinstar")
    check("replace_tiles", m.cell((0, 0))["tiles"][1][1][
        "kind"] == "solid")
    check("replace_tiles with no target raises", _raises(
        OpError, ops.replace_tiles, m))
    ascii_rows = ops.cell_ascii(m, (0, 0))
    ops.set_cell_ascii(m, (0, 0), ascii_rows)
    check("cell_ascii/set_cell_ascii", len(ascii_rows) == 10
          and m.cell((0, 0))["tiles"][1][1]["kind"] == "solid")
    check("set_cell_ascii strict validation raises", _raises(
        OpError, ops.set_cell_ascii, m, (0, 0), ["x" * 16] * 9))

    # -- edges & doors --
    ops.set_edge(m, (0, 0), "east", "wall")
    check("set_edge wall both sides", m.cell((0, 0))["edges"]["east"][
        "type"] == "wall" and m.cell((1, 0))["edges"]["west"]["type"]
        == "wall")
    ops.set_edge(m, (0, 0), "east", "open")
    check("set_edge open both sides", m.cell((0, 0))["edges"]["east"][
        "type"] == "open" and m.cell((1, 0))["edges"]["west"]["type"]
        == "open")
    check("set_edge open without neighbour raises", _raises(
        OpError, ops.set_edge, m, (0, 0), "south", "open"))
    check("set_edge bad type raises", _raises(
        OpError, ops.set_edge, m, (0, 0), "east", "bogus"))
    r = ops.link_door(m, (0, 0), "east", (1, 0))
    check("link_door auto id", r["door_id"]
          and m.cell((0, 0))["edges"]["east"]["type"] == "door"
          and m.cell((1, 0))["edges"]["west"]["type"] == "door"
          and m.cell((0, 0))["edges"]["east"]["door_id"]
          == m.cell((1, 0))["edges"]["west"]["door_id"])
    check("link_door missing target raises", _raises(
        OpError, ops.link_door, m, (0, 0), "east", (90, 90)))
    ops.unlink_door(m, (0, 0), "east")
    check("unlink_door both wall", m.cell((0, 0))["edges"]["east"][
        "type"] == "wall" and m.cell((1, 0))["edges"]["west"]["type"]
        == "wall")
    check("unlink_door on wall is no-op",
          ops.unlink_door(m, (0, 0), "east")["changed"] is False)

    # -- entities --
    idx = ops.add_entity(m, (0, 0), "save_station", 3, 3,
                         props={"note": "hi"})["index"]

    def eidx(etyp):
        return next(i for i, e in enumerate(m.cell((0, 0))["entities"])
                    if e["type"] == etyp)

    check("add_entity", m.cell((0, 0))["entities"][idx]["note"] == "hi")
    ops.add_entity(m, (0, 0), "spawn_point", 2, 2)
    check("spawn_point moves start + removes others",
          m.start_cell == (0, 0)
          and len([e for e in m.cell((0, 0))["entities"]
                   if e["type"] == "spawn_point"]) == 1
          and len([e for e in m.cell((0, 0))["entities"]
                   if e["type"] == "save_station"]) == 1)
    sidx = eidx("save_station")
    ops.move_entity(m, (0, 0), sidx, 5, 5)
    check("move_entity", eidx("save_station") >= 0
          and m.cell((0, 0))["entities"][eidx("save_station")][
              "tile_x"] == 5)
    sidx = eidx("save_station")
    ops.update_entity(m, (0, 0), sidx, {"color": "red"})
    check("update_entity merge",
          m.cell((0, 0))["entities"][eidx("save_station")][
              "color"] == "red"
          and m.cell((0, 0))["entities"][eidx("save_station")][
              "note"] == "hi")
    sidx = eidx("save_station")
    ops.remove_entity(m, (0, 0), sidx)
    check("remove_entity",
          "save_station" not in [e["type"]
                                 for e in m.cell((0, 0))["entities"]]
          and eidx("spawn_point") == 0)
    check("remove_entity bad index raises", _raises(
        OpError, ops.remove_entity, m, (0, 0), 99))
    check("entity_types helper", "spawn_point" in ops.entity_types(m))
    check("add_entity bad tile raises", _raises(
        OpError, ops.add_entity, m, (0, 0), "x", 99, 0))

    # -- zones --
    ops.set_zone(m, "Test Zone", {"accent": [1, 2, 3],
                                  "fog_top": [4, 5, 6]})
    check("set_zone fills default keys",
          "Test Zone" in m.zones
          and len(m.zones["Test Zone"]["rock"]) == 8
          and m.zones["Test Zone"]["accent"] == [1, 2, 3])
    check("set_zone bad ramp raises", _raises(
        OpError, ops.set_zone, m, "Bad", {"rock": [[1, 2, 3]]}))
    ops.set_zone_color(m, "Test Zone", "cap", [9, 9, 9], index=0)
    check("set_zone_color", m.zones["Test Zone"]["cap"][0] == [9, 9, 9])
    ops.rename_zone(m, "Test Zone", "Test Zone 2")
    check("rename_zone", "Test Zone 2" in m.zones
          and "Test Zone" not in m.zones)
    ops.delete_zone(m, "Test Zone 2")
    check("delete_zone", "Test Zone 2" not in m.zones)
    check("delete_zone missing raises", _raises(
        OpError, ops.delete_zone, m, "Test Zone 2"))

    # -- palettes --
    ops.create_palette(m, "test_pal", base="blue_brinstar")
    check("create_palette", "test_pal" in m.palettes
          and m.palettes["test_pal"]["name"] == "test_pal")
    check("create_palette bad name raises", _raises(
        OpError, ops.create_palette, m, "Bad Name!"))
    colors = {"rock": [[i, i, i] for i in range(8)],
              "cap": [[i, i, i] for i in range(8)],
              "accent": [250, 250, 250]}
    ops.set_palette_colors(m, "test_pal", colors)
    check("set_palette_colors", m.palettes["test_pal"]["build"][
        "colors"]["accent"] == [250, 250, 250])
    ops.set_palette_color(m, "test_pal", "rock", 0, [1, 1, 1])
    check("set_palette_color", m.palettes["test_pal"]["build"][
        "colors"]["rock"][0] == [1, 1, 1])
    ops.set_tiles(m, (1, 0), [[0, 0]], "solid", palette="test_pal")
    ops.rename_palette(m, "test_pal", "test_pal2")
    check("rename_palette repoints tiles", "test_pal" not in m.palettes
          and "test_pal2" in m.palettes
          and m.cell((1, 0))["tiles"][0][0]["palette"] == "test_pal2")
    check("delete_palette used without replace raises", _raises(
        OpError, ops.delete_palette, m, "test_pal2"))
    ops.delete_palette(m, "test_pal2", replace_with="blue_brinstar")
    check("delete_palette with replace", "test_pal2" not in m.palettes
          and m.cell((1, 0))["tiles"][0][0]["palette"]
          == "blue_brinstar")

    # -- sprites --
    spec = {"name": "test_spr", "colors": {"a": [1, 2, 3]},
            "heights": {}, "voxel_px": 1,
            "animations": {"idle": {"frame_ticks": 2,
                                    "frames": [["a", "a"],
                                               ["a", "a"],
                                               ["a", "a"],
                                               ["a", "a"]]}}}
    ops.set_sprite_data(m, "test_spr", spec)
    check("set_sprite_data", m.sprites["test_spr"]["name"] == "test_spr")
    bad_spec = copy.deepcopy(spec)
    bad_spec["animations"]["idle"]["frames"] = [["a", "b"], ["a", "a"],
                                                ["a", "a"], ["a", "a"]]
    check("set_sprite_data invalid raises", _raises(
        OpError, ops.set_sprite_data, m, "test_spr", bad_spec))
    ops.delete_sprite(m, "test_spr")
    check("delete_sprite", "test_spr" not in m.sprites)

    # -- manifest --
    ops.set_manifest_value(m, "title", "Maker Test")
    check("set_manifest_value", m.manifest["title"] == "Maker Test")
    check("set_manifest_value format protected", _raises(
        OpError, ops.set_manifest_value, m, "format", 1))
    check("set_manifest_value bad path raises", _raises(
        OpError, ops.set_manifest_value, m, "paths", {"bogus": "x"}))
    ops.set_manifest_value(m, "paths", {"zones": "z2.json"})
    check("set_manifest_value paths merged", m.manifest["paths"][
        "zones"] == "z2.json" and m.manifest["paths"]["cells_dir"]
        == _COPY_PATHS["cells_dir"])

    # -- undo still works after everything above --
    check("ops left model consistent (no edge errors)",
          edge_error_codes(m) == [])


def _raises(exc_type, fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
        return False
    except exc_type:
        return True


def _pick_cell_for_move(m: ProjectModel, exclude=()) -> tuple:
    """A cell (not (0,0), not in `exclude`) with a free destination one
    east."""
    for c in sorted(m.cells):
        if c not in exclude and c != (0, 0) \
                and not m.has_cell((c[0] + 1, c[1])):
            return c
    raise AssertionError("no movable cell found")


def _pick_other(m: ProjectModel, exclude: set) -> tuple:
    """First sorted cell not in `exclude` (usable as a delete victim)."""
    for c in sorted(m.cells):
        if c not in exclude:
            return c
    raise AssertionError("no other cell found")


def _free_offset(m: ProjectModel, coords: list) -> tuple:
    """A uniform (dx, dy) whose destinations for `coords` are all free."""
    for dy in range(-12, 13):
        for dx in range(-12, 13):
            if (dx, dy) == (0, 0):
                continue
            if all(not m.has_cell((c[0] + dx, c[1] + dy))
                   for c in coords):
                return (dx, dy)
    raise AssertionError("no free uniform offset found")


def test_undo_redo() -> None:
    print("\n== 3. undo / redo ==")
    m = reset_copy()
    before = model_state(m)
    ops.set_tiles(m, (0, 0), [[9, 9]], "spring")
    ops.update_entity(m, (0, 0), 0, {"touched": True})
    ops.link_door(m, (0, 0), "east", (1, 0))
    movable = _pick_cell_for_move(m, exclude={(0, 0)})
    ops.move_cells(m, [movable], 1, 0)
    ops.set_zone_color(m, "Blue Brinstar", "accent", [10, 20, 30])
    ops.set_manifest_value(m, "title", "Undo Me")
    after = model_state(m)
    check("ops recorded a step each", len(m._history.undo_stack) == 6)
    while m.can_undo:
        m.undo()
    check("undo all -> original state", model_state(m) == before)
    while m.can_redo:
        m.redo()
    check("redo all -> post-op state", model_state(m) == after)

    # nested transaction merges into one step
    m2 = reset_copy()
    s0 = model_state(m2)
    with m2.transaction("outer"):
        ops.set_tiles(m2, (0, 0), [[3, 3]], "solid")
        ops.add_entity(m2, (0, 0), "save_station", 1, 1)
    s1 = model_state(m2)
    check("nested txs -> one step", len(m2._history.undo_stack) == 1
          and m2._history.undo_label == "outer")
    m2.undo()
    check("outer undo reverts both", model_state(m2) == s0)
    m2.redo()
    check("outer redo reapplies both", model_state(m2) == s1)

    # exception rolls back the whole transaction
    m3 = reset_copy()
    s0 = model_state(m3)
    try:
        with m3.transaction("boom"):
            ops.set_tiles(m3, (0, 0), [[7, 7]], "solid")
            raise RuntimeError("fail the transaction")
    except RuntimeError:
        pass
    check("exception rolls back", model_state(m3) == s0
          and len(m3._history.undo_stack) == 0)


def test_edge_invariants() -> None:
    print("\n== 4. edge pairing invariants after edge ops ==")
    m = reset_copy()
    check("baseline has no edge/door errors", edge_error_codes(m) == [])
    ops.set_edge(m, (0, 0), "east", "wall")
    ops.set_edge(m, (0, 0), "east", "open")
    ops.link_door(m, (0, 0), "east", (1, 0))
    ops.unlink_door(m, (0, 0), "east")
    movable = _pick_cell_for_move(m)
    ops.move_cells(m, [movable], 1, 0)
    ex, ey = _free_offset(m, [(0, 0), (1, 0)])
    ops.duplicate_cells(m, [(0, 0), (1, 0)], ex, ey)
    victim = _pick_other(m, {(0, 0), movable,
                             (movable[0] + 1, movable[1])})
    ops.delete_cell(m, victim)
    check("no new edge/door errors introduced",
          edge_error_codes(m) == [])


def test_ascii_roundtrip() -> None:
    print("\n== 5. ascii round trip is a no-op ==")
    m = reset_copy()
    before = copy.deepcopy(m.cell((0, 0)))
    steps = len(m._history.undo_stack)
    rows = ops.cell_ascii(m, (0, 0))
    ops.set_cell_ascii(m, (0, 0), rows)
    check("data unchanged", m.cell((0, 0)) == before)
    check("no undo step recorded", len(m._history.undo_stack) == steps)


def test_save_reopen() -> None:
    print("\n== 6. save + reopen ==")
    m = reset_copy()
    newc = free_coord(m)
    ops.new_cell(m, newc, zone="Blue Brinstar", template="walled",
                 palette="blue_brinstar")
    ops.set_tiles(m, (0, 0), [[10, 9]], "oneway")
    move_cell = _pick_cell_for_move(m, exclude={newc})
    ops.move_cells(m, [move_cell], 1, 0)
    victim = _pick_other(m, {move_cell,
                             (move_cell[0] + 1, move_cell[1]),
                             newc, (0, 0)})
    ops.delete_cell(m, victim)
    ops.set_start_cell(m, (move_cell[0] + 1, move_cell[1]))
    ops.create_palette(m, "test_pal")
    ops.set_palette_color(m, "test_pal", "rock", 0,
                          [3, 3, 3])
    ops.rename_palette(m, "red_brinstar", "red_b2")
    ops.set_zone_color(m, "Blue Brinstar", "ambient", [1, 2, 3])
    ops.set_sprite_data(m, "test_spr", {
        "name": "test_spr", "colors": {"a": [1, 2, 3]}, "heights": {},
        "voxel_px": 1,
        "animations": {"idle": {"frame_ticks": 2,
                                "frames": [["a"] * 4] * 4}}})
    ops.set_manifest_value(m, "title", "Saved Title")
    check("dirty before save", m.is_dirty)

    written = m.save()
    check("save wrote files", len(written) > 0)
    check("clean after save", not m.is_dirty)
    check("second save writes nothing", m.save() == [])

    cells_dir = PROJ_COPY / _COPY_PATHS["cells_dir"]
    check("moved cell file renamed",
          (cells_dir / f"{move_cell[0]}_{move_cell[1]}.json").exists()
          is False
          and (cells_dir / f"{move_cell[0] + 1}_{move_cell[1]}.json")
          .exists())
    check("deleted cell file removed",
          not (cells_dir / f"{victim[0]}_{victim[1]}.json").exists())
    check("new cell file written",
          (cells_dir / f"{newc[0]}_{newc[1]}.json").exists())
    index = json.loads((PROJ_COPY / _COPY_PATHS["world_index"])
                       .read_text())
    check("world index updated", index["start_cell"] == [
        move_cell[0] + 1, move_cell[1]]
          and f"{move_cell[0]},{move_cell[1]}" not in index["cells"]
          and f"{move_cell[0] + 1},{move_cell[1]}" in index["cells"]
          and f"{victim[0]},{victim[1]}" not in index["cells"]
          and f"{newc[0]},{newc[1]}" in index["cells"])
    check("palette written",
          (PROJ_COPY / _COPY_PATHS["palettes_dir"] / "test_pal.json")
          .exists())
    check("renamed palette file + texture dir",
          (PROJ_COPY / _COPY_PATHS["palettes_dir"] / "red_b2.json")
          .exists()
          and not (PROJ_COPY / _COPY_PATHS["palettes_dir"]
                   / "red_brinstar.json").exists()
          and (PROJ_COPY / _COPY_PATHS["textures_dir"] / "red_b2")
          .exists()
          and not (PROJ_COPY / _COPY_PATHS["textures_dir"]
                   / "red_brinstar").exists())
    check("zones written", json.loads(
        (PROJ_COPY / _COPY_PATHS["zones"]).read_text()
    )["Blue Brinstar"]["ambient"] == [1, 2, 3])
    check("sprite written", (PROJ_COPY / _COPY_PATHS["sprite_src_dir"]
                             / "test_spr.json").exists())
    check("manifest written", json.loads(
        (PROJ_COPY / "project.json").read_text())["title"]
        == "Saved Title")

    m2 = ProjectModel.open(PROJ_COPY)
    check("reopen: cells match", all(
        m2.cell(c) == m.cell(c) for c in m.cells)
        and len(m2.cells) == len(m.cells))
    check("reopen: start cell", m2.start_cell
          == (move_cell[0] + 1, move_cell[1]))
    check("reopen: palettes/zones/sprites/manifest",
          "test_pal" in m2.palettes and "red_b2" in m2.palettes
          and "red_brinstar" not in m2.palettes
          and m2.zones["Blue Brinstar"]["ambient"] == [1, 2, 3]
          and "test_spr" in m2.sprites
          and m2.manifest["title"] == "Saved Title")
    check("reopen: not dirty", not m2.is_dirty)


def test_validate_demo() -> None:
    print("\n== 7. validate() on the untouched demo ==")
    m = reset_copy()
    issues = validate.validate(m)
    counts = Counter((i.severity, i.code) for i in issues)
    for sev in ("error", "warning", "info"):
        code_counts = {code: n for (s, code), n in sorted(counts.items())
                       if s == sev}
        print(f"  {sev}: {sum(code_counts.values())} "
              f"{dict(sorted(code_counts.items()))}")
    pairing = {"open_edge_missing_neighbor", "open_edge_unmatched",
               "door_target_missing", "door_unmatched", "door_missing_id"}
    pairing_errors = [i for i in issues
                      if i.severity == "error" and i.code in pairing]
    check("0 edge/door pairing errors on demo data",
          len(pairing_errors) == 0)
    check("no errors at all on demo data",
          sum(1 for i in issues if i.severity == "error") == 0)
    for issue in issues:
        print(f"    {issue.severity} {issue.code}: {issue.message}")


def test_listener() -> None:
    print("\n== 8. listener ChangeSet ==")
    m = reset_copy()
    received = []
    m.add_listener(received.append)
    ops.set_tiles(m, (0, 0), [[4, 4]], "spring")
    check("commit ChangeSet cells", received[-1].cells == {(0, 0)}
          and received[-1].label == "set_tiles")
    ops.link_door(m, (0, 0), "east", (1, 0))
    check("commit ChangeSet two cells", received[-1].cells
          == {(0, 0), (1, 0)})
    ops.set_zone_color(m, "Blue Brinstar", "accent", [5, 5, 5])
    check("zone ChangeSet", received[-1].zones is True)
    ops.set_manifest_value(m, "title", "L")
    check("manifest ChangeSet", received[-1].manifest is True)
    ops.create_palette(m, "listener_pal")
    check("palette ChangeSet", received[-1].palettes
          == {"listener_pal"})
    m.undo()
    check("undo fires listener", received[-1].label == "undo:create_palette"
          and received[-1].palettes == {"listener_pal"})


def main() -> None:
    test_open_demo()
    test_ops_and_errors()
    test_undo_redo()
    test_edge_invariants()
    test_ascii_roundtrip()
    test_save_reopen()
    test_validate_demo()
    test_listener()
    print("\n== summary ==")
    print(f"checks failed: {len(failures)}")
    for label in failures:
        print(f"  FAIL: {label}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
