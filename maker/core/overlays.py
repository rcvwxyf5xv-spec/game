"""Overlay drawing shared by the maker CLI renders and the GUI.

draw_overlays paints editor annotations on top of a rendered view where
screen px = (world_game_px - cam) * SCALE * zoom: a tile grid, cell
borders + labels, edge/door markers, per-kind collision fills, entity
boxes, selection outlines and validation issue outlines. Everything is
drawn into a temporary SRCALPHA overlay and blitted once.
"""

from __future__ import annotations

from typing import Optional

import pygame

from src.engine.constants import (
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    TILE_SIZE,
)
from src.engine.room_assembler import room_origin_px
from src.main import SCALE, draw_dashed_line

# tile kind (engine int ids) -> translucent collision-view colour
_TILE_KIND_NAMES = {1: "solid", 2: "spring", 3: "oneway", 4: "crumble"}
_KIND_FILLS = {
    "solid": (110, 110, 122, 70),
    "spring": (70, 230, 120, 60),
    "oneway": (90, 160, 255, 55),
    "crumble": (255, 150, 70, 60),
}
_CELL_LABEL = (255, 240, 200)
_EDGE_OPEN = (0, 255, 120, 70)
_EDGE_DOOR = (255, 220, 80, 180)
_EDGE_DOOR_EDGE = (80, 220, 255, 210)
_EDGE_WALL = (120, 40, 40, 180)
_GRID = (255, 255, 255, 26)

_FONTS = {}


def _font(size: int = 11):
    """Cached monospace font (default pygame.font.SysFont("monospace",
    11))."""
    if size not in _FONTS:
        _FONTS[size] = pygame.font.SysFont("monospace", size)
    return _FONTS[size]


def _overlay(surface) -> pygame.Surface:
    return pygame.Surface(surface.get_size(), pygame.SRCALPHA)


def _scale(zoom: float) -> float:
    return SCALE * zoom


def _px(x: float, cam, zoom: float) -> float:
    return (x - cam) * _scale(zoom)


def _rect(wx, wy, ww, wh, cam_x, cam_y, zoom):
    return (round(_px(wx, cam_x, zoom)), round(_px(wy, cam_y, zoom)),
            round(ww * _scale(zoom)), round(wh * _scale(zoom)))


def _draw_grid(surface, room, cam_x, cam_y, zoom):
    overlay = _overlay(surface)
    ox, oy = room_origin_px(room)
    sx = _px(ox, cam_x, zoom)
    sy = _px(oy, cam_y, zoom)
    step = TILE_SIZE * _scale(zoom)
    for tx in range(room.width + 1):
        x = sx + tx * step
        pygame.draw.line(overlay, _GRID, (x, sy), (x, sy + room.height * step))
    for ty in range(room.height + 1):
        y = sy + ty * step
        pygame.draw.line(overlay, _GRID, (sx, y), (sx + room.width * step, y))
    surface.blit(overlay, (0, 0))


def _draw_cells(surface, model, room, cam_x, cam_y, zoom, font):
    overlay = _overlay(surface)
    cw = CELL_WIDTH_TILES * TILE_SIZE
    ch = CELL_HEIGHT_TILES * TILE_SIZE
    for coord in room.cells:
        wx = coord[0] * cw
        wy = coord[1] * ch
        x0 = _px(wx, cam_x, zoom)
        y0 = _px(wy, cam_y, zoom)
        x1 = _px(wx + cw, cam_x, zoom)
        y1 = _px(wy + ch, cam_y, zoom)
        color = (*_CELL_LABEL, 90)
        draw_dashed_line(overlay, (x0, y0), (x1, y0), color, 8)
        draw_dashed_line(overlay, (x1, y0), (x1, y1), color, 8)
        draw_dashed_line(overlay, (x1, y1), (x0, y1), color, 8)
        draw_dashed_line(overlay, (x0, y1), (x0, y0), color, 8)
        label = font.render(f"{coord[0]},{coord[1]}", True, _CELL_LABEL)
        overlay.blit(label, (x0 + 8, y0 + 8))
    surface.blit(overlay, (0, 0))


def _draw_edges(surface, model, room, cam_x, cam_y, zoom, font):
    overlay = _overlay(surface)
    cw = CELL_WIDTH_TILES * TILE_SIZE
    ch = CELL_HEIGHT_TILES * TILE_SIZE
    members = set(room.cells)
    s = max(4, int(10 * _scale(zoom)))

    def strip(direction, x0, y0, x1, y1):
        if direction == "north":
            return (x0, y0, x1 - x0, s)
        if direction == "south":
            return (x0, y1 - s, x1 - x0, s)
        if direction == "west":
            return (x0, y0, s, y1 - y0)
        return (x1 - s, y0, s, y1 - y0)

    for coord in room.cells:
        data = model.cell(coord)
        edges = data.get("edges", {})
        wx = coord[0] * cw
        wy = coord[1] * ch
        for direction, (ddx, ddy) in (
                ("north", (0, -1)), ("south", (0, 1)),
                ("west", (-1, 0)), ("east", (1, 0))):
            edge = edges.get(direction, {"type": "wall"})
            etype = edge.get("type", "wall")
            x0 = _px(wx, cam_x, zoom)
            y0 = _px(wy, cam_y, zoom)
            x1 = _px(wx + cw, cam_x, zoom)
            y1 = _px(wy + ch, cam_y, zoom)
            if etype == "open":
                pygame.draw.rect(overlay, _EDGE_OPEN,
                                 strip(direction, x0, y0, x1, y1))
            elif etype == "door":
                pygame.draw.rect(overlay, _EDGE_DOOR,
                                 strip(direction, x0, y0, x1, y1))
                pygame.draw.rect(overlay, _EDGE_DOOR_EDGE,
                                 strip(direction, x0, y0, x1, y1), 1)
                text = (f"{edge.get('door_id', '?')} -> "
                        f"{tuple(edge.get('target_cell') or ())} "
                        f"{edge.get('target_edge', '')}")
                label = font.render(text.strip(), True, (30, 30, 30))
                ex, ey, ew, eh = strip(direction, x0, y0, x1, y1)
                lw, lh = label.get_size()
                lx = ex + ew / 2 - lw / 2
                ly = ey + eh / 2 - lh / 2
                # keep the label inside the cell next to its strip
                if direction == "west":
                    lx = ex + 2
                elif direction == "east":
                    lx = ex + ew - lw - 2
                elif direction == "south":
                    ly = ey + eh - lh - 1
                else:
                    ly = ey + 1
                box = pygame.Rect(int(lx) - 3, int(ly) - 1, lw + 6, lh + 2)
                pygame.draw.rect(overlay, (*_EDGE_DOOR[:3], 230), box)
                overlay.blit(label, box.move(3, 1).topleft)
            else:
                nbr = (coord[0] + ddx, coord[1] + ddy)
                if nbr in members:
                    continue
                pygame.draw.line(overlay, _EDGE_WALL, *_wall_line(
                    x0, y0, x1, y1, direction), 3)
    surface.blit(overlay, (0, 0))


def _wall_line(x0, y0, x1, y1, direction):
    if direction == "north":
        return (x0, y0), (x1, y0)
    if direction == "south":
        return (x0, y1), (x1, y1)
    if direction == "west":
        return (x0, y0), (x0, y1)
    return (x1, y0), (x1, y1)


def _draw_kinds(surface, room, cam_x, cam_y, zoom):
    overlay = _overlay(surface)
    ox, oy = room_origin_px(room)
    for ty in range(room.height):
        for tx in range(room.width):
            name = _TILE_KIND_NAMES.get(room.tiles[ty][tx])
            if name is None:
                continue
            wx = ox + tx * TILE_SIZE
            wy = oy + ty * TILE_SIZE
            pygame.draw.rect(overlay, _KIND_FILLS[name],
                             _rect(wx, wy, TILE_SIZE, TILE_SIZE,
                                   cam_x, cam_y, zoom))
    surface.blit(overlay, (0, 0))


def _draw_entities(surface, room, cam_x, cam_y, zoom, font):
    overlay = _overlay(surface)
    ox, oy = room_origin_px(room)
    for entity in room.entities:
        etype = entity.get("type", "")
        tx = entity.get("tile_x", 0)
        ty = entity.get("tile_y", 0)
        wx = ox + tx * TILE_SIZE
        wy = oy + ty * TILE_SIZE
        rect = _rect(wx, wy, TILE_SIZE, TILE_SIZE, cam_x, cam_y, zoom)
        if etype == "spawn_point":
            pygame.draw.rect(overlay, (70, 255, 120, 210), rect, 2)
            pygame.draw.circle(overlay, (70, 255, 120, 230),
                               (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2),
                               max(3, rect[2] // 4))
            label = font.render("SPAWN", True, (70, 255, 120))
        elif etype in ("light", "ambient_light"):
            pygame.draw.rect(overlay, (255, 230, 120, 210), rect, 2)
            if etype == "light":
                try:
                    radius = float(entity.get("radius", 48))
                except (TypeError, ValueError):
                    radius = 48.0
                pygame.draw.circle(overlay, (255, 230, 120, 140),
                                   (rect[0] + rect[2] // 2,
                                    rect[1] + rect[3] // 2),
                                   max(4, int(radius * SCALE * zoom)), 1)
            label = font.render(etype, True, (255, 235, 160))
        else:
            pygame.draw.rect(overlay, (255, 130, 90, 200), rect, 2)
            label = font.render(etype, True, (255, 205, 170))
        overlay.blit(label, (rect[0], rect[1] - label.get_height() - 2))
    surface.blit(overlay, (0, 0))


def _draw_selection(surface, model, room, cam_x, cam_y, zoom, selection):
    overlay = _overlay(surface)
    cw = CELL_WIDTH_TILES * TILE_SIZE
    ch = CELL_HEIGHT_TILES * TILE_SIZE
    members = set(room.cells)
    if "tiles" in selection:
        for wtx, wty in selection["tiles"]:
            cx, tx = divmod(int(wtx), CELL_WIDTH_TILES)
            cy, ty = divmod(int(wty), CELL_HEIGHT_TILES)
            if (cx, cy) not in members:
                continue
            wx = cx * cw + tx * TILE_SIZE
            wy = cy * ch + ty * TILE_SIZE
            pygame.draw.rect(overlay, (255, 255, 255, 240),
                             _rect(wx, wy, TILE_SIZE, TILE_SIZE,
                                   cam_x, cam_y, zoom), 1)
    if "cells" in selection:
        for coord in selection["cells"]:
            wx = coord[0] * cw
            wy = coord[1] * ch
            pygame.draw.rect(overlay, (255, 255, 255, 200),
                             _rect(wx, wy, cw, ch, cam_x, cam_y, zoom), 2)
    if "entity" in selection:
        coord, index = selection["entity"]
        data = model.cell(coord)
        ents = data.get("entities", []) if data else []
        if 0 <= index < len(ents):
            e = ents[index]
            wx = coord[0] * cw + e["tile_x"] * TILE_SIZE
            wy = coord[1] * ch + e["tile_y"] * TILE_SIZE
            pygame.draw.rect(overlay, (255, 255, 255, 250),
                             _rect(wx, wy, TILE_SIZE, TILE_SIZE,
                                   cam_x, cam_y, zoom), 2)
    surface.blit(overlay, (0, 0))


def _draw_issues(surface, model, room, cam_x, cam_y, zoom, issues):
    overlay = _overlay(surface)
    cw = CELL_WIDTH_TILES * TILE_SIZE
    ch = CELL_HEIGHT_TILES * TILE_SIZE
    for issue in issues:
        coord = issue.cell
        if coord is None or not model.has_cell(coord):
            continue
        if issue.severity == "error":
            color = (255, 80, 70, 230)
        elif issue.severity == "warning":
            color = (255, 230, 80, 210)
        else:
            color = (180, 180, 220, 170)
        wx = coord[0] * cw
        wy = coord[1] * ch
        pygame.draw.rect(overlay, color,
                         _rect(wx, wy, cw, ch, cam_x, cam_y, zoom), 2)
    surface.blit(overlay, (0, 0))


def draw_overlays(surface, model, room, cam_x, cam_y, zoom=1.0,
                  which=("grid", "cells", "edges", "entities", "kinds"),
                  selection=None, issues=None, font=None):
    """Draw editor overlays on `surface` (already showing the room).

    Screen px = (world_game_px - cam_x) * SCALE * zoom. `selection` is a
    dict like {"tiles": world tile coords} / {"entity": (coord, index)} /
    {"cells": coords}; `issues` is a list of validate.Issue.
    """
    font = font or _font()
    which = list(which or ())
    if "grid" in which:
        _draw_grid(surface, room, cam_x, cam_y, zoom)
    if "cells" in which:
        _draw_cells(surface, model, room, cam_x, cam_y, zoom, font)
    if "edges" in which:
        _draw_edges(surface, model, room, cam_x, cam_y, zoom, font)
    if "kinds" in which:
        _draw_kinds(surface, room, cam_x, cam_y, zoom)
    if "entities" in which:
        _draw_entities(surface, room, cam_x, cam_y, zoom, font)
    if selection is not None:
        _draw_selection(surface, model, room, cam_x, cam_y, zoom, selection)
    if issues is not None:
        _draw_issues(surface, model, room, cam_x, cam_y, zoom, issues)
