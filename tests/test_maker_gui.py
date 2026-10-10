"""Headless end-to-end checks for the Maker GUI (python -m maker).

Drives the real App with synthetic pygame events (SDL dummy driver) on a
COPY of the demo project under .maker_tmp/, checks the model after each
interaction and saves screenshots to screenshots/maker_gui_*.png.

Run with:  .venv/bin/python -m tests.test_maker_gui
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ["SDL_AUDIODRIVER"] = "dummy"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

import pygame  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TMP = ROOT / ".maker_tmp" / "gui_test"
SHOTS = ROOT / "screenshots"

failures: list = []


def check(label, cond) -> bool:
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        failures.append(label)
    return cond


class Driver:
    def __init__(self, app) -> None:
        self.app = app

    def frame(self, n=1) -> None:
        for _ in range(n):
            self.app.step([])

    def send(self, *events) -> None:
        self.app.step(list(events))

    def move(self, pos, buttons=(0, 0, 0)) -> None:
        self.send(pygame.event.Event(pygame.MOUSEMOTION, pos=pos, rel=(0, 0),
                                     buttons=buttons))

    def click(self, pos, button=1, mods=0) -> None:
        self.app.mods = mods
        self.move(pos)
        self.send(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=pos,
                                     button=button))
        self.send(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=pos,
                                     button=button))
        self.app.mods = 0

    def double_click(self, pos) -> None:
        self.click(pos)
        self.click(pos)

    def drag(self, points, button=1, mods=0) -> None:
        self.app.mods = mods
        self.move(points[0])
        self.send(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=points[0],
                                     button=button))
        for p in points[1:]:
            self.move(p, (1, 0, 0))
        self.send(pygame.event.Event(pygame.MOUSEBUTTONUP, pos=points[-1],
                                     button=button))
        self.app.mods = 0

    def key(self, key, mod=0) -> None:
        self.send(pygame.event.Event(pygame.KEYDOWN, key=key, mod=mod,
                                     unicode="", scancode=0))
        self.send(pygame.event.Event(pygame.KEYUP, key=key, mod=mod,
                                     unicode="", scancode=0))

    def type(self, text) -> None:
        self.send(pygame.event.Event(pygame.TEXTINPUT, text=text))

    def shot(self, name) -> Path:
        self.frame(2)
        SHOTS.mkdir(exist_ok=True)
        path = SHOTS / f"maker_gui_{name}.png"
        pygame.image.save(self.app.screen, str(path))
        return path

    def button(self, container, label):
        for w in walk(container):
            text = getattr(w, "text", None)
            text = text() if callable(text) else text
            if isinstance(text, str) and text.startswith(label) and \
                    hasattr(w, "on_click"):
                return w
        raise AssertionError(f"button {label!r} not found")

    def menu_item(self, label_prefix):
        menu = self.app.menu
        assert menu is not None, "no menu open"
        for item, rect in menu._rows():
            if item and item[0].startswith(label_prefix):
                return rect.center
        raise AssertionError(f"menu item {label_prefix!r} not found")


def walk(c):
    for ch in getattr(c, "children", []):
        yield ch
        yield from walk(ch)


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def main() -> int:
    from maker.core.model import ProjectModel
    from maker.core.project_ops import create_project
    from src.project import ENGINE_ROOT

    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    proj = TMP / "demo"
    create_project(proj, "guitest", "GUI Test", template="copy",
                   source=ENGINE_ROOT)
    pygame.init()
    model = ProjectModel.open(proj)
    original = {c: copy.deepcopy(model.cell(c)) for c in model.cell_coords()}

    from maker.gui.app import App
    app = App(model, (1440, 900), headless=True)
    d = Driver(app)
    rv = app.room_view
    d.frame(2)
    check("room view opened the start cell",
          rv.room is not None and model.start_cell in rv.room.cells)
    check("no validation errors at start",
          not [i for i in app.issues if i.severity == "error"])
    d.shot("room")

    # ---- paint stroke = one undo step --------------------------------------
    start = model.start_cell
    cx, cy = start
    base = (cx * 16, cy * 10)
    d.key(pygame.K_b)
    d.key(pygame.K_2)  # solid
    pts = [rv.tile_rect((base[0] + x, base[1] + 3)).center for x in range(3, 9)]
    mark = model.history_mark()
    d.drag(pts)
    row = model.cell(start)["tiles"][3]
    check("paint drag painted 6 tiles",
          all(row[x]["kind"] == "solid" for x in range(3, 9)))
    check("paint drag is ONE undo step", model.history_mark() == mark + 1)
    d.key(pygame.K_z, pygame.KMOD_META)
    check("undo restores the stroke",
          model.cell(start)["tiles"][3] == original[start]["tiles"][3])
    d.key(pygame.K_z, pygame.KMOD_META | pygame.KMOD_SHIFT)
    check("redo re-applies it", model.cell(start)["tiles"][3][5]["kind"] == "solid")

    # ---- rectangle tool -----------------------------------------------------
    d.key(pygame.K_r)
    d.key(pygame.K_4)  # oneway
    a = rv.tile_rect((base[0] + 10, base[1] + 2)).center
    b = rv.tile_rect((base[0] + 12, base[1] + 4)).center
    d.drag([a, b])
    t = model.cell(start)["tiles"]
    check("rect tool filled 3x3 oneway",
          all(t[y][x]["kind"] == "oneway" for y in range(2, 5)
              for x in range(10, 13)))

    # ---- eyedropper + fill ---------------------------------------------------
    d.key(pygame.K_i)
    d.click(rv.tile_rect((base[0] + 4, base[1] + 3)).center)
    check("eyedropper picks solid", app.brush_kind == "solid")

    # ---- entity tool: place, move, delete -------------------------------------
    n_before = len(model.cell(start)["entities"])
    app.entity_type = "save_station"
    d.key(pygame.K_n)
    d.click(rv.tile_rect((base[0] + 6, base[1] + 7)).center)
    ents = model.cell(start)["entities"]
    check("entity tool placed a save_station",
          len(ents) == n_before + 1 and ents[-1]["type"] == "save_station")
    idx = len(ents) - 1
    check("new entity is selected",
          app.sel and app.sel["kind"] == "entity" and app.sel["index"] == idx)
    d.drag([rv.tile_rect((base[0] + 6, base[1] + 7)).center,
            rv.tile_rect((base[0] + 9, base[1] + 7)).center])
    e = model.cell(start)["entities"][idx]
    check("drag moves the entity", (e["tile_x"], e["tile_y"]) == (9, 7))
    d.shot("entity")

    # ---- inspector form: entity property via text field ------------------------
    insp = app.panels["Inspect"]
    tx_field = None
    for w in walk(insp):
        if type(w).__name__ == "TextInput" and w.get_value and \
                w.get_value() == "9":
            tx_field = w
    if check("inspector shows tile_x field", tx_field is not None):
        d.click(tx_field.rect.center)
        d.key(pygame.K_BACKSPACE)
        d.type("11")
        d.key(pygame.K_RETURN)
        check("editing tile_x moves the entity",
              model.cell(start)["entities"][idx]["tile_x"] == 11)

    # ---- raw JSON tab ------------------------------------------------------------
    app.tabs.select("JSON")
    jp = app.panels["JSON"]
    d.frame()
    data = json.loads(jp.area.text)
    check("JSON tab shows the selected entity", data.get("type") == "save_station")
    data["note"] = "hello"
    jp.area.set_text(json.dumps(data, indent=2))
    jp.area.dirty = True
    d.click(d.button(jp, "Apply").rect.center)
    check("JSON apply updates the entity",
          model.cell(start)["entities"][idx].get("note") == "hello")
    jp.area.set_text("{ broken")
    jp.area.dirty = True
    d.click(d.button(jp, "Apply").rect.center)
    check("invalid JSON is rejected with a message",
          "invalid JSON" in jp.msg and
          model.cell(start)["entities"][idx].get("note") == "hello")
    d.shot("json")
    d.key(pygame.K_DELETE)  # focus is not on the area -> deletes entity?
    app.set_focus(None)
    app.select_entity(start, idx)
    d.key(pygame.K_DELETE)
    check("Delete key removes the selected entity",
          len(model.cell(start)["entities"]) == n_before)

    # ---- edge menu -------------------------------------------------------------
    app.tabs.select("Inspect")
    app.select_cell(start)
    d.key(pygame.K_e)
    east_mid = rv.tile_rect((base[0] + 15, base[1] + 5)).center
    d.click(east_mid)
    check("edge tool opens a menu", app.menu is not None)
    if app.menu is not None:
        d.click(d.menu_item("Wall"))
        check("menu 'Wall' walls the east edge",
              model.cell(start)["edges"].get("east", {}).get("type", "wall")
              == "wall" and (cx + 1, cy) not in rv.room.cells)
        d.key(pygame.K_z, pygame.KMOD_META)
        d.frame()
        check("undo re-opens it", (cx + 1, cy) in rv.room.cells)

    # ---- world map ----------------------------------------------------------------
    d.key(pygame.K_TAB)
    check("Tab switches to the world map", app.mode == "map")
    mv = app.map_view
    mv.fit()
    d.frame()
    b0 = model.bounds()
    empty = (b0[2] + 1, b0[1])
    d.click(mv.cell_rect(empty).center)
    d.key(pygame.K_n)
    check("map: N creates a cell on the chosen empty square",
          model.has_cell(empty))
    mv.fit()
    d.frame()
    moved = (empty[0], empty[1] + 1)
    mv._last_click = (0, None)  # not a double-click
    d.drag([mv.cell_rect(empty).center, mv.cell_rect(moved).center])
    check("map: drag moves the cell", model.has_cell(moved)
          and not model.has_cell(empty))
    d.shot("map")
    mv._last_click = (0, None)
    d.double_click(mv.cell_rect(moved).center)
    check("map: double-click opens the room", app.mode == "room"
          and rv.focus_cell == moved)
    app.delete_cells([moved])
    d.key(pygame.K_RETURN)  # confirm dialog
    check("delete cell (confirmed)", not model.has_cell(moved))

    # ---- palettes ----------------------------------------------------------------
    app.open_room(start)
    app.tabs.select("Palettes")
    pp = app.panels["Palettes"]
    pp.current = "blue_brinstar"
    pp.rebuild()
    d.frame()
    solid = proj / "textures" / "blue_brinstar" / "solid.png"
    h0 = md5(solid)
    swatches = [w for w in walk(pp.form) if type(w).__name__ == "Swatch"]
    d.click(swatches[7].rect.center)  # rock[7]
    check("swatch click selects rock[7]", pp.colors.sel == ("rock", 7))
    slider = [w for w in walk(pp.form) if type(w).__name__ == "Slider"][0]
    d.drag([slider.rect.midleft, slider.rect.midright])
    check("slider commit sets rock[7].r = 255",
          model.palettes["blue_brinstar"]["build"]["colors"]["rock"][7][0] == 255)
    check("palette edit rebuilt solid.png", md5(solid) != h0)
    d.shot("palettes")

    # ---- zones -----------------------------------------------------------------
    app.tabs.select("Zones")
    zp = app.panels["Zones"]
    zname = model.cell(start)["zone"]
    zp.current = zname
    zp.colors.select("accent", None)
    d.frame()
    hexfield = [w for w in walk(zp.form) if type(w).__name__ == "TextInput"][-1]
    d.click(hexfield.rect.center)
    hexfield.value = ""
    hexfield.cursor = 0
    d.type("#ff0080")
    d.key(pygame.K_RETURN)
    check("zone accent set via hex field",
          model.zones[zname]["accent"] == [255, 0, 128])
    d.shot("zones")

    # ---- problems -------------------------------------------------------------
    sp = [i for i, e in enumerate(model.cell(start)["entities"])
          if e["type"] == "spawn_point"]
    app.delete_entity(start, sp[0])
    app.tabs.select("Problems")
    d.frame(2)
    check("problems list shows the missing spawn error",
          any(i.code == "no_spawn_point" for i in app.issues)
          and "no spawn_point" in app.panels["Problems"].list.items[0][0])
    d.shot("problems")
    d.key(pygame.K_z, pygame.KMOD_META)
    d.frame()
    check("undo clears the error",
          not any(i.code == "no_spawn_point" for i in app.issues))

    # ---- sprites tab renders ----------------------------------------------------
    app.tabs.select("Sprites")
    d.frame()
    check("sprites tab lists sprites", len(app.panels["Sprites"].list.items) >= 1)
    d.shot("sprites")

    # ---- save ----------------------------------------------------------------
    d.key(pygame.K_s, pygame.KMOD_META)
    check("Cmd+S saves", not model.is_dirty)
    disk = json.loads((proj / "world/cells" /
                       f"{start[0]}_{start[1]}.json").read_text())
    check("saved file has the painted row",
          disk["tiles"][3][5]["kind"] == "solid")

    # ---- build game (dialog) -------------------------------------------------------
    app.build_game()
    dlg = app.dialogs[-1]
    out = TMP / "built"
    dlg.inputs[0].value = str(out)
    d.key(pygame.K_RETURN)
    check("Build Game exports a playable folder",
          (out / "play.sh").exists() and (out / "world/world_index.json").exists())
    if app.dialogs:
        d.key(pygame.K_RETURN)  # close the result message

    # ---- playtest -------------------------------------------------------------------
    app.playtest(start, (8, 7))
    time.sleep(3)
    procs = list(app._playtests)
    alive = bool(procs) and procs[0][0].poll() is None
    for proc, log in procs:
        proc.kill()
        proc.wait()
        log.close()
    app._playtests = []
    log_text = (ROOT / ".maker_tmp" / "playtest.log").read_text()
    check("playtest process runs without a traceback",
          alive and "Traceback" not in log_text)

    # ---- undo everything back to the original data ------------------------------------
    while model.can_undo:
        model.undo()
    d.frame()
    same = all(model.cell(c) == original[c] for c in original) and \
        set(model.cell_coords()) == set(original)
    check("undoing everything restores the original cells", same)

    print()
    print(f"{len(failures)} failure(s)")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
