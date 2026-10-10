"""World Map viewport: the schematic map of every cell.

Click selects a cell (Shift/Cmd toggles multi-select), drag selected
cells to move them (Alt-drag duplicates), double-click opens the room in
the Room Editor, click an empty square to select it and press N (or
right-click) to create a cell there. Cmd+wheel zooms, wheel pans.
Uses maker.core.render.render_world_map + world_map_layout so clicks map
back exactly to the drawn grid.
"""

from __future__ import annotations

import pygame

from maker.core import ops, render
from maker.gui import theme as T
from maker.gui.widgets import Widget, cmd_down


class MapView(Widget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.model = app.model
        self.ppc = 32
        self.pan = [20.0, 30.0]
        self.selection = set()
        self.empty_target = None  # selected empty grid square
        self._surf = None
        self._key = None
        self.version = 0
        self._drag = None
        self._panning = None
        self.hover = None
        self._last_click = (0, None)

    def on_change(self, changes) -> None:
        if changes.cells or changes.index or changes.zones:
            self.version += 1
            self.selection = {c for c in self.selection
                              if self.model.has_cell(c)}

    def _minxy(self):
        b = self.model.bounds()
        return (b[0], b[1]) if b else (0, 0)

    def cell_at(self, pos):
        off, _ = render.world_map_layout(self.model, self.ppc)
        minx, miny = self._minxy()
        x = pos[0] - self.rect.x - self.pan[0] - off[0]
        y = pos[1] - self.rect.y - self.pan[1] - off[1]
        return (minx + int(x // self.ppc), miny + int(y // self.ppc))

    def cell_rect(self, coord) -> pygame.Rect:
        off, _ = render.world_map_layout(self.model, self.ppc)
        minx, miny = self._minxy()
        return pygame.Rect(
            self.rect.x + self.pan[0] + off[0] + (coord[0] - minx) * self.ppc,
            self.rect.y + self.pan[1] + off[1] + (coord[1] - miny) * self.ppc,
            self.ppc, self.ppc)

    def center_on(self, coord) -> None:
        r = self.cell_rect(coord)
        self.pan[0] += self.rect.centerx - r.centerx
        self.pan[1] += self.rect.centery - r.centery

    def fit(self) -> None:
        b = self.model.bounds()
        if not b or self.rect.w <= 0:
            return
        nx, ny = b[2] - b[0] + 1, b[3] - b[1] + 1
        self.ppc = int(max(8, min(64, min((self.rect.w - 60) / nx,
                                          (self.rect.h - 60) / ny))))
        _, (w, h) = render.world_map_layout(self.model, self.ppc)
        self.pan = [(self.rect.w - w) / 2, (self.rect.h - h) / 2]

    def draw(self, surface, ui) -> None:
        pygame.draw.rect(surface, (10, 10, 14), self.rect)
        key = (self.version, self.ppc, frozenset(self.selection))
        if key != self._key:
            self._surf = render.render_world_map(
                self.model, self.ppc, highlight=self.selection)
            self._key = key
        clip = surface.get_clip()
        surface.set_clip(self.rect)
        # faint grid so empty squares are clickable targets
        off, (w, h) = render.world_map_layout(self.model, self.ppc)
        gx0 = self.rect.x + (self.pan[0] + off[0]) % self.ppc
        gy0 = self.rect.y + (self.pan[1] + off[1]) % self.ppc
        x = gx0
        while x < self.rect.right:
            pygame.draw.line(surface, (22, 22, 30), (x, self.rect.y),
                             (x, self.rect.bottom))
            x += self.ppc
        y = gy0
        while y < self.rect.bottom:
            pygame.draw.line(surface, (22, 22, 30), (self.rect.x, y),
                             (self.rect.right, y))
            y += self.ppc
        surface.blit(self._surf, (self.rect.x + self.pan[0],
                                  self.rect.y + self.pan[1]))
        rv = self.app.room_view
        if rv.room is not None:
            for c in rv.room.cells:
                pygame.draw.rect(surface, T.ACCENT, self.cell_rect(c), 1)
        for issue in ui.issues:
            if issue.cell is not None and issue.severity == "error":
                pygame.draw.rect(surface, T.ERROR,
                                 self.cell_rect(issue.cell).inflate(-6, -6), 2)
        if self.empty_target is not None:
            r = self.cell_rect(self.empty_target)
            pygame.draw.rect(surface, T.OK, r, 2)
            T.text(surface, "+", (r.centerx - 4, r.centery - 9), T.OK, 16)
        if self._drag and self._drag.get("moved"):
            dx, dy = self._drag["delta"]
            for c in self.selection:
                r = self.cell_rect((c[0] + dx, c[1] + dy))
                pygame.draw.rect(surface, T.ACCENT if not self._drag["dup"]
                                 else T.OK, r, 2)
        if self.hover is not None and self.ppc >= 24 and \
                self.model.has_cell(self.hover):
            r = self.cell_rect(self.hover)
            pygame.draw.rect(surface, (200, 200, 220), r, 1)
        surface.set_clip(clip)
        T.text(surface, "World Map — double-click: open room · N: new cell · "
               "Del: delete · drag: move (Alt: duplicate) · S: set start",
               (self.rect.x + 8, self.rect.y + 4), T.TEXT_DIM, 12,
               max_w=self.rect.w - 16)

    def hover_info(self) -> str:
        if self.hover is None:
            return ""
        c = self.hover
        if not self.model.has_cell(c):
            return f"cell {c[0]},{c[1]} (empty)"
        d = self.model.cell(c)
        n = len(self.model.room_cells(c))
        return (f"cell {c[0]},{c[1]}  zone {d.get('zone', '')}  "
                f"room of {n} cell(s)  entities {len(d.get('entities', []))}")

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEMOTION:
            if self._panning:
                sx, sy, px, py = self._panning
                self.pan = [px + ev.pos[0] - sx, py + ev.pos[1] - sy]
                return True
            if not self.rect.collidepoint(ev.pos) and not self._drag:
                return False
            self.hover = self.cell_at(ev.pos)
            ui.set_hover_info(self.hover_info())
            if self._drag:
                s = self._drag["start"]
                delta = (self.hover[0] - s[0], self.hover[1] - s[1])
                self._drag["delta"] = delta
                if delta != (0, 0):
                    self._drag["moved"] = True
            return True
        if ev.type == pygame.MOUSEWHEEL and self.rect.collidepoint(ui.mouse):
            if cmd_down(ui.mods) or (ui.wheel_zooms and not ev.x):
                old = self.ppc
                self.ppc = max(8, min(96, int(round(old * (1.15 ** ev.y)))))
                if self.ppc == old:
                    self.ppc = max(8, min(96, old + (1 if ev.y > 0 else -1)))
                # keep the point under the mouse fixed
                mx = ui.mouse[0] - self.rect.x
                my = ui.mouse[1] - self.rect.y
                f = self.ppc / old
                self.pan = [mx - (mx - self.pan[0]) * f,
                            my - (my - self.pan[1]) * f]
            else:
                self.pan[0] += ev.x * 30
                self.pan[1] += ev.y * 30
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN and self.rect.collidepoint(ev.pos):
            ui.set_focus(None)
            if ev.button == 2 or (ev.button == 1 and ui.space_down):
                self._panning = (ev.pos[0], ev.pos[1], self.pan[0], self.pan[1])
                return True
            c = self.cell_at(ev.pos)
            if ev.button == 3:
                self._menu(ui, c)
                return True
            if ev.button != 1:
                return True
            now = pygame.time.get_ticks()
            double = self._last_click[1] == c and now - self._last_click[0] < 400
            self._last_click = (now, c)
            if self.model.has_cell(c):
                self.empty_target = None
                multi = bool(ui.mods & (pygame.KMOD_SHIFT | pygame.KMOD_META
                                        | pygame.KMOD_CTRL))
                if double:
                    ui.open_room(c)
                    return True
                if multi:
                    self.selection ^= {c}
                elif c not in self.selection:
                    self.selection = {c}
                ui.select_cell(c, from_map=True)
                self._drag = {"start": c, "delta": (0, 0), "moved": False,
                              "dup": bool(ui.mods & pygame.KMOD_ALT)}
            else:
                self.selection = set()
                self.empty_target = c
                ui.status(f"empty square {c[0]},{c[1]} — press N or "
                          f"right-click to create a cell")
                if double:
                    self.new_cell(ui, c)
            return True
        if ev.type == pygame.MOUSEBUTTONUP:
            if self._panning and ev.button in (1, 2):
                self._panning = None
                return True
            if self._drag and ev.button == 1:
                d, self._drag = self._drag, None
                if d["moved"] and self.selection:
                    dx, dy = d["delta"]
                    coords = sorted(self.selection)
                    fn = ops.duplicate_cells if d["dup"] else ops.move_cells
                    res = ui.run(fn, [list(c) for c in coords], dx, dy,
                                 label=("duplicate" if d["dup"] else "move")
                                 + f" {len(coords)} cell(s)")
                    if res is not None:
                        self.selection = {(c[0] + dx, c[1] + dy)
                                          for c in coords}
                        rv = self.app.room_view
                        if rv.focus_cell in coords and not d["dup"]:
                            rv.focus_cell = (rv.focus_cell[0] + dx,
                                             rv.focus_cell[1] + dy)
                            rv.refresh()
                return True
        if ev.type == pygame.KEYDOWN and ui.focus is None:
            return self._key_cmd(ev, ui)
        return False

    def _key_cmd(self, ev, ui) -> bool:
        k = ev.key
        if cmd_down(ev.mod):
            return False
        if k == pygame.K_n:
            target = self.empty_target or (self.hover if self.hover and not
                                           self.model.has_cell(self.hover)
                                           else None)
            if target is not None:
                self.new_cell(ui, target)
            return True
        if k in (pygame.K_DELETE, pygame.K_BACKSPACE) and self.selection:
            ui.delete_cells(sorted(self.selection))
            return True
        if k == pygame.K_s and len(self.selection) == 1:
            ui.run(ops.set_start_cell, next(iter(self.selection)),
                   label="set start cell")
            return True
        if k in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.selection:
            ui.open_room(sorted(self.selection)[0])
            return True
        if k == pygame.K_0:
            self.fit()
            return True
        return False

    def new_cell(self, ui, coord) -> None:
        res = ui.run(ops.new_cell, coord, label=f"new cell {coord}")
        if res is not None:
            self.empty_target = None
            self.selection = {tuple(coord)}
            ui.select_cell(tuple(coord), from_map=True)

    def _menu(self, ui, c) -> None:
        if self.model.has_cell(c):
            if c not in self.selection:
                self.selection = {c}
                ui.select_cell(c, from_map=True)
            sel = sorted(self.selection)
            items = [
                ("Open room", lambda: ui.open_room(c)),
                ("Set as start cell", lambda: ui.run(ops.set_start_cell, c)),
                ("Playtest from this cell", lambda: ui.playtest(c, None)),
                None,
                (f"Delete {len(sel)} cell(s)", lambda: ui.delete_cells(sel)),
            ]
            for d, (dx, dy) in (("north", (0, -1)), ("south", (0, 1)),
                                ("west", (-1, 0)), ("east", (1, 0))):
                n = (c[0] + dx, c[1] + dy)
                if self.model.has_cell(n):
                    items.insert(3, (f"Edge {d}…", lambda d=d: self.app.edge_menu(
                        c, d, ui.mouse)))
        else:
            self.empty_target = c
            items = []
            for tmpl in ("walled", "empty", "solid"):
                items.append((f"New {tmpl} cell at {c[0]},{c[1]}",
                              lambda t=tmpl: self._new_tmpl(ui, c, t)))
        ui.open_menu(items, ui.mouse)

    def _new_tmpl(self, ui, c, template) -> None:
        res = ui.run(ops.new_cell, c, template=template, label="new cell")
        if res is not None:
            self.empty_target = None
            self.selection = {c}
            ui.select_cell(c, from_map=True)
