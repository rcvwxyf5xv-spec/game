"""Right-hand data pane: one panel per tab.

Each panel is a Container with rebuild() (recreate widgets from model +
selection) and layout(rect). Field widgets read their values through
callables so they always mirror the model; edits go through app.run()
-> maker.core.ops, so every change is undoable and identical to the CLI.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pygame

from maker.core import assets, ops
from maker.core.errors import OpError
from maker.gui import theme as T
from maker.gui.widgets import (
    Button, Container, Form, Image, Label, ListView, Slider, Swatch,
    TextArea, TextInput,
)

KINDS = ("empty", "solid", "spring", "oneway", "crumble")


def _rgb_str(c) -> str:
    return ",".join(str(int(v)) for v in c) if c else ""


def _parse_rgb(s):
    s = s.strip().lstrip("#")
    if len(s) == 6 and "," not in s:
        try:
            return [int(s[i:i + 2], 16) for i in (0, 2, 4)]
        except ValueError:
            pass
    parts = [p for p in s.replace(" ", ",").split(",") if p]
    if len(parts) != 3:
        raise OpError("colour must be r,g,b or #rrggbb")
    vals = [int(p) for p in parts]
    if any(v < 0 or v > 255 for v in vals):
        raise OpError("colour components must be 0..255")
    return vals


def _parse_value(s):
    """Entity prop text -> JSON value (bare words stay strings)."""
    try:
        return json.loads(s)
    except ValueError:
        return s


def pretty_json(obj, indent=0, width=78) -> str:
    """json.dumps(indent=2) but containers that fit in `width` columns
    stay on one line (keeps tile rows / colour triples readable)."""
    flat = json.dumps(obj)
    if not isinstance(obj, (list, dict)) or len(flat) + indent <= width:
        return flat
    pad = " " * (indent + 2)
    if isinstance(obj, list):
        if not obj:
            return "[]"
        inner = ",\n".join(pad + pretty_json(v, indent + 2, width) for v in obj)
        return "[\n" + inner + "\n" + " " * indent + "]"
    if not obj:
        return "{}"
    inner = ",\n".join(
        f"{pad}{json.dumps(k)}: {pretty_json(v, indent + 2, width)}"
        for k, v in obj.items())
    return "{\n" + inner + "\n" + " " * indent + "}"


class Panel(Container):
    title = ""

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.model = app.model

    def rebuild(self) -> None:
        pass

    def on_change(self, changes) -> None:
        self.rebuild()


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------


class InspectPanel(Panel):
    title = "Inspect"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.form = Form()
        self.add(self.form)
        self.ent_list = ListView(on_select=self._pick_entity, mono=True)

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        self.form.layout(rect)

    def rebuild(self) -> None:
        f = self.form
        keep_scroll = f.scroll
        f.clear()
        sel = self.app.sel
        if sel is None or not self.model.has_cell(sel["coord"]):
            f.heading("Nothing selected")
            f.add_row([Label("Select a tile/cell (Select tool, or the World "
                             "Map) or an entity.", T.TEXT_DIM, 12)])
            self._project_summary(f)
            return
        if sel["kind"] == "entity":
            self._entity_form(f, sel["coord"], sel["index"])
        else:
            self._cell_form(f, sel["coord"], sel.get("tile"))
        f.scroll = keep_scroll

    def _project_summary(self, f) -> None:
        m = self.model
        f.spacer(10)
        f.heading("Project")
        n_ent = sum(len(d.get("entities", [])) for d in m.cells.values())
        for label, val in (("title", m.manifest.get("title", "")),
                           ("cells", len(m.cells)),
                           ("entities", n_ent),
                           ("start cell", m.start_cell),
                           ("palettes", len(m.palettes)),
                           ("zones", len(m.zones))):
            f.field(label, Label(str(val)))

    # -- cell -------------------------------------------------------------

    def _cell_form(self, f, coord, tile) -> None:
        app, m = self.app, self.model
        d = m.cell(coord)
        f.heading(f"Cell {coord[0]},{coord[1]}"
                  + ("   (start)" if m.start_cell == coord else ""))
        f.add_row([
            Button("Open room", lambda: app.open_room(coord)),
            Button("Playtest", lambda: app.playtest(coord, tile)),
            Button("Set start", lambda: app.run(ops.set_start_cell, coord),
                   enabled=lambda: m.start_cell != coord),
            Button("Delete", lambda: app.delete_cells([coord])),
        ], 24)
        zone_in = TextInput(lambda: m.cell(coord).get("zone", "") if m.has_cell(coord) else "",
                            lambda v: app.run(ops.set_cell_props, coord,
                                              zone=v.strip()) is not None)
        f.add_row([Label("zone", T.TEXT_DIM, 12), zone_in,
                   Button("…", lambda: app.open_menu(
                       [(z, lambda z=z: app.run(ops.set_cell_props, coord,
                                                zone=z))
                        for z in m.zone_names()] or [("(no zones)", lambda: None, False)],
                       app.mouse), tooltip="choose a zone")],
                  T.ROW_H, [110, None, 26])
        bg = TextInput(lambda: _rgb_str(m.cell(coord).get("background_color"))
                       if m.has_cell(coord) else "",
                       lambda v: app.run(ops.set_cell_props, coord,
                                         background_color=_parse_rgb(v))
                       is not None)
        f.add_row([Label("background", T.TEXT_DIM, 12), bg,
                   Swatch(lambda: m.cell(coord).get("background_color",
                                                    [0, 0, 0])
                          if m.has_cell(coord) else (0, 0, 0))],
                  T.ROW_H, [110, None, 26])
        f.spacer()
        f.heading("Edges")
        for direction in ("north", "south", "west", "east"):
            f.add_row([Label(direction, T.TEXT_DIM, 12),
                       Label(self._edge_text(d, direction), T.TEXT, 12),
                       Button("Change…", lambda dr=direction: app.edge_menu(
                           coord, dr, app.mouse), size=12)],
                      T.ROW_H, [110, None, 80])
        if tile is not None:
            tx, ty = tile
            t = d["tiles"][ty][tx]
            f.spacer()
            f.heading(f"Tile {tx},{ty}")
            f.field("kind", Label(t.get("kind", "empty")))
            f.field("palette", Label(t.get("palette") or "-"))
            f.add_row([Button("Use as brush", lambda: app.set_brush(
                t.get("kind", "empty"), t.get("palette") or None)),
                Button("Playtest from here",
                       lambda: app.playtest(coord, tile))], 24)
        f.spacer()
        ents = d.get("entities", [])
        f.heading(f"Entities ({len(ents)})")
        self.ent_list = ListView(on_select=self._pick_entity, mono=True)
        self.ent_list.set_items(
            [(f"#{i} {e.get('type', '?')} @ {e.get('tile_x')},{e.get('tile_y')}",
              (coord, i)) for i, e in enumerate(ents)])
        f.add_row([self.ent_list], max(24, min(6, len(ents) or 1) * 20 + 2))
        counts = {}
        for row in d["tiles"]:
            for t in row:
                counts[t.get("kind", "empty")] = counts.get(
                    t.get("kind", "empty"), 0) + 1
        f.spacer()
        f.heading("Tiles")
        f.add_row([Label("  ".join(f"{k}:{v}" for k, v in sorted(counts.items())),
                         T.TEXT_DIM, 12)])
        room = m.room_cells(coord)
        f.field("room", Label(f"{len(room)} cell(s)", T.TEXT_DIM, 12))

    @staticmethod
    def _edge_text(d, direction) -> str:
        e = d.get("edges", {}).get(direction, {"type": "wall"})
        if isinstance(e, str):
            return e
        t = e.get("type", "wall")
        if t == "door":
            tgt = e.get("target_cell") or e.get("target")
            return (f"door {e.get('door_id', '?')} -> "
                    f"{tgt} {e.get('target_edge', '')}")
        return t

    def _pick_entity(self, value) -> None:
        coord, idx = value
        self.app.select_entity(coord, idx)

    # -- entity -------------------------------------------------------------

    def _entity_form(self, f, coord, idx) -> None:
        app, m = self.app, self.model
        ents = m.cell(coord).get("entities", [])
        if idx >= len(ents):
            app.select_cell(coord)
            return
        e = ents[idx]
        f.heading(f"Entity #{idx} in cell {coord[0]},{coord[1]}")
        f.add_row([Button("Back to cell", lambda: app.select_cell(coord)),
                   Button("Delete entity", lambda: app.delete_entity(coord, idx))],
                  24)

        def getter(key):
            def g():
                es = m.cell(coord).get("entities", []) if m.has_cell(coord) else []
                if idx < len(es):
                    v = es[idx].get(key, "")
                    return v if isinstance(v, str) else json.dumps(v)
                return ""
            return g

        def setter(key):
            def s(v):
                if key in ("tile_x", "tile_y"):
                    val = int(v)
                elif key == "type":
                    val = v.strip()
                else:
                    val = _parse_value(v)
                return app.run(ops.update_entity, coord, idx, {key: val},
                               label=f"entity {key}") is not None
            return s

        type_in = TextInput(getter("type"), setter("type"))
        f.add_row([Label("type", T.TEXT_DIM, 12), type_in,
                   Button("…", lambda: app.open_menu(
                       [(t, lambda t=t: app.run(ops.update_entity, coord, idx,
                                                {"type": t}))
                        for t in ops.entity_types(m)], app.mouse))],
                  T.ROW_H, [110, None, 26])
        f.field("tile_x", TextInput(getter("tile_x"), setter("tile_x")))
        f.field("tile_y", TextInput(getter("tile_y"), setter("tile_y")))
        f.spacer()
        f.heading("Properties")
        extra = [k for k in e if k not in ("type", "tile_x", "tile_y")]
        if not extra:
            f.add_row([Label("(none)", T.TEXT_DIM, 12)])
        for key in extra:
            f.add_row([Label(key, T.TEXT_DIM, 12),
                       TextInput(getter(key), setter(key)),
                       Button("✕", lambda k=key: self._remove_prop(coord, idx, k),
                              tooltip="remove property")],
                      T.ROW_H, [110, None, 26])
        f.add_row([Button("Add property…", lambda: app.prompt(
            "Add property", [("key", ""), ("value (JSON or text)", "")],
            lambda vals: app.run(ops.update_entity, coord, idx,
                                 {vals[0].strip(): _parse_value(vals[1])})
            is not None if vals[0].strip() else False))], 24)
        f.add_row([Label("Values are JSON (numbers, true/false, lists); "
                         "anything else is stored as text.", T.TEXT_DIM, 11)])

    def _remove_prop(self, coord, idx, key) -> None:
        data = copy.deepcopy(self.model.cell(coord))
        data["entities"][idx].pop(key, None)
        self.app.run(ops.set_cell_data, coord, data, label=f"remove {key}")


# ---------------------------------------------------------------------------
# Raw JSON
# ---------------------------------------------------------------------------


class JsonPanel(Panel):
    """Raw JSON of the current target; Apply validates through the same
    ops as the CLI (set_cell_data / set_zone / set_palette_data / ...)."""

    title = "JSON"
    TARGETS = ("selection", "zone", "palette", "sprite", "manifest")

    def __init__(self, app) -> None:
        super().__init__(app)
        self.target = "selection"
        self.area = TextArea()
        self.msg = ""
        self.msg_color = T.TEXT_DIM
        self._loaded_key = None
        self.buttons = []
        for t in self.TARGETS:
            b = Button(t.capitalize(), lambda t=t: self.set_target(t),
                       selected=lambda t=t: self.target == t, size=12)
            self.buttons.append(b)
            self.add(b)
        self.apply_btn = self.add(Button("Apply (Cmd+Enter)", self.apply))
        self.revert_btn = self.add(Button("Revert", self.reload))
        self.fmt_btn = self.add(Button("Format", self.format))
        self.add(self.area)

    def set_target(self, t) -> None:
        self.target = t
        self.reload()

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        x = rect[0] + 6
        bw = (rect[2] - 12 - 4 * (len(self.buttons) - 1)) // len(self.buttons)
        for b in self.buttons:
            b.layout((x, rect[1] + 6, bw, 22))
            x += bw + 4
        self.area.layout((rect[0] + 6, rect[1] + 50, rect[2] - 12,
                          rect[3] - 96))
        by = rect[1] + rect[3] - 38
        self.apply_btn.layout((rect[0] + 6, by, 150, 28))
        self.revert_btn.layout((rect[0] + 162, by, 80, 28))
        self.fmt_btn.layout((rect[0] + 248, by, 80, 28))

    def _resolve(self):
        """(key, label, data) for the current target, or (None, msg, None)."""
        app, m = self.app, self.model
        sel = app.sel
        if self.target == "selection":
            if sel is None or not m.has_cell(sel["coord"]):
                return None, "select a cell or entity", None
            if sel["kind"] == "entity":
                ents = m.cell(sel["coord"]).get("entities", [])
                if sel["index"] < len(ents):
                    return (("entity", sel["coord"], sel["index"]),
                            f"entity #{sel['index']} in cell {sel['coord']}",
                            ents[sel["index"]])
            return (("cell", sel["coord"]), f"cell {sel['coord']}",
                    m.cell(sel["coord"]))
        if self.target == "zone":
            name = app.current_zone()
            if not name or name not in m.zones:
                return None, "no zone selected (Zones tab / cell zone)", None
            return ("zone", name), f"zone {name!r}", m.zones[name]
        if self.target == "palette":
            name = app.current_palette()
            if not name or name not in m.palettes:
                return None, "no palette selected (Palettes tab)", None
            return ("palette", name), f"palette {name!r}", m.palettes[name]
        if self.target == "sprite":
            name = app.current_sprite()
            if not name or name not in m.sprites:
                return None, "no sprite selected (Sprites tab)", None
            return ("sprite", name), f"sprite {name!r}", m.sprites[name]
        return ("manifest",), "project manifest", m.manifest

    def reload(self) -> None:
        key, label, data = self._resolve()
        self._loaded_key = key
        self._label = label
        if key is None:
            self.area.set_text("")
            self.msg, self.msg_color = label, T.TEXT_DIM
            return
        self.area.set_text(pretty_json(data))
        self.msg, self.msg_color = f"editing {label}", T.TEXT_DIM

    def rebuild(self) -> None:
        key, _, _ = self._resolve()
        if key != self._loaded_key or not self.area.dirty:
            self.reload()

    def format(self) -> None:
        try:
            data = json.loads(self.area.text)
        except ValueError as exc:
            self._parse_error(exc)
            return
        row = self.area.row
        self.area.set_text(pretty_json(data))
        self.area.dirty = True
        self.area.row = min(row, len(self.area.lines) - 1)

    def _parse_error(self, exc) -> None:
        line = getattr(exc, "lineno", None)
        self.area.error_line = (line - 1) if line else None
        if line:
            self.area.row = line - 1
            self.area._ensure_visible()
        self.msg, self.msg_color = f"invalid JSON: {exc}", T.ERROR

    def apply(self) -> None:
        key = self._loaded_key
        if key is None:
            return
        try:
            data = json.loads(self.area.text)
        except ValueError as exc:
            self._parse_error(exc)
            return
        m, app = self.model, self.app
        kind = key[0]
        res = None
        if kind == "cell":
            res = app.run(ops.set_cell_data, key[1], data, label="edit cell JSON")
        elif kind == "entity":
            if not isinstance(data, dict):
                self.msg, self.msg_color = "entity must be an object", T.ERROR
                return
            cell = copy.deepcopy(m.cell(key[1]))
            cell["entities"][key[2]] = data
            res = app.run(ops.set_cell_data, key[1], cell,
                          label="edit entity JSON")
        elif kind == "zone":
            res = app.run(ops.set_zone, key[1], data, label="edit zone JSON")
        elif kind == "palette":
            res = app.run(ops.set_palette_data, key[1], data,
                          label="edit palette JSON")
        elif kind == "sprite":
            res = app.run(ops.set_sprite_data, key[1], data,
                          label="edit sprite JSON")
        elif kind == "manifest":
            if not isinstance(data, dict):
                self.msg, self.msg_color = "manifest must be an object", T.ERROR
                return
            changed = {k: v for k, v in data.items()
                       if m.manifest.get(k) != v and k != "format"}

            def apply_all():
                with m.transaction("edit manifest JSON"):
                    for k, v in changed.items():
                        ops.set_manifest_value(m, k, v)
                return {"changed": sorted(changed)}
            res = app.run(apply_all, label="edit manifest")
        if res is not None:
            self.area.dirty = False
            self.reload()
            self.msg, self.msg_color = f"applied to {self._label}", T.OK
        else:
            self.msg, self.msg_color = app.last_error, T.ERROR

    def draw(self, surface, ui) -> None:
        T.text(surface, self.msg + ("   (modified)" if self.area.dirty else ""),
               (self.rect.x + 8, self.rect.y + 32), self.msg_color, 12,
               max_w=self.rect.w - 16)
        super().draw(surface, ui)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.KEYDOWN and ui.focus is self.area and \
                ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and \
                (ev.mod & (pygame.KMOD_META | pygame.KMOD_CTRL)):
            self.apply()
            return True
        return super().handle(ev, ui)


# ---------------------------------------------------------------------------
# Problems
# ---------------------------------------------------------------------------


class ProblemsPanel(Panel):
    title = "Problems"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.show = {"error": True, "warning": True, "info": False}
        self.list = self.add(ListView(on_select=self._goto, row_h=20))
        self.btns = []
        for sev in ("error", "warning", "info"):
            b = Button(lambda s=sev: f"{s}s ({self._count(s)})",
                       lambda s=sev: self._toggle(s),
                       selected=lambda s=sev: self.show[s], size=12)
            self.btns.append(self.add(b))
        self.reval = self.add(Button("Re-validate", app.revalidate, size=12))

    def _count(self, sev) -> int:
        return sum(1 for i in self.app.issues if i.severity == sev)

    def _toggle(self, sev) -> None:
        self.show[sev] = not self.show[sev]
        self.rebuild()

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        x = rect[0] + 6
        for b in self.btns:
            b.layout((x, rect[1] + 6, 100, 22))
            x += 104
        self.reval.layout((x, rect[1] + 6, rect[0] + rect[2] - x - 6, 22))
        self.list.layout((rect[0] + 6, rect[1] + 34, rect[2] - 12,
                          rect[3] - 40))

    def rebuild(self) -> None:
        items = []
        for n, issue in enumerate(self.app.issues):
            if not self.show.get(issue.severity, True):
                continue
            where = f"[{issue.cell[0]},{issue.cell[1]}] " if issue.cell else ""
            items.append((f"{issue.severity[0].upper()} {where}{issue.message}",
                          n, T.SEVERITY_COLORS.get(issue.severity)))
        if not items:
            items = [("No problems shown.", None, T.OK)]
        self.list.set_items(items)

    def _goto(self, n) -> None:
        issue = self.app.issues[n]
        if issue.cell is not None and self.model.has_cell(issue.cell):
            self.app.open_room(issue.cell, fit=False)
            self.app.select_cell(issue.cell)
        self.app.status(f"{issue.code}: {issue.message}")


# ---------------------------------------------------------------------------
# Colour editing (shared by palettes and zones)
# ---------------------------------------------------------------------------


class ColorEditor:
    """Swatch rows + RGB sliders + hex field for one selected colour.

    get(key, index) -> rgb, commit(key, index, rgb) applies an op. Slider
    drags preview locally and commit once on release (one undo step)."""

    def __init__(self, app, get, commit, on_select=None) -> None:
        self.app = app
        self.get = get
        self.commit = commit
        self.on_select = on_select
        self.sel = None  # (key, index)
        self.preview = None

    def current(self):
        if self.sel is None:
            return [0, 0, 0]
        if self.preview is not None:
            return self.preview
        try:
            return list(self.get(*self.sel))
        except (KeyError, IndexError, TypeError):
            return [0, 0, 0]

    def add_swatch_rows(self, f, rows) -> None:
        """rows: [(label, key, count or None)]."""
        for label, key, count in rows:
            widgets = [Label(label, T.TEXT_DIM, 12)]
            widths = [80]
            idxs = range(count) if count else [None]
            for i in idxs:
                widgets.append(Swatch(
                    lambda k=key, i=i: self._color(k, i),
                    on_click=lambda k=key, i=i: self.select(k, i),
                    selected=lambda k=key, i=i: self.sel == (k, i),
                    tooltip=f"{key}[{i}]" if i is not None else key))
                widths.append(26)
            f.add_row(widgets, 24, widths)

    def _color(self, key, i):
        if self.sel == (key, i) and self.preview is not None:
            return self.preview
        try:
            return self.get(key, i)
        except (KeyError, IndexError, TypeError):
            return (0, 0, 0)

    def select(self, key, i) -> None:
        self.sel = (key, i)
        self.preview = None
        if self.on_select:
            self.on_select()

    def add_editor(self, f) -> None:
        if self.sel is None:
            f.add_row([Label("Click a swatch to edit it.", T.TEXT_DIM, 12)])
            return
        key, i = self.sel
        name = f"{key}[{i}]" if i is not None else key
        f.add_row([Label(f"editing {name}", T.ACCENT, 12),
                   Swatch(self.current)], 26, [None, 60])
        for ch, col in ((0, (230, 80, 80)), (1, (80, 220, 100)),
                        (2, (90, 140, 255))):
            f.add_row([Label("RGB"[ch], T.TEXT_DIM, 12),
                       Slider(0, 255, lambda ch=ch: self.current()[ch],
                              on_change=lambda v, ch=ch: self._slide(ch, v),
                              on_commit=lambda v: self._commit(), color=col),
                       Label(lambda ch=ch: str(self.current()[ch]), T.TEXT, 12)],
                      20, [20, None, 34])
        f.add_row([Label("r,g,b / #hex", T.TEXT_DIM, 12),
                   TextInput(lambda: _rgb_str(self.current()), self._typed)],
                  T.ROW_H, [110, None])

    def _slide(self, ch, v) -> None:
        cur = list(self.current())
        cur[ch] = v
        self.preview = cur

    def _commit(self) -> None:
        if self.preview is None or self.sel is None:
            return
        rgb, self.preview = self.preview, None
        self.commit(self.sel[0], self.sel[1], rgb)

    def _typed(self, v):
        if self.sel is None:
            return False
        try:
            rgb = _parse_rgb(v)
        except (OpError, ValueError) as exc:
            self.app.error(str(exc))
            return False
        return self.commit(self.sel[0], self.sel[1], rgb) is not None


class _ListAndForm(Panel):
    """List on top, scrollable form below."""

    list_h = 150

    def __init__(self, app) -> None:
        super().__init__(app)
        self.list = self.add(ListView(on_select=self._on_pick, mono=True))
        self.form = self.add(Form())
        self.current = None

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        self.list.layout((rect[0] + 6, rect[1] + 6, rect[2] - 12, self.list_h))
        self.form.layout((rect[0], rect[1] + self.list_h + 10, rect[2],
                          rect[3] - self.list_h - 10))

    def _on_pick(self, value) -> None:
        self.current = value
        self.rebuild()


# ---------------------------------------------------------------------------
# Palettes + textures
# ---------------------------------------------------------------------------


class PalettesPanel(_ListAndForm):
    title = "Palettes"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.colors = ColorEditor(app, self._get, self._commit, self.rebuild)
        self._thumbs = {}

    def _get(self, key, i):
        cols = self.model.palettes[self.current]["build"]["colors"]
        return cols[key] if i is None else cols[key][i]

    def _commit(self, key, i, rgb):
        name = self.current
        if i is not None:
            return self.app.run(ops.set_palette_color, name, key, i, rgb,
                                label=f"palette {name} {key}[{i}]")
        colors = copy.deepcopy(self.model.palettes[name]["build"]["colors"])
        colors[key] = rgb
        return self.app.run(ops.set_palette_colors, name, colors,
                            label=f"palette {name} {key}")

    def thumb(self, name, kind):
        """First 48x48 frame of textures/<name>/<kind>.png (cached by
        mtime), or None."""
        pal = self.model.palettes.get(name, {})
        spec = pal.get("textures", {}).get(kind)
        if not spec:
            return None
        path = self.model.project.textures_dir / name / spec.get("file", f"{kind}.png")
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return None
        key = (str(path), mtime)
        img = self._thumbs.get(key)
        if img is None:
            try:
                sheet = pygame.image.load(str(path))
                tp = int(pal.get("tile_px", 48))
                top = 0
                if spec.get("layout") == "autotile16":
                    # mask 15 (fully surrounded) row shows the body texture
                    top = 15 * (tp + int(spec.get("overhang_px", 0)))
                    top = min(top, max(0, sheet.get_height() - tp))
                frame = sheet.subsurface((0, top, min(tp, sheet.get_width()),
                                          min(tp, sheet.get_height() - top)))
                img = pygame.transform.scale(frame, (48, 48))
            except Exception:
                return None
            self._thumbs[key] = img
        return img

    def rebuild(self) -> None:
        m, app = self.model, self.app
        usage = m.palette_usage()
        names = sorted(m.palettes)
        if self.current not in m.palettes:
            self.current = app.brush_palette if app.brush_palette in m.palettes \
                else (names[0] if names else None)
        self.list.selected_value = self.current
        self.list.set_items([(f"{n:<20} {usage.get(n, 0):>5} tiles", n)
                             for n in names])
        f = self.form
        keep = f.scroll
        f.clear()
        f.add_row([Button("New…", self._new), Button("Rename…", self._rename),
                   Button("Delete…", self._delete),
                   Button("Rebuild", self._rebuild)], 24)
        if self.current is None:
            f.add_row([Label("No palettes.", T.TEXT_DIM, 12)])
            return
        name = self.current
        pal = m.palettes[name]
        f.heading(f"Palette {name!r}")
        f.field("display name", TextInput(
            lambda: m.palettes.get(name, {}).get("display_name", ""),
            lambda v: app.run(ops.set_palette_data, name,
                              dict(copy.deepcopy(m.palettes[name]),
                                   display_name=v)) is not None))
        f.add_row([Button("Use as brush palette",
                          lambda: app.set_brush(app.brush_kind, name),
                          selected=lambda: app.brush_palette == name)], 24)
        build = pal.get("build", {})
        if "colors" in build:
            f.spacer()
            f.heading("Colours (textures rebuild on change)")
            self.colors.add_swatch_rows(f, [("rock", "rock", 8),
                                            ("cap", "cap", 8),
                                            ("accent", "accent", None)])
            self.colors.add_editor(f)
        f.spacer()
        f.heading("Textures")
        locked = set(build.get("locked", []))
        for kind in assets.TEXTURE_KINDS:
            state = "imported (locked)" if kind in locked else (
                "generated" if kind in pal.get("textures", {}) else "missing")
            f.add_row([Image(lambda k=kind: self.thumb(name, k)),
                       Label(kind, T.TEXT, 12),
                       Label(state, T.TEXT_DIM, 12),
                       Button("Import…", lambda k=kind: self._import(k), size=12),
                       Button("Unlock", lambda k=kind: self._unlock(k), size=12,
                              enabled=kind in locked),
                       Button("Export…", lambda k=kind: self._export(k), size=12)],
                      48, [48, 60, None, 64, 56, 64])
        f.add_row([Label("Import accepts a 16/48 px tile, a strip, or a full "
                         "sheet (see AGENTS.md).", T.TEXT_DIM, 11)])
        f.scroll = keep

    def _new(self) -> None:
        app, m = self.app, self.model

        def create(vals):
            name, base = vals[0].strip(), vals[1].strip() or "default"
            res = app.run(ops.create_palette, name, base, label="new palette")
            if res is None:
                raise OpError(app.last_error)
            app.background(lambda: assets.create_palette_assets(m, name, base),
                           f"generated textures for {name}")
            self.current = name
            self.rebuild()
        app.prompt("New palette", [("name", ""), ("copy of", self.current or "default")],
                   create)

    def _rename(self) -> None:
        if not self.current:
            return
        old = self.current

        def go(vals):
            res = self.app.run(ops.rename_palette, old, vals[0].strip())
            if res is None:
                raise OpError(self.app.last_error)
            self.current = vals[0].strip()
            if self.app.brush_palette == old:
                self.app.brush_palette = self.current
        self.app.prompt(f"Rename palette {old!r}", [("new name", old)], go)

    def _delete(self) -> None:
        if not self.current:
            return
        name = self.current
        others = [n for n in sorted(self.model.palettes) if n != name]

        def go(vals):
            repl = vals[0].strip() or None
            res = self.app.run(ops.delete_palette, name, repl)
            if res is None:
                raise OpError(self.app.last_error)
            self.current = None
        self.app.prompt(f"Delete palette {name!r}",
                        [("re-label tiles to", others[0] if others else "")],
                        go, message="Tiles using it are re-labelled to the "
                        "replacement (blank = auto). Texture files are kept "
                        "until you save.")

    def _rebuild(self) -> None:
        if self.current:
            name = self.current
            self.app.background(lambda: assets.rebuild_textures(self.model, [name]),
                                f"rebuilt textures for {name}")

    def _import(self, kind) -> None:
        name = self.current

        def go(vals):
            path = os.path.expanduser(vals[0].strip())
            mode = vals[1].strip() or "auto"
            assets.import_texture(self.model, name, kind, path, mode=mode)
            self.app.after_asset_change()
            self.app.status(f"imported {kind} into {name}")
        self.app.prompt(f"Import {kind} texture into {name!r}",
                        [("PNG path", ""), ("mode", "auto")], go,
                        message="mode: auto | single | full | strip. The kind "
                        "is locked so rebuilds keep your art (Unlock to "
                        "regenerate).")

    def _unlock(self, kind) -> None:
        name = self.current
        self.app.background(lambda: assets.unlock_texture(self.model, name, kind),
                            f"unlocked {kind}; regenerated")

    def _export(self, kind) -> None:
        name = self.current

        def go(vals):
            out = assets.export_texture(self.model, name, kind,
                                        os.path.expanduser(vals[0].strip()))
            self.app.status(f"exported {out}")
        default = str(Path.home() / f"{name}_{kind}.png")
        self.app.prompt(f"Export {kind} sheet", [("save to", default)], go)


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


class ZonesPanel(_ListAndForm):
    title = "Zones"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.colors = ColorEditor(app, self._get, self._commit, self.rebuild)

    def _get(self, key, i):
        z = self.model.zones[self.current]
        return z[key] if i is None else z[key][i]

    def _commit(self, key, i, rgb):
        return self.app.run(ops.set_zone_color, self.current, key, rgb, i,
                            label=f"zone {self.current} {key}")

    def rebuild(self) -> None:
        m, app = self.model, self.app
        names = sorted(m.zones)
        if self.current not in m.zones:
            z = app.current_zone(from_panel=False)
            self.current = z if z in m.zones else (names[0] if names else None)
        counts = {}
        for d in m.cells.values():
            counts[d.get("zone", "")] = counts.get(d.get("zone", ""), 0) + 1
        self.list.selected_value = self.current
        self.list.set_items([(f"{n:<22} {counts.get(n, 0):>4} cells", n)
                             for n in names])
        f = self.form
        keep = f.scroll
        f.clear()
        f.add_row([Button("New…", self._new), Button("Rename…", self._rename),
                   Button("Delete", self._delete)], 24)
        if self.current is None:
            return
        name = self.current
        f.heading(f"Zone {name!r}")
        sel = app.sel
        if sel is not None:
            coord = sel["coord"]
            f.add_row([Button(f"Assign to cell {coord[0]},{coord[1]}",
                              lambda: app.run(ops.set_cell_props, coord,
                                              zone=name))], 24)
        f.spacer()
        f.heading("Default palette")
        f.add_row([
            Label(lambda: m.zones[name].get("default_palette") or "(none)",
                  T.TEXT, 12),
            Button("...", lambda: app.open_menu(
                [("(none)", lambda: app.run(ops.set_zone_palette, name, None))] +
                [(p, lambda p=p: app.run(ops.set_zone_palette, name, p))
                 for p in sorted(m.palettes)], app.mouse),
                tooltip="choose the zone's default palette", size=12),
            Button("Apply to zone tiles",
                   lambda: app.confirm(
                       f"Re-skin every tile in zone {name!r} with palette "
                       f"{m.zones[name].get('default_palette')!r}?",
                       lambda: app.run(ops.apply_zone_palette, name)),
                   enabled=lambda: bool(m.zones[name].get("default_palette")),
                   size=12),
        ], T.ROW_H, [None, 30, 150])
        f.spacer()
        f.heading("Colours (live preview in the room)")
        z = m.zones[name]
        rows = []
        for key in ("rock", "cap"):
            if isinstance(z.get(key), list) and z[key] and \
                    isinstance(z[key][0], list):
                rows.append((key, key, len(z[key])))
        for key in ("accent", "fog_top", "fog_bottom", "ambient"):
            if key in z:
                rows.append((key, key, None))
        self.colors.add_swatch_rows(f, rows)
        self.colors.add_editor(f)
        f.scroll = keep

    def _new(self) -> None:
        base = self.current

        def go(vals):
            name = vals[0].strip()
            data = copy.deepcopy(self.model.zones.get(base)) if base else None
            if data is None:
                data = {"rock": [[40 + 16 * i] * 3 for i in range(8)],
                        "cap": [[80 + 16 * i] * 3 for i in range(8)],
                        "accent": [200, 200, 220], "fog_top": [10, 10, 20],
                        "fog_bottom": [20, 20, 30], "ambient": [40, 40, 60]}
            res = self.app.run(ops.set_zone, name, data, label="new zone")
            if res is None:
                raise OpError(self.app.last_error)
            self.current = name
        self.app.prompt("New zone", [("name", "")], go,
                        message=f"Starts as a copy of {base!r}." if base else "")

    def _rename(self) -> None:
        old = self.current
        if not old:
            return

        def go(vals):
            res = self.app.run(ops.rename_zone, old, vals[0].strip())
            if res is None:
                raise OpError(self.app.last_error)
            self.current = vals[0].strip()
        self.app.prompt(f"Rename zone {old!r}", [("new name", old)], go,
                        message="Cells using it are updated too.")

    def _delete(self) -> None:
        name = self.current
        if not name:
            return
        self.app.confirm(f"Delete zone {name!r}?",
                         lambda: self.app.run(ops.delete_zone, name, True))


# ---------------------------------------------------------------------------
# Sprites
# ---------------------------------------------------------------------------


class SpritesPanel(Panel):
    title = "Sprites"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.list = self.add(ListView(on_select=self._pick, mono=True))
        self.area = self.add(TextArea())
        self.apply_btn = self.add(Button("Apply", self.apply))
        self.build_btn = self.add(Button("Build sheet", self.build))
        self.current = None
        self.msg = ""
        self.msg_color = T.TEXT_DIM
        self._sheet = None
        self._sheet_key = None

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        x, y, w, h = rect
        self.list.layout((x + 6, y + 6, 160, 110))
        self.area.layout((x + 6, y + 150, w - 12, h - 196))
        self.apply_btn.layout((x + 6, y + h - 38, 100, 28))
        self.build_btn.layout((x + 112, y + h - 38, 110, 28))

    def _pick(self, name) -> None:
        self.current = name
        self.load()

    def load(self) -> None:
        d = self.model.sprites.get(self.current)
        self.area.set_text(pretty_json(d) if d else "")

    def rebuild(self) -> None:
        names = sorted(self.model.sprites)
        if self.current not in self.model.sprites:
            self.current = names[0] if names else None
            self.load()
        elif not self.area.dirty:
            self.load()
        self.list.selected_value = self.current
        self.list.set_items([(n, n) for n in names])

    def apply(self) -> None:
        try:
            data = json.loads(self.area.text)
        except ValueError as exc:
            self.msg, self.msg_color = f"invalid JSON: {exc}", T.ERROR
            return
        if self.app.run(ops.set_sprite_data, self.current, data,
                        label=f"edit sprite {self.current}") is not None:
            self.area.dirty = False
            self.msg, self.msg_color = "applied (Build sheet to see it in game)", T.OK
        else:
            self.msg, self.msg_color = self.app.last_error, T.ERROR

    def build(self) -> None:
        name = self.current
        if name:
            self.app.background(lambda: assets.build_sprites(self.model, [name]),
                                f"built sprite sheet {name}")
            self._sheet_key = None

    def _sheet_img(self):
        if not self.current:
            return None
        path = self.model.project.sprites_dir / f"{self.current}.png"
        try:
            key = (str(path), path.stat().st_mtime)
        except OSError:
            return None
        if key != self._sheet_key:
            try:
                img = pygame.image.load(str(path))
                scale = min(1.0, (self.rect.w - 190) / max(1, img.get_width()),
                            110 / max(1, img.get_height()))
                if scale < 1:
                    img = pygame.transform.smoothscale(
                        img, (max(1, int(img.get_width() * scale)),
                              max(1, int(img.get_height() * scale))))
                self._sheet = img
            except Exception:
                self._sheet = None
            self._sheet_key = key
        return self._sheet

    def draw(self, surface, ui) -> None:
        img = self._sheet_img()
        if img is not None:
            pygame.draw.rect(surface, (60, 60, 70),
                             (self.rect.x + 172, self.rect.y + 6,
                              img.get_width(), img.get_height()))
            surface.blit(img, (self.rect.x + 172, self.rect.y + 6))
        T.text(surface, self.msg or "Sprite source JSON (voxel grids + colours)",
               (self.rect.x + 8, self.rect.y + 126), self.msg_color, 12,
               max_w=self.rect.w - 16)
        super().draw(surface, ui)


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------


class ProjectPanel(Panel):
    title = "Project"

    def __init__(self, app) -> None:
        super().__init__(app)
        self.form = self.add(Form())

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        self.form.layout(rect)

    def rebuild(self) -> None:
        app, m = self.app, self.model
        f = self.form
        f.clear()
        f.heading("Project")
        f.field("folder", Label(str(m.project.root), T.TEXT_DIM, 12))
        for key in ("title", "name"):
            f.field(key, TextInput(
                lambda k=key: m.manifest.get(k, ""),
                lambda v, k=key: app.run(ops.set_manifest_value, k, v,
                                         label=f"set {k}") is not None))
        f.field("start cell", TextInput(
            lambda: f"{m.start_cell[0]},{m.start_cell[1]}" if m.start_cell else "",
            lambda v: app.run(ops.set_start_cell,
                              [int(p) for p in v.split(",")]) is not None))
        n_ent = sum(len(d.get("entities", [])) for d in m.cells.values())
        rooms = len({tuple(sorted(m.room_cells(c))) for c in m.cells}) \
            if len(m.cells) < 400 else "?"
        f.field("contents", Label(f"{len(m.cells)} cells, {rooms} rooms, "
                                  f"{n_ent} entities, {len(m.palettes)} "
                                  f"palettes, {len(m.zones)} zones",
                                  T.TEXT_DIM, 12))
        f.spacer()
        f.heading("Actions")
        f.add_row([Button("Save (Cmd+S)", app.save),
                   Button("Validate", app.revalidate)], 26)
        f.add_row([Button("Playtest start (F5)", lambda: app.playtest(None, None)),
                   Button("Build Game…", app.build_game)], 26)
        f.add_row([Button("Open project…", app.open_project_dialog),
                   Button("New project…", app.new_project_dialog)], 26)
        f.spacer()
        f.heading("Options")
        f.add_row([Button(lambda: "Autosave every 2 min: "
                          + ("ON" if app.autosave else "off"),
                          app.toggle_autosave,
                          selected=lambda: app.autosave)], 24)
        f.add_row([Button(lambda: "Mouse wheel: " + (
            "zoom" if app.wheel_zooms else "pan (Cmd+wheel zooms)"),
            app.toggle_wheel)], 24)
        f.spacer()
        f.heading("Shortcuts")
        for line in (
                "Cmd+Z / Shift+Cmd+Z  undo / redo      Cmd+S  save",
                "Tab  room <-> world map        F5  playtest here",
                "V B R F X I N E  tools        1-5  brush kind",
                "Space/middle-drag  pan    Cmd+wheel, +/-  zoom   0  fit",
                "Cmd+G grid  Cmd+K kinds  Cmd+L lighting",
                "Del  delete selected entity / cells"):
            f.add_row([Label(line, T.TEXT_DIM, 12, mono=True)], 18)
