"""Metroidvania Maker main window.

Layout: toolbar (top), tool strip (left), Room Editor / World Map
viewport (centre), data pane with tabs (right), status bar (bottom).

Every edit goes through App.run() -> a maker.core.ops function, so
edits are transactional, undoable and identical to the CLI/API. The
model's change listener re-renders views, rebuilds panels, rebuilds the
textures of edited palettes and re-validates.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Optional

import pygame

from maker.core import ops, render, validate
from maker.core.errors import OpError
from maker.core.model import ProjectModel
from maker.core.project_ops import create_project, export_game, playtest_command
from maker.gui import theme as T
from maker.gui.mapview import MapView
from maker.gui.panels import (
    InspectPanel, JsonPanel, PalettesPanel, ProblemsPanel, ProjectPanel,
    SpritesPanel, ZonesPanel,
)
from maker.gui.roomview import TOOLS, RoomView
from maker.gui.widgets import (
    Button, Container, Dialog, Label, ListView, PopupMenu, TabBar, cmd_down,
)
from maker.state import save_last_project
from src.project import PROJECTS_DIR

MAKER_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = MAKER_ROOT / ".maker_tmp"
KINDS = ("empty", "solid", "spring", "oneway", "crumble")
AUTOSAVE_SECONDS = 120
OPPOSITE = {"north": "south", "south": "north", "west": "east", "east": "west"}
DELTA = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


class App:
    def __init__(self, model: ProjectModel, size=(1440, 900),
                 headless: bool = False) -> None:
        self.headless = headless
        self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.key.set_repeat(350, 35)
        pygame.key.start_text_input()
        self.clock = pygame.time.Clock()
        self.running = True
        # input state
        self.mouse = (0, 0)
        self.mods = 0
        self.space_down = False
        self.focus = None
        self.menu: Optional[PopupMenu] = None
        self.dialogs: list = []
        self._clipboard = ""
        # status
        self.status_msg = ""
        self.status_color = T.TEXT_DIM
        self.status_time = 0.0
        self.last_error = ""
        self.hover_info = ""
        self._busy = None
        # options
        self.autosave = False
        self.wheel_zooms = False
        self._last_autosave = time.time()
        self._playtests: list = []
        self._hover_since = (None, 0)
        self.load_model(model)

    # -- model / components ---------------------------------------------------

    def load_model(self, model: ProjectModel) -> None:
        self.model = model
        render.activate(model)
        model.add_listener(self._on_change)
        self.brush_kind = "solid"
        usage = model.palette_usage()
        self.brush_palette = None  # None = auto (cell's dominant palette)
        self.entity_type = "save_station"
        self.sel = None
        self.mode = "room"
        self.issues = []
        self._validate_pending = True
        self.room_view = RoomView(self)
        self.map_view = MapView(self)
        self.panels = {}
        for cls in (InspectPanel, JsonPanel, ProblemsPanel, PalettesPanel,
                    ZonesPanel, SpritesPanel, ProjectPanel):
            p = cls(self)
            self.panels[p.title] = p
        self.tabs = TabBar(list(self.panels), on_change=self._tab_changed)
        self._build_chrome()
        self.layout()
        start = model.start_cell if model.has_cell(model.start_cell or (0, 0)) \
            else (model.cell_coords() or [None])[0]
        if start is not None:
            self.room_view.open(start)
            self.select_cell(start)
        self.revalidate()
        self.map_view.fit()
        for p in self.panels.values():
            p.rebuild()
        title = model.manifest.get("title", model.project.root.name)
        pygame.display.set_caption(f"Metroidvania Maker — {title}")
        self.status(f"opened {model.project.root}  ({len(model.cells)} cells,"
                    f" most-used palette: "
                    f"{max(usage, key=usage.get) if usage else '-'})")
        if not self.headless:
            save_last_project(model.project.root)

    def _build_chrome(self) -> None:
        self.toolbar = Container()
        tb = self.toolbar
        self.tb_buttons = [
            Button("Room", lambda: self.set_mode("room"),
                   selected=lambda: self.mode == "room",
                   tooltip="Room Editor (Tab)"),
            Button("World Map", lambda: self.set_mode("map"),
                   selected=lambda: self.mode == "map",
                   tooltip="World Map (Tab)"),
            None,
            Button("Undo", self.undo, enabled=lambda: self.model.can_undo,
                   tooltip=lambda: f"Undo {self.model.undo_label or ''} (Cmd+Z)"),
            Button("Redo", self.redo, enabled=lambda: self.model.can_redo,
                   tooltip=lambda: f"Redo {self.model.redo_label or ''} (Shift+Cmd+Z)"),
            Button(lambda: "Save*" if self.model.is_dirty else "Save", self.save,
                   enabled=lambda: self.model.is_dirty, tooltip="Save (Cmd+S)"),
            None,
            Button("Playtest", lambda: self.playtest_here(),
                   tooltip="Save + run the game from the hovered/selected tile (F5)"),
            Button("Build Game", self.build_game,
                   tooltip="Export a standalone game folder"),
            None,
            Button("Grid", lambda: self._toggle_overlay("grid"),
                   selected=lambda: self.room_view.overlays["grid"],
                   tooltip="tile grid (Cmd+G)"),
            Button("Kinds", lambda: self._toggle_overlay("kinds"),
                   selected=lambda: self.room_view.overlays["kinds"],
                   tooltip="colour tiles by kind (Cmd+K)"),
            Button("Edges", lambda: self._toggle_overlay("edges"),
                   selected=lambda: self.room_view.overlays["edges"],
                   tooltip="open edges / doors"),
            Button("Entities", lambda: self._toggle_overlay("entities"),
                   selected=lambda: self.room_view.overlays["entities"]),
            Button("Issues", lambda: self._toggle_overlay("issues"),
                   selected=lambda: self.room_view.overlays["issues"]),
            Button("Light", self.toggle_lighting,
                   selected=lambda: self.room_view.lighting,
                   tooltip="game lighting in the editor view (Cmd+L)"),
        ]
        for b in self.tb_buttons:
            if b is not None:
                tb.add(b)
        # tool strip
        self.strip = Container()
        st = self.strip
        self.tool_buttons = []
        for tid, label, key, help_ in TOOLS:
            b = Button(f"{label}  ({key.upper()})",
                       lambda t=tid: self.set_tool(t),
                       selected=lambda t=tid: self.room_view.tool == t,
                       tooltip=help_, align="left", size=12)
            self.tool_buttons.append(st.add(b))
        self.kind_label = st.add(Label("Brush kind", T.ACCENT, 12, bold=True))
        self.kind_buttons = []
        for i, k in enumerate(KINDS):
            b = Button(f"{i + 1} {k}", lambda k=k: self.set_brush(k, self.brush_palette),
                       selected=lambda k=k: self.brush_kind == k,
                       swatch=T.KIND_COLORS[k], size=12, align="left")
            self.kind_buttons.append(st.add(b))
        self.pal_label = st.add(Label("Brush palette", T.ACCENT, 12, bold=True))
        self.pal_list = st.add(ListView(on_select=self._pick_palette, row_h=18))
        self.ent_label = st.add(Label("Entity type", T.ACCENT, 12, bold=True))
        self.ent_list = st.add(ListView(on_select=self._pick_entity_type,
                                        row_h=18))
        self._refresh_strip_lists()

    def _refresh_strip_lists(self) -> None:
        self.pal_list.set_items([("(auto: zone default)", "__auto__")] +
                                [(n, n) for n in sorted(self.model.palettes)])
        self.pal_list.selected_value = self.brush_palette or "__auto__"
        self.ent_list.set_items([(t, t) for t in ops.entity_types(self.model)])
        self.ent_list.selected_value = self.entity_type

    def layout(self) -> None:
        w, h = self.screen.get_size()
        self.size = (w, h)
        x = 6
        for b in self.tb_buttons:
            if b is None:
                x += 10
                continue
            label = b.text() if callable(b.text) else b.text
            bw = max(52, T.font(13).size(label + "*")[0] + 20)
            b.layout((x, 5, bw, T.TOOLBAR_H - 10))
            x += bw + 4
        top = T.TOOLBAR_H
        bottom = h - T.STATUS_H
        y = top + 6
        for b in self.tool_buttons:
            b.layout((6, y, T.TOOLSTRIP_W - 12, 22))
            y += 25
        y += 6
        self.kind_label.layout((8, y, T.TOOLSTRIP_W - 12, 18))
        y += 20
        for b in self.kind_buttons:
            b.layout((6, y, T.TOOLSTRIP_W - 12, 22))
            y += 25
        y += 6
        rest = bottom - y - 52
        self.pal_label.layout((8, y, T.TOOLSTRIP_W - 12, 18))
        ph = max(60, rest // 2)
        self.pal_list.layout((6, y + 20, T.TOOLSTRIP_W - 12, ph))
        y += 26 + ph
        self.ent_label.layout((8, y, T.TOOLSTRIP_W - 12, 18))
        self.ent_list.layout((6, y + 20, T.TOOLSTRIP_W - 12,
                              max(40, bottom - y - 26)))
        vx = T.TOOLSTRIP_W
        vw = w - T.TOOLSTRIP_W - T.INSPECTOR_W
        view_rect = pygame.Rect(vx, top, vw, bottom - top)
        old = self.room_view.rect.size
        self.room_view.layout(view_rect)
        self.map_view.layout(view_rect)
        if old != view_rect.size and old != (0, 0):
            self.room_view.fit()
        ix = w - T.INSPECTOR_W
        self.tabs.layout((ix + 4, top + 4, T.INSPECTOR_W - 8, 24))
        self.panel_rect = pygame.Rect(ix, top + 30, T.INSPECTOR_W,
                                      bottom - top - 30)
        for p in self.panels.values():
            p.layout(self.panel_rect)

    @property
    def panel(self):
        return self.panels[self.tabs.current]

    def _tab_changed(self, tab) -> None:
        self.set_focus(None)
        self.panels[tab].rebuild()

    # -- change handling ---------------------------------------------------------

    def _on_change(self, changes) -> None:
        if changes.palettes:
            self._rebuild_palette_textures(changes.palettes)
        if changes.zones:
            render.reset_caches()
        self.room_view.on_change(changes)
        self.map_view.on_change(changes)
        if self.sel is not None and not self.model.has_cell(self.sel["coord"]):
            self.sel = None
        if changes.palettes or changes.cells:
            self._refresh_strip_lists()
        self._validate_pending = True
        for p in self.panels.values():
            try:
                p.on_change(changes)
            except Exception:
                traceback.print_exc()

    def _rebuild_palette_textures(self, names) -> None:
        from maker.core import assets
        todo = [n for n in sorted(names) if n in self.model.palettes
                and (self.model.project.textures_dir / n).is_dir()]
        if todo:
            try:
                assets.rebuild_textures(self.model, todo)
            except Exception as exc:
                self.error(f"texture rebuild failed: {exc}")
            self.room_view.refresh()

    def revalidate(self) -> None:
        try:
            self.issues = validate.validate(self.model)
        except Exception as exc:
            self.issues = []
            self.error(f"validate failed: {exc}")
        self._validate_pending = False
        if "Problems" in getattr(self, "panels", {}):
            self.panels["Problems"].rebuild()

    def after_edit(self) -> None:
        """Called after a coalesced drag (no listener fires for merges)."""
        self.room_view.refresh()

    def after_asset_change(self) -> None:
        render.reset_caches()
        self.room_view.refresh()
        self.panels["Palettes"].rebuild()

    # -- running ops --------------------------------------------------------------

    def run(self, fn, *args, label=None, quiet=False, **kwargs):
        """Run an op (or any callable doing ops); errors become a status
        message and return None. Never raises."""
        try:
            res = fn(self.model, *args, **kwargs) if fn.__module__.startswith(
                "maker.core") else fn(*args, **kwargs)
        except (OpError, ValueError, KeyError, TypeError) as exc:
            self.error(str(exc) if isinstance(exc, OpError)
                       else f"{type(exc).__name__}: {exc}")
            return None
        except Exception as exc:  # unexpected: keep editing alive
            traceback.print_exc()
            self.error(f"{type(exc).__name__}: {exc}")
            return None
        if not quiet:
            self.status(f"{label or getattr(fn, '__name__', 'edit')}: ok", T.OK)
        return {} if res is None else res

    def background(self, fn, done_msg) -> None:
        """Run a slow file operation with a busy overlay."""
        self._busy = done_msg
        self.draw()
        pygame.display.flip()
        try:
            fn()
            self.status(done_msg, T.OK)
        except Exception as exc:
            self.error(str(exc))
        finally:
            self._busy = None
        self.after_asset_change()

    def undo(self) -> None:
        label = self.model.undo()
        self.status(f"undid {label}" if label else "nothing to undo")

    def redo(self) -> None:
        label = self.model.redo()
        self.status(f"redid {label}" if label else "nothing to redo")

    def save(self) -> bool:
        try:
            res = self.model.save()
        except Exception as exc:
            self.error(f"save failed: {exc}")
            return False
        self.status(f"saved {self.model.project.root.name}"
                    + (f" ({res})" if isinstance(res, (int, str)) else ""), T.OK)
        for p in self.panels.values():
            p.rebuild()
        return True

    # -- status / focus / clipboard -------------------------------------------------

    def status(self, msg, color=T.TEXT_DIM) -> None:
        self.status_msg = str(msg)
        self.status_color = color
        self.status_time = time.time()

    def error(self, msg) -> None:
        self.last_error = str(msg)
        self.status(msg, T.ERROR)

    def set_hover_info(self, s) -> None:
        self.hover_info = s

    def set_focus(self, w, blur=True) -> None:
        if w is self.focus:
            return
        old, self.focus = self.focus, w
        if old is not None and blur and hasattr(old, "on_blur"):
            old.on_blur(self)

    def focus_next(self, w, backwards=False) -> None:
        from maker.gui.widgets import TextInput
        root = self.dialogs[-1] if self.dialogs else self.panel
        fields = []

        def walk(c):
            for ch in getattr(c, "children", []):
                if isinstance(ch, TextInput) and ch.visible:
                    fields.append(ch)
                walk(ch)
        walk(root)
        if w in fields and len(fields) > 1:
            i = fields.index(w) + (-1 if backwards else 1)
            self.set_focus(fields[i % len(fields)], blur=False)
        else:
            self.set_focus(None, blur=False)

    def modal_ok(self, widget) -> bool:
        if self.dialogs:
            return widget in self.dialogs[-1].children
        return self.menu is None

    def clipboard_get(self) -> str:
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            raw = pygame.scrap.get(pygame.SCRAP_TEXT)
            if raw:
                return raw.decode("utf-8", "ignore").rstrip("\x00")
        except Exception:
            pass
        return self._clipboard

    def clipboard_set(self, s) -> None:
        self._clipboard = s
        try:
            if not pygame.scrap.get_init():
                pygame.scrap.init()
            pygame.scrap.put(pygame.SCRAP_TEXT, s.encode("utf-8"))
        except Exception:
            pass

    # -- menus / dialogs ------------------------------------------------------------

    def open_menu(self, items, pos) -> None:
        self.menu = PopupMenu(items, pos)
        self.menu.clamp(self.screen.get_rect())

    def close_menu(self) -> None:
        self.menu = None

    def open_dialog(self, d: Dialog) -> Dialog:
        d.ui = self
        self.dialogs.append(d)
        self.set_focus(d.inputs[0] if d.inputs else None, blur=False)
        return d

    def close_dialog(self, d) -> None:
        if d in self.dialogs:
            self.dialogs.remove(d)
        self.set_focus(None, blur=False)

    def prompt(self, title, fields, on_ok, message="") -> Dialog:
        return self.open_dialog(Dialog(title, message, fields,
                                       [("Cancel", None), ("OK", on_ok)]))

    def confirm(self, message, on_yes, title="Confirm", yes="OK") -> Dialog:
        return self.open_dialog(Dialog(title, message, (),
                                       [("Cancel", None),
                                        (yes, lambda _v: on_yes())]))

    def message(self, title, message) -> Dialog:
        return self.open_dialog(Dialog(title, message, (), [("OK", None)]))

    # -- selection -------------------------------------------------------------------

    def select_cell(self, coord, from_map=False) -> None:
        self.sel = {"kind": "cell", "coord": tuple(coord), "tile": None}
        if not from_map:
            self.map_view.selection = {tuple(coord)}
        self._selection_changed()

    def select_tile(self, coord, tile) -> None:
        self.sel = {"kind": "cell", "coord": tuple(coord), "tile": tuple(tile)}
        self.map_view.selection = {tuple(coord)}
        self._selection_changed()

    def select_entity(self, coord, index) -> None:
        self.sel = {"kind": "entity", "coord": tuple(coord), "index": int(index)}
        self._selection_changed()

    def _selection_changed(self) -> None:
        self.panels["Inspect"].rebuild()
        self.panels["JSON"].rebuild()
        if self.tabs.current == "Zones":
            self.panels["Zones"].rebuild()

    def selection_overlay(self):
        sel = self.sel
        if sel is None:
            return None
        if sel["kind"] == "entity":
            return {"entity": (sel["coord"], sel["index"])}
        out = {"cells": [sel["coord"]]}
        if sel.get("tile") is not None:
            cx, cy = sel["coord"]
            tx, ty = sel["tile"]
            out["tiles"] = [(cx * 16 + tx, cy * 10 + ty)]
        return out

    def current_zone(self, from_panel=True):
        if from_panel and self.tabs.current == "Zones":
            return self.panels["Zones"].current
        if self.sel is not None and self.model.has_cell(self.sel["coord"]):
            z = self.model.cell(self.sel["coord"]).get("zone", "")
            if z:
                return z
        return self.panels["Zones"].current if from_panel else None

    def current_palette(self):
        return self.panels["Palettes"].current or self.brush_palette

    def current_sprite(self):
        return self.panels["Sprites"].current

    def delete_entity(self, coord, index) -> None:
        if self.run(ops.remove_entity, coord, index,
                    label="delete entity") is not None:
            self.select_cell(coord)

    def delete_cells(self, coords) -> None:
        coords = [tuple(c) for c in coords]

        def go():
            def do():
                with self.model.transaction(f"delete {len(coords)} cell(s)"):
                    for c in coords:
                        ops.delete_cell(self.model, c, force=True)
            self.run(do, label=f"deleted {len(coords)} cell(s)")
            self.map_view.selection = set()
        names = ", ".join(f"{c[0]},{c[1]}" for c in coords[:6])
        self.confirm(f"Delete {len(coords)} cell(s): {names}"
                     f"{'…' if len(coords) > 6 else ''}? Doors into them are "
                     f"unlinked and open edges walled. (Undo restores them.)",
                     go, "Delete cells", "Delete")

    # -- brushes / tools --------------------------------------------------------------

    def set_tool(self, tool) -> None:
        self.room_view.tool = tool
        for tid, label, key, help_ in TOOLS:
            if tid == tool:
                self.status(f"{label}: {help_}")
        if self.mode != "room":
            self.set_mode("room")

    def set_brush(self, kind, palette) -> None:
        self.brush_kind = kind
        self.brush_palette = palette
        self.pal_list.selected_value = palette or "__auto__"
        if self.room_view.tool not in ("paint", "rect", "fill"):
            self.room_view.tool = "paint"
        self.status(f"brush: {kind} [{palette or 'auto'}]")

    def _pick_palette(self, value) -> None:
        self.brush_palette = None if value == "__auto__" else value
        self.status(f"brush palette: {self.brush_palette or 'auto'}")

    def _pick_entity_type(self, value) -> None:
        self.entity_type = value
        self.room_view.tool = "entity"
        self.status(f"Entity tool: click to place {value}")

    def _toggle_overlay(self, name) -> None:
        ov = self.room_view.overlays
        ov[name] = not ov[name]

    def toggle_lighting(self) -> None:
        self.room_view.lighting = not self.room_view.lighting
        self.room_view.refresh()

    def toggle_autosave(self) -> None:
        self.autosave = not self.autosave
        self._last_autosave = time.time()

    def toggle_wheel(self) -> None:
        self.wheel_zooms = not self.wheel_zooms

    def set_mode(self, mode) -> None:
        self.mode = mode
        if mode == "map" and self.room_view.focus_cell is not None:
            r = self.map_view.cell_rect(self.room_view.focus_cell)
            if not self.map_view.rect.contains(r):
                self.map_view.center_on(self.room_view.focus_cell)

    def open_room(self, coord, fit=True) -> None:
        coord = tuple(coord)
        if not self.model.has_cell(coord):
            self.error(f"no cell at {coord}")
            return
        self.mode = "room"
        same = (self.room_view.room is not None
                and coord in self.room_view.room.cells)
        self.room_view.open(coord, fit=fit or not same)
        self.select_cell(coord)

    # -- edges / doors ---------------------------------------------------------------

    def edge_menu(self, coord, direction, pos) -> None:
        m = self.model
        d = m.cell(coord)
        if d is None:
            return
        edge = d.get("edges", {}).get(direction, {"type": "wall"})
        etype = edge.get("type", "wall")
        n = (coord[0] + DELTA[direction][0], coord[1] + DELTA[direction][1])
        has_n = m.has_cell(n)

        def compound(label, *steps):
            def do():
                with m.transaction(label):
                    if etype == "door":
                        ops.unlink_door(m, coord, direction)
                    for fn, args, kw in steps:
                        fn(m, *args, **kw)
                return {}
            return lambda: self.run(do, label=label)

        items = [(f"{direction} edge of {coord[0]},{coord[1]}: {etype}",
                  lambda: None, False), None]
        items.append(("Wall", compound("wall edge", (ops.set_edge,
                                                      (coord, direction, "wall"),
                                                      {})), etype != "wall"))
        items.append((f"Open — join with {n[0]},{n[1]} (carve gap)",
                      compound("open edge", (ops.set_edge,
                                             (coord, direction, "open"),
                                             {"carve": True})),
                      has_n and etype != "open"))
        items.append((f"Door to neighbour {n[0]},{n[1]} (carve gap)",
                      compound("link door", (ops.link_door, (coord, direction, n),
                                             {"carve": True})), has_n))
        items.append(("Door to another cell…",
                      lambda: self._door_dialog(coord, direction, etype), True))
        if etype == "door":
            tgt = edge.get("target_cell")
            items.append(None)
            items.append((f"Go through door -> {tgt}",
                          lambda: self.open_room(tuple(tgt), fit=True),
                          bool(tgt) and m.has_cell(tuple(tgt))))
        elif etype == "open" and has_n:
            items.append(None)
            items.append((f"Select neighbour {n[0]},{n[1]}",
                          lambda: self.select_cell(n), True))
        self.open_menu(items, pos)

    def _door_dialog(self, coord, direction, etype) -> None:
        m = self.model

        def go(vals):
            target = tuple(int(p) for p in vals[0].replace(" ", "").split(","))
            tedge = vals[1].strip() or OPPOSITE[direction]
            door_id = vals[2].strip() or None

            with m.transaction("link door"):
                if etype == "door":
                    ops.unlink_door(m, coord, direction)
                ops.link_door(m, coord, direction, target, tedge, door_id,
                              carve=True)
            self.status(f"linked door {coord} {direction} -> {target} {tedge}",
                        T.OK)
        self.prompt(f"Door from {coord[0]},{coord[1]} {direction}",
                    [("target cell", ""), ("target edge", OPPOSITE[direction]),
                     ("door id", "")], go,
                    message="Links both ends (the target's edge becomes a door "
                    "back). Leave door id blank to auto-number.")

    # -- playtest / build / projects -----------------------------------------------

    def playtest_here(self) -> None:
        rv = self.room_view
        if self.mode == "room" and rv.hover is not None and rv.tile_in_room(rv.hover) \
                and rv.rect.collidepoint(self.mouse):
            coord, tile = rv.split_tile(rv.hover)
            self.playtest(coord, tile)
        elif self.sel is not None:
            self.playtest(self.sel["coord"], self.sel.get("tile"))
        else:
            self.playtest(None, None)

    def playtest(self, coord, tile) -> None:
        def launch():
            argv = playtest_command(self.model, coord, tile)
            LOG_DIR.mkdir(exist_ok=True)
            log = open(LOG_DIR / "playtest.log", "w")
            env = dict(os.environ)
            if not self.headless:
                env.pop("SDL_VIDEODRIVER", None)
            proc = subprocess.Popen(argv, cwd=str(MAKER_ROOT), stdout=log,
                                    stderr=subprocess.STDOUT, env=env)
            self._playtests.append((proc, log))
            where = f"cell {coord} tile {tile}" if coord else "the start"
            self.status(f"playtest launched from {where} (Esc quits the game)",
                        T.OK)

        if self.model.is_dirty:
            self.confirm("The game runs from the saved files. Save changes and "
                         "playtest?", lambda: self.save() and launch(),
                         "Playtest", "Save && Playtest")
        else:
            launch()

    def _poll_playtests(self) -> None:
        for proc, log in list(self._playtests):
            code = proc.poll()
            if code is None:
                continue
            self._playtests.remove((proc, log))
            log.close()
            if code not in (0, -15, -9):
                tail = (LOG_DIR / "playtest.log").read_text()[-600:]
                self.message("Playtest crashed", tail or f"exit code {code}")

    def build_game(self) -> None:
        name = self.model.manifest.get("name", "game")
        default = str(self.model.project.root / "builds" / name)

        def go(vals):
            out = Path(os.path.expanduser(vals[0].strip()))
            force = vals[1].strip().lower() in ("y", "yes", "true", "1")
            if self.model.is_dirty:
                if not self.save():
                    return False
            res = export_game(self.model, out, force=force)
            self.message("Game built",
                         f"Exported to {res['out_dir']} ({res['files']} files, "
                         f"{res['warnings']} warnings). Run play.command there "
                         f"(needs Python 3 + pygame).")
        self.prompt("Build Game", [("output folder", default),
                                   ("ignore errors (y/n)", "n")], go,
                    message="Saves, validates, then writes a standalone game "
                    "folder (engine + this project's data). An existing folder "
                    "is replaced only if it is a previous export.")

    def open_project_dialog(self) -> None:
        def go(vals):
            path = Path(os.path.expanduser(vals[0].strip()))
            model = ProjectModel.open(path)
            self._switch(model)
        self._guard_unsaved(lambda: self.prompt(
            "Open project", [("project folder", str(self.model.project.root))],
            go))

    def new_project_dialog(self) -> None:
        def go(vals):
            dest = Path(os.path.expanduser(vals[0].strip()))
            template = vals[3].strip() or "starter"
            create_project(dest, vals[1].strip() or dest.name,
                           vals[2].strip() or dest.name, template=template,
                           source=self.model.project.root
                           if template == "copy" else None)
            self._switch(ProjectModel.open(dest))
        default = str(PROJECTS_DIR / "new_game")
        self._guard_unsaved(lambda: self.prompt(
            "New project", [("folder", default), ("name", "new_game"),
                            ("title", "New Game"),
                            ("template (starter/copy)", "starter")], go,
            message="'copy' duplicates the current project."))

    def _guard_unsaved(self, then) -> None:
        if self.model.is_dirty:
            self.confirm("Discard unsaved changes?", then, "Unsaved changes",
                         "Discard")
        else:
            then()

    def _switch(self, model) -> None:
        self.dialogs = []
        self.focus = None
        self.load_model(model)

    # -- events -----------------------------------------------------------------

    def request_quit(self) -> None:
        if not self.model.is_dirty:
            self.running = False
            return
        d = Dialog("Unsaved changes", "Save before quitting?", (),
                   [("Cancel", None),
                    ("Quit without saving", lambda _v: self._quit()),
                    ("Save && Quit", lambda _v: self.save() and self._quit())])
        self.open_dialog(d)

    def _quit(self) -> None:
        self.running = False

    def handle(self, ev) -> None:
        if ev.type == pygame.QUIT:
            self.request_quit()
            return
        if ev.type == pygame.VIDEORESIZE:
            self.screen = pygame.display.set_mode((max(1000, ev.w),
                                                   max(640, ev.h)),
                                                  pygame.RESIZABLE)
            self.layout()
            return
        if hasattr(ev, "pos"):
            self.mouse = ev.pos
        if ev.type in (pygame.KEYDOWN, pygame.KEYUP):
            self.mods = ev.mod
            if ev.key == pygame.K_SPACE and self.focus is None:
                self.space_down = ev.type == pygame.KEYDOWN
        else:
            self.mods = pygame.key.get_mods() if not self.headless else self.mods
        if self.dialogs:
            self.dialogs[-1].handle(ev, self)
            return
        if self.menu is not None:
            if self.menu.handle(ev, self):
                return
        if ev.type in (pygame.KEYDOWN, pygame.TEXTINPUT) and self.focus is not None:
            if self.focus.handle(ev, self):
                return
        if ev.type == pygame.TEXTINPUT:
            return
        if ev.type == pygame.KEYDOWN and self._shortcut(ev):
            return
        if ev.type == pygame.MOUSEBUTTONDOWN and self.focus is not None and \
                not self.focus.rect.collidepoint(ev.pos):
            self.set_focus(None)
        view = self.room_view if self.mode == "room" else self.map_view
        for w in (self.toolbar, self.strip, self.tabs, self.panel, view):
            if w.handle(ev, self):
                return

    def _shortcut(self, ev) -> bool:
        k, mods = ev.key, ev.mod
        shift = bool(mods & pygame.KMOD_SHIFT)
        if cmd_down(mods):
            if k == pygame.K_z:
                self.redo() if shift else self.undo()
            elif k == pygame.K_y:
                self.redo()
            elif k == pygame.K_s:
                self.save()
            elif k == pygame.K_g:
                self._toggle_overlay("grid")
            elif k == pygame.K_k:
                self._toggle_overlay("kinds")
            elif k == pygame.K_l:
                self.toggle_lighting()
            elif k == pygame.K_q:
                self.request_quit()
            elif k == pygame.K_0:
                (self.room_view if self.mode == "room" else self.map_view).fit()
            else:
                return False
            return True
        if self.focus is not None:
            return False
        if k == pygame.K_TAB:
            self.set_mode("map" if self.mode == "room" else "room")
            return True
        if k == pygame.K_F5:
            self.playtest_here()
            return True
        if k == pygame.K_ESCAPE:
            self.sel = None
            self._selection_changed()
            return True
        if self.mode == "map":
            return self.map_view.handle(ev, self)
        for tid, label, key, help_ in TOOLS:
            if k == pygame.key.key_code(key):
                self.set_tool(tid)
                return True
        if pygame.K_1 <= k <= pygame.K_5:
            self.set_brush(KINDS[k - pygame.K_1], self.brush_palette)
            return True
        if k in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
            self.room_view.set_zoom(self.room_view.zoom * 1.25)
            return True
        if k in (pygame.K_MINUS, pygame.K_KP_MINUS):
            self.room_view.set_zoom(self.room_view.zoom / 1.25)
            return True
        if k == pygame.K_0:
            self.room_view.fit()
            return True
        if k in (pygame.K_DELETE, pygame.K_BACKSPACE) and self.sel is not None \
                and self.sel["kind"] == "entity":
            self.delete_entity(self.sel["coord"], self.sel["index"])
            return True
        if k in (pygame.K_LEFT, pygame.K_RIGHT, pygame.K_UP, pygame.K_DOWN):
            dx = {pygame.K_LEFT: 60, pygame.K_RIGHT: -60}.get(k, 0)
            dy = {pygame.K_UP: 60, pygame.K_DOWN: -60}.get(k, 0)
            self.room_view.pan[0] += dx
            self.room_view.pan[1] += dy
            return True
        return False

    # -- per-frame ------------------------------------------------------------------

    def update(self) -> None:
        if self._validate_pending:
            self.revalidate()
        if self.autosave and self.model.is_dirty and \
                time.time() - self._last_autosave > AUTOSAVE_SECONDS:
            self._last_autosave = time.time()
            if self.save():
                self.status("autosaved", T.OK)
        self._poll_playtests()

    def draw(self) -> None:
        s = self.screen
        w, h = s.get_size()
        s.fill(T.BG)
        view = self.room_view if self.mode == "room" else self.map_view
        view.draw(s, self)
        # toolbar
        pygame.draw.rect(s, T.PANEL, (0, 0, w, T.TOOLBAR_H))
        pygame.draw.line(s, T.BORDER, (0, T.TOOLBAR_H - 1), (w, T.TOOLBAR_H - 1))
        self.toolbar.draw(s, self)
        title = self.model.manifest.get("title", "")
        dirty = " ●" if self.model.is_dirty else ""
        fnt = T.font(13, bold=True)
        while title and fnt.size(title + dirty)[0] > T.INSPECTOR_W - 24:
            title = title[:-1]
        T.text(s, title + dirty, (w - T.INSPECTOR_W + 12, 9),
               T.ACCENT if dirty else T.TEXT, 13, bold=True)
        # tool strip
        pygame.draw.rect(s, T.PANEL, (0, T.TOOLBAR_H, T.TOOLSTRIP_W,
                                      h - T.TOOLBAR_H - T.STATUS_H))
        self.strip.draw(s, self)
        # inspector
        ix = w - T.INSPECTOR_W
        pygame.draw.rect(s, T.PANEL, (ix, T.TOOLBAR_H, T.INSPECTOR_W,
                                      h - T.TOOLBAR_H - T.STATUS_H))
        pygame.draw.line(s, T.BORDER, (ix, T.TOOLBAR_H), (ix, h - T.STATUS_H))
        self.tabs.draw(s, self)
        clip = s.get_clip()
        s.set_clip(self.panel_rect)
        self.panel.draw(s, self)
        s.set_clip(clip)
        # status bar
        pygame.draw.rect(s, T.PANEL_2, (0, h - T.STATUS_H, w, T.STATUS_H))
        n_err = sum(1 for i in self.issues if i.severity == "error")
        n_warn = sum(1 for i in self.issues if i.severity == "warning")
        right = (f"{n_err} errors  {n_warn} warnings   "
                 f"zoom {int(self.room_view.zoom * 300)}%   "
                 f"brush {self.brush_kind} [{self.brush_palette or 'auto'}]")
        rw = T.font(12).size(right)[0]
        T.text(s, right, (w - rw - 10, h - T.STATUS_H + 5),
               T.ERROR if n_err else T.TEXT_DIM, 12)
        msg = self.status_msg
        T.text(s, (self.hover_info + "    " if self.hover_info else "") + msg,
               (8, h - T.STATUS_H + 5),
               self.status_color if msg else T.TEXT_DIM, 12,
               max_w=w - rw - 30)
        self._draw_tooltip(s)
        if self.menu is not None:
            self.menu.draw(s, self)
        for d in self.dialogs:
            d.draw(s, self)
        if self._busy:
            shade = pygame.Surface((w, h), pygame.SRCALPHA)
            shade.fill((0, 0, 0, 120))
            s.blit(shade, (0, 0))
            T.text(s, "Working…", (w // 2 - 40, h // 2 - 10), T.ACCENT, 18,
                   bold=True)

    def _draw_tooltip(self, s) -> None:
        if self.menu or self.dialogs:
            return
        target = None
        for c in (self.toolbar, self.strip, self.panel):
            if c.hit(self.mouse) or c is self.toolbar:
                target = c.widget_at(self.mouse)
                if target is not None:
                    break
        tip = getattr(target, "tooltip", "") if target else ""
        if callable(tip):
            tip = tip()
        now = pygame.time.get_ticks()
        if target is not self._hover_since[0]:
            self._hover_since = (target, now)
            return
        if not tip or now - self._hover_since[1] < 600:
            return
        f = T.font(12)
        tw, th = f.size(tip)
        r = pygame.Rect(self.mouse[0] + 14, self.mouse[1] + 18, tw + 12, th + 8)
        r.clamp_ip(s.get_rect())
        pygame.draw.rect(s, (250, 240, 200), r)
        pygame.draw.rect(s, T.BORDER, r, 1)
        s.blit(f.render(tip, True, T.TEXT_DARK), (r.x + 6, r.y + 4))

    def step(self, events=None) -> None:
        """One frame: handle events (given or from the queue), update,
        draw. Tests drive the GUI through this."""
        for ev in (pygame.event.get() if events is None else events):
            try:
                self.handle(ev)
            except Exception as exc:
                traceback.print_exc()
                self.error(f"internal error: {exc}")
        self.update()
        self.draw()
        pygame.display.flip()

    def run_loop(self) -> None:
        while self.running:
            self.step()
            self.clock.tick(60)
        for proc, log in self._playtests:
            log.close()
