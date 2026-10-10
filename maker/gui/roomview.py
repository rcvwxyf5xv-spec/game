"""Room Editor viewport: the live-rendered room plus editing tools.

The room containing the focused cell is rendered once at game SCALE
(3 screen px per game px) through maker.core.render (the game's own
VoxelRenderer) into `room_surf`, re-rendered when a ChangeSet touches
it, then shown scaled by `zoom` and panned by `pan`. Overlays are drawn
every frame with maker.core.overlays at the same transform.

Screen <-> world:  world_game_px = origin_px + (screen - view - pan) /
(SCALE * zoom), where origin_px is the world px of the room bbox's
top-left cell.

All edits go through maker.core.ops (one undo step per click/drag: a
drag's per-frame ops are merged with model.coalesce_history).
"""

from __future__ import annotations

from typing import Optional, Tuple

import pygame

from src.engine.constants import CELL_HEIGHT_TILES, CELL_WIDTH_TILES, TILE_SIZE
from src.main import SCALE

from maker.core import ops, render
from maker.core.errors import OpError
from maker.core.overlays import draw_overlays
from maker.gui import theme as T
from maker.gui.widgets import Widget, cmd_down

CW_PX = CELL_WIDTH_TILES * TILE_SIZE
CH_PX = CELL_HEIGHT_TILES * TILE_SIZE

TOOLS = [
    # id, label, shortcut key, help
    ("select", "Select", "v", "click: select tile/cell/entity; drag an entity to move it"),
    ("paint", "Paint", "b", "drag: paint brush kind; right-drag: erase; Alt-click: pick"),
    ("rect", "Rectangle", "r", "drag: fill a rectangle (Shift = outline only)"),
    ("fill", "Fill", "f", "click: flood-fill the connected area inside the cell"),
    ("erase", "Erase", "x", "drag: set tiles to empty"),
    ("pick", "Eyedropper", "i", "click: take kind + palette from a tile"),
    ("entity", "Entity", "n", "click: place the chosen entity type; click one to select"),
    ("edge", "Edge/Door", "e", "click near a cell side: wall / open / door menu"),
]

DIRS = ("north", "south", "west", "east")
DELTA = {"north": (0, -1), "south": (0, 1), "west": (-1, 0), "east": (1, 0)}


def _line(a, b):
    """Integer points from a to b inclusive (Bresenham)."""
    (x0, y0), (x1, y1) = a, b
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    out = []
    while True:
        out.append((x0, y0))
        if (x0, y0) == (x1, y1):
            return out
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


class RoomView(Widget):
    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.model = app.model
        self.focus_cell: Optional[Tuple[int, int]] = None
        self.room = None
        self.room_surf: Optional[pygame.Surface] = None
        self._scaled = None
        self._scaled_key = None
        self.version = 0
        self.zoom = 0.5
        self.pan = [0.0, 0.0]
        self.lighting = True
        self.overlays = {"grid": True, "cells": True, "edges": True,
                         "entities": True, "kinds": False, "issues": True}
        self.tool = "paint"
        self._drag = None  # dict describing the active drag
        self._panning = None
        self._needs_render = True
        self.hover = None  # (wtx, wty) world tile under mouse

    # -- room / rendering ---------------------------------------------------

    def open(self, coord, fit=True) -> None:
        coord = tuple(coord)
        if not self.model.has_cell(coord):
            return
        self.focus_cell = coord
        self._rerender()
        if fit:
            self.fit()

    def _bbox(self):
        xs = [c[0] for c in self.room.cells]
        ys = [c[1] for c in self.room.cells]
        return min(xs), min(ys), max(xs), max(ys)

    def _rerender(self) -> None:
        self._needs_render = False
        if self.focus_cell is None or not self.model.has_cell(self.focus_cell):
            coords = self.model.cell_coords()
            if not coords:
                self.room = None
                self.room_surf = None
                return
            self.focus_cell = (self.model.start_cell
                               if self.model.start_cell in coords
                               else coords[0])
        self.room = self.model.assemble(self.focus_cell)
        minx, miny, maxx, maxy = self._bbox()
        surf = pygame.Surface(((maxx - minx + 1) * CW_PX * SCALE,
                               (maxy - miny + 1) * CH_PX * SCALE))
        surf.fill((12, 12, 16))
        for cell in self.room.cells:
            view = render.render_view(self.model, self.room, cell[0] * CW_PX,
                                      cell[1] * CH_PX, lighting=self.lighting)
            surf.blit(view, ((cell[0] - minx) * CW_PX * SCALE,
                             (cell[1] - miny) * CH_PX * SCALE))
        self.room_surf = surf
        self.version += 1

    def on_change(self, changes) -> None:
        """Model listener: re-render when the shown room may differ."""
        if self.room is None:
            self._needs_render = True
            return
        members = set(self.room.cells)
        touched = set(changes.cells)
        # a change to a neighbour can open/close an edge into this room
        near = {(c[0] + d[0], c[1] + d[1]) for c in members
                for d in DELTA.values()} | members
        if (touched & near) or changes.index or changes.zones \
                or changes.palettes:
            self._needs_render = True

    def refresh(self) -> None:
        self._needs_render = True

    def fit(self) -> None:
        if self.room_surf is None or self.rect.w <= 0:
            return
        sw, sh = self.room_surf.get_size()
        z = min((self.rect.w - 40) / sw, (self.rect.h - 40) / sh)
        self.zoom = max(0.1, min(1.5, z))
        self.pan = [(self.rect.w - sw * self.zoom) / 2,
                    (self.rect.h - sh * self.zoom) / 2]

    def set_zoom(self, z, anchor=None) -> None:
        z = max(0.1, min(1.5, z))
        if anchor is None:
            anchor = self.rect.center
        ax = anchor[0] - self.rect.x
        ay = anchor[1] - self.rect.y
        # keep the base px under the anchor fixed
        bx = (ax - self.pan[0]) / self.zoom
        by = (ay - self.pan[1]) / self.zoom
        self.zoom = z
        self.pan = [ax - bx * z, ay - by * z]

    # -- coordinate transforms ---------------------------------------------

    def origin_px(self):
        minx, miny, _, _ = self._bbox()
        return minx * CW_PX, miny * CH_PX

    def screen_to_world_px(self, pos):
        ox, oy = self.origin_px()
        s = SCALE * self.zoom
        return (ox + (pos[0] - self.rect.x - self.pan[0]) / s,
                oy + (pos[1] - self.rect.y - self.pan[1]) / s)

    def screen_to_tile(self, pos):
        wx, wy = self.screen_to_world_px(pos)
        return int(wx // TILE_SIZE), int(wy // TILE_SIZE)

    @staticmethod
    def split_tile(wt):
        cx, tx = divmod(wt[0], CELL_WIDTH_TILES)
        cy, ty = divmod(wt[1], CELL_HEIGHT_TILES)
        return (cx, cy), (tx, ty)

    def tile_in_room(self, wt) -> bool:
        if self.room is None:
            return False
        coord, _ = self.split_tile(wt)
        return coord in self.room.cells

    def cam(self):
        ox, oy = self.origin_px()
        s = SCALE * self.zoom
        return ox - self.pan[0] / s, oy - self.pan[1] / s

    def tile_rect(self, wt) -> pygame.Rect:
        cam_x, cam_y = self.cam()
        s = SCALE * self.zoom
        return pygame.Rect(
            round(self.rect.x + (wt[0] * TILE_SIZE - cam_x) * s),
            round(self.rect.y + (wt[1] * TILE_SIZE - cam_y) * s),
            round(TILE_SIZE * s), round(TILE_SIZE * s))

    def entity_at(self, wt):
        coord, (tx, ty) = self.split_tile(wt)
        data = self.model.cell(coord)
        if not data:
            return None
        for i, e in enumerate(data.get("entities", [])):
            if e.get("tile_x") == tx and e.get("tile_y") == ty:
                return coord, i
        return None

    def edge_at(self, pos):
        """(coord, direction) of the member-cell side nearest to `pos`
        within 2 tiles, else None."""
        wx, wy = self.screen_to_world_px(pos)
        coord = (int(wx // CW_PX), int(wy // CH_PX))
        if self.room is None or coord not in self.room.cells:
            return None
        lx = wx - coord[0] * CW_PX
        ly = wy - coord[1] * CH_PX
        dists = {"west": lx, "east": CW_PX - lx, "north": ly,
                 "south": CH_PX - ly}
        d = min(dists, key=dists.get)
        if dists[d] > 2 * TILE_SIZE:
            return None
        return coord, d

    # -- drawing -------------------------------------------------------------

    def draw(self, surface, ui) -> None:
        pygame.draw.rect(surface, (12, 12, 16), self.rect)
        if self._needs_render:
            try:
                self._rerender()
            except Exception as exc:  # keep the editor alive
                ui.error(f"render failed: {exc}")
                self.room = None
        if self.room_surf is None:
            T.text(surface, "No cells. Use the World Map to add one.",
                   (self.rect.x + 20, self.rect.y + 20), T.TEXT_DIM, 14)
            return
        key = (self.version, round(self.zoom, 4))
        if self._scaled_key != key:
            sw, sh = self.room_surf.get_size()
            size = (max(1, int(sw * self.zoom)), max(1, int(sh * self.zoom)))
            if self.zoom < 1:
                self._scaled = pygame.transform.smoothscale(self.room_surf, size)
            else:
                self._scaled = pygame.transform.scale(self.room_surf, size)
            self._scaled_key = key
        clip = surface.get_clip()
        surface.set_clip(self.rect)
        surface.blit(self._scaled, (self.rect.x + round(self.pan[0]),
                                    self.rect.y + round(self.pan[1])))
        view = surface.subsurface(self.rect)
        cam_x, cam_y = self.cam()
        which = [k for k in ("grid", "cells", "edges", "kinds", "entities")
                 if self.overlays.get(k)]
        issues = ui.issues if self.overlays.get("issues") else None
        try:
            draw_overlays(view, self.model, self.room, cam_x, cam_y,
                          zoom=self.zoom, which=which,
                          selection=ui.selection_overlay(), issues=issues,
                          font=T.font(11, True))
        except Exception as exc:
            ui.error(f"overlay failed: {exc}")
        self._draw_tool_preview(surface, ui)
        surface.set_clip(clip)
        # room caption
        cells = ", ".join(f"{c[0]},{c[1]}" for c in self.room.cells[:8])
        if len(self.room.cells) > 8:
            cells += ", …"
        cap = f"Room of {len(self.room.cells)} cell(s): {cells}   zone {self.room.zone}"
        pygame.draw.rect(surface, (0, 0, 0), (self.rect.x, self.rect.y,
                                              self.rect.w, 20))
        T.text(surface, cap, (self.rect.x + 8, self.rect.y + 3), T.TEXT_DIM,
               12, max_w=self.rect.w - 16)

    def _draw_tool_preview(self, surface, ui) -> None:
        if self.hover is None or not self.rect.collidepoint(ui.mouse):
            return
        drag = self._drag
        if drag and drag["kind"] == "rect":
            pts = self._rect_points(drag["start"], self.hover,
                                    drag.get("outline", False))
            color = T.KIND_COLORS.get(ui.brush_kind, (255, 255, 255))
            ov = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
            for p in pts:
                pygame.draw.rect(ov, (*color, 110), self.tile_rect(p))
            surface.blit(ov, (0, 0))
            a, b = drag["start"], self.hover
            T.text(surface, f"{abs(a[0] - b[0]) + 1} x {abs(a[1] - b[1]) + 1}",
                   (ui.mouse[0] + 14, ui.mouse[1] + 8), T.ACCENT, 12)
            return
        if self.tool == "edge":
            hit = self.edge_at(ui.mouse)
            if hit:
                coord, d = hit
                cam_x, cam_y = self.cam()
                s = SCALE * self.zoom
                x0 = self.rect.x + (coord[0] * CW_PX - cam_x) * s
                y0 = self.rect.y + (coord[1] * CH_PX - cam_y) * s
                w, h = CW_PX * s, CH_PX * s
                line = {"north": ((x0, y0), (x0 + w, y0)),
                        "south": ((x0, y0 + h), (x0 + w, y0 + h)),
                        "west": ((x0, y0), (x0, y0 + h)),
                        "east": ((x0 + w, y0), (x0 + w, y0 + h))}[d]
                pygame.draw.line(surface, T.ACCENT, line[0], line[1], 4)
            return
        if self.tile_in_room(self.hover):
            r = self.tile_rect(self.hover)
            color = {"erase": T.ERROR, "pick": T.INFO,
                     "select": (255, 255, 255)}.get(
                self.tool, T.KIND_COLORS.get(ui.brush_kind, T.ACCENT))
            pygame.draw.rect(surface, color, r, 2)

    # -- events --------------------------------------------------------------

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEMOTION:
            if self._panning is not None:
                sx, sy, px, py = self._panning
                self.pan = [px + ev.pos[0] - sx, py + ev.pos[1] - sy]
                return True
            if self.rect.collidepoint(ev.pos) and self.room is not None:
                self.hover = self.screen_to_tile(ev.pos)
                ui.set_hover_info(self.hover_info())
                if self._drag:
                    self._drag_move(ui)
                return True
            if self._drag:
                return True
            return False
        if ev.type == pygame.MOUSEWHEEL:
            if not self.rect.collidepoint(ui.mouse):
                return False
            if cmd_down(ui.mods) or ev.y and not ev.x and ui.wheel_zooms:
                self.set_zoom(self.zoom * (1.15 ** ev.y), ui.mouse)
            else:
                self.pan[0] += ev.x * 30
                self.pan[1] += ev.y * 30
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN:
            if not self.rect.collidepoint(ev.pos):
                return False
            ui.set_focus(None)
            if ev.button == 2 or (ev.button == 1 and ui.space_down):
                self._panning = (ev.pos[0], ev.pos[1], self.pan[0], self.pan[1])
                return True
            if self.room is None or ev.button not in (1, 3):
                return True
            self.hover = self.screen_to_tile(ev.pos)
            self._press(ev, ui)
            return True
        if ev.type == pygame.MOUSEBUTTONUP:
            if self._panning is not None and ev.button in (1, 2):
                self._panning = None
                return True
            if self._drag:
                self._release(ui)
                return True
        return False

    def hover_info(self) -> str:
        if self.hover is None:
            return ""
        coord, (tx, ty) = self.split_tile(self.hover)
        data = self.model.cell(coord)
        if data is None or coord not in self.room.cells:
            return f"world tile {self.hover[0]},{self.hover[1]} (outside room)"
        tile = data["tiles"][ty][tx]
        pal = tile.get("palette") or "-"
        ent = self.entity_at(self.hover)
        etxt = ""
        if ent:
            etxt = "  entity: " + data["entities"][ent[1]].get("type", "?")
        return (f"cell {coord[0]},{coord[1]}  tile {tx},{ty}  "
                f"{tile.get('kind', 'empty')} [{pal}]{etxt}")

    # -- tool logic ------------------------------------------------------------

    def _press(self, ev, ui) -> None:
        wt = self.hover
        tool = self.tool
        alt = bool(ui.mods & pygame.KMOD_ALT)
        if tool in ("paint", "erase", "rect", "fill") and alt:
            tool = "pick"
        if ev.button == 3 and tool in ("select", "entity"):
            self._context_menu(ui, wt)
            return
        if tool == "pick":
            self._pick(ui, wt)
        elif tool in ("paint", "erase"):
            kind = "empty" if (tool == "erase" or ev.button == 3) \
                else ui.brush_kind
            self._drag = {"kind": "paint", "mark": self.model.history_mark(),
                          "last": wt, "brush": kind, "painted": set()}
            self._paint_points(ui, [wt])
        elif tool == "rect":
            self._drag = {"kind": "rect", "start": wt,
                          "outline": bool(ui.mods & pygame.KMOD_SHIFT),
                          "brush": "empty" if ev.button == 3 else ui.brush_kind}
        elif tool == "fill":
            if not self.tile_in_room(wt):
                return
            coord, (tx, ty) = self.split_tile(wt)
            kind = "empty" if ev.button == 3 else ui.brush_kind
            ui.run(ops.flood_fill, coord, tx, ty, kind, ui.brush_palette,
                   label="fill")
        elif tool == "entity":
            hit = self.entity_at(wt)
            if hit:
                ui.select_entity(*hit)
                self._drag = {"kind": "move_entity", "ent": hit,
                              "start": wt}
            elif self.tile_in_room(wt):
                coord, (tx, ty) = self.split_tile(wt)
                res = ui.run(ops.add_entity, coord, ui.entity_type, tx, ty,
                             label=f"add {ui.entity_type}")
                if res is not None:
                    ui.select_entity(coord, res.get("index", len(
                        self.model.cell(coord)["entities"]) - 1))
        elif tool == "select":
            hit = self.entity_at(wt)
            if hit:
                ui.select_entity(*hit)
                self._drag = {"kind": "move_entity", "ent": hit, "start": wt}
            elif self.tile_in_room(wt):
                coord, (tx, ty) = self.split_tile(wt)
                ui.select_tile(coord, (tx, ty))
        elif tool == "edge":
            hit = self.edge_at(ui.mouse)
            if hit:
                self.app.edge_menu(hit[0], hit[1], ui.mouse)

    def _context_menu(self, ui, wt) -> None:
        items = []
        hit = self.entity_at(wt)
        if hit:
            items.append(("Delete entity", lambda: ui.delete_entity(*hit)))
            items.append(None)
        if self.tile_in_room(wt):
            coord, (tx, ty) = self.split_tile(wt)
            items.append((f"Playtest from tile {tx},{ty}",
                          lambda: ui.playtest(coord, (tx, ty))))
            items.append((f"Add {ui.entity_type} here", lambda: ui.run(
                ops.add_entity, coord, ui.entity_type, tx, ty)))
            items.append(("Set as start cell", lambda: ui.run(
                ops.set_start_cell, coord)))
        if items:
            ui.open_menu(items, ui.mouse)

    def _pick(self, ui, wt) -> None:
        if not self.tile_in_room(wt):
            return
        coord, (tx, ty) = self.split_tile(wt)
        tile = self.model.cell(coord)["tiles"][ty][tx]
        ui.brush_kind = tile.get("kind", "empty")
        pal = tile.get("palette") or None
        ui.brush_palette = pal
        ui.status(f"picked {ui.brush_kind} [{pal or 'auto'}]")
        if self.tool == "pick":
            self.tool = "paint"

    def _paint_points(self, ui, pts) -> None:
        d = self._drag
        pts = [p for p in pts if p not in d["painted"] and self.tile_in_room(p)]
        if not pts:
            return
        d["painted"].update(pts)
        palette = None if d["brush"] == "empty" else ui.brush_palette
        ui.run(ops.paint_world, [list(p) for p in pts], d["brush"], palette,
               label="paint", quiet=True)

    @staticmethod
    def _rect_points(a, b, outline=False):
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        pts = []
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                if outline and x0 < x < x1 and y0 < y < y1:
                    continue
                pts.append((x, y))
        return pts

    def _drag_move(self, ui) -> None:
        d = self._drag
        if d["kind"] == "paint" and self.hover != d["last"]:
            self._paint_points(ui, _line(d["last"], self.hover))
            d["last"] = self.hover

    def _release(self, ui) -> None:
        d, self._drag = self._drag, None
        if d["kind"] == "paint":
            self.model.coalesce_history(d["mark"], f"paint {d['brush']}")
            ui.after_edit()
        elif d["kind"] == "rect":
            pts = [p for p in self._rect_points(d["start"], self.hover,
                                                d["outline"])
                   if self.tile_in_room(p)]
            if pts:
                palette = None if d["brush"] == "empty" else ui.brush_palette
                ui.run(ops.paint_world, [list(p) for p in pts], d["brush"],
                       palette, label=f"rect {d['brush']}")
        elif d["kind"] == "move_entity":
            if self.hover != d["start"] and self.tile_in_room(self.hover):
                coord, idx = d["ent"]
                to_coord, (tx, ty) = self.split_tile(self.hover)
                res = ui.run(ops.move_entity, coord, idx, tx, ty,
                             to_coord if to_coord != coord else None,
                             label="move entity")
                if res is not None:
                    new_idx = res.get("index", idx) if isinstance(res, dict) \
                        else idx
                    ui.select_entity(to_coord, new_idx)
