"""Rendering for the maker UI and CLI.

Headless-safe pygame rendering of a project: activate() points the game's
renderer (src.main.VoxelRenderer) at a ProjectModel's data, then
render_view / render_room / render_cell / render_world_map produce PNGs
for editors, agents and the CLI. All entry points are idempotent about
pygame initialisation and never touch an existing display (the GUI calls
pygame itself first).

Screens are SCALE-fold: one game px = 3 screen px, a window-sized view is
WINDOW_W x WINDOW_H (768x480 = exactly one visible cell at a time).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from src.engine.constants import (  # noqa: E402
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    TILE_SIZE,
)
from src.engine.room_assembler import AssembledRoom, room_origin_px  # noqa: E402
from src.gfx.palette import get_palette  # noqa: E402
from src.main import SCALE, WINDOW_H, WINDOW_W, VoxelRenderer  # noqa: E402
from src.project import active, invalidate, set_active  # noqa: E402

from maker.core.overlays import draw_overlays  # noqa: E402

CELL_WIDTH_PX = CELL_WIDTH_TILES * TILE_SIZE
CELL_HEIGHT_PX = CELL_HEIGHT_TILES * TILE_SIZE

_RENDERER = None


def ensure_pygame(headless: bool = True) -> None:
    """Initialise pygame for headless drawing if needed; idempotent.

    Sets SDL_VIDEODRIVER=dummy only when `headless`, the variable is not
    already set and pygame is not initialised; pygame.init() runs once;
    a tiny display mode is created only when none exists (convert_alpha
    needs one). An existing display is never replaced.
    """
    if not pygame.get_init():
        if headless and "SDL_VIDEODRIVER" not in os.environ:
            os.environ["SDL_VIDEODRIVER"] = "dummy"
        pygame.init()
    if pygame.display.get_surface() is None:
        pygame.display.set_mode((1, 1))


def activate(model) -> None:
    """Make the renderer see this model's project and (unsaved) zones.

    Call before rendering: switches the process-active project when the
    root differs (dropping stale render caches) and pushes the model's
    zones as the zone override so palettes reflect unsaved edits.
    """
    if active().root != model.project.root:
        set_active(model.project)
        reset_caches()
    model.set_zone_preview()


def reset_caches() -> None:
    """Drop every render cache (zones, src.gfx caches) AND the shared
    VoxelRenderer, which holds its own texture registry: call after
    textures/palettes/zones change on disk so the next render reloads."""
    global _RENDERER
    invalidate()
    _RENDERER = None


def _get_renderer() -> VoxelRenderer:
    """Module-level shared VoxelRenderer (created after activate())."""
    global _RENDERER
    if _RENDERER is None:
        _RENDERER = VoxelRenderer()
    return _RENDERER


def render_view(model, room, cam_x, cam_y, tick=0, lighting=True,
                entities=True, backdrop=True, out=None) -> pygame.Surface:
    """One window-sized (768x480) view of `room` at camera (world game px,
    like render_frame), reusing the game's VoxelRenderer. Pass `out` to
    draw onto an existing surface."""
    ensure_pygame(headless=True)
    activate(model)
    renderer = _get_renderer()
    if out is None:
        out = pygame.Surface((WINDOW_W, WINDOW_H))
    renderer.render_static(out, room, cam_x, cam_y, tick=tick,
                           lighting=lighting, entities=entities,
                           backdrop=backdrop)
    return out


def render_room(model, coord, scale=1.0, lighting=True, entities=True,
                overlays=()) -> pygame.Surface:
    """The WHOLE room containing `coord`: one 768x480 view per member cell
    of the room's bounding box, blitted into a big surface (bbox slots
    without a room cell are filled (12,12,16)), then overlays, then
    optionally smoothscaled by `scale`."""
    ensure_pygame(headless=True)
    activate(model)
    room = model.assemble(coord)
    xs = [c[0] for c in room.cells]
    ys = [c[1] for c in room.cells]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    w = (maxx - minx + 1) * CELL_WIDTH_TILES * TILE_SIZE * SCALE
    h = (maxy - miny + 1) * CELL_HEIGHT_TILES * TILE_SIZE * SCALE
    surf = pygame.Surface((w, h))
    surf.fill((12, 12, 16))
    for cell in room.cells:
        lx = cell[0] - minx
        ly = cell[1] - miny
        cam_x = cell[0] * CELL_WIDTH_PX
        cam_y = cell[1] * CELL_HEIGHT_PX
        view = render_view(model, room, cam_x, cam_y,
                           lighting=lighting, entities=entities)
        surf.blit(view, (lx * WINDOW_W, ly * WINDOW_H))
    if overlays:
        draw_overlays(surf, model, room, minx * CELL_WIDTH_PX,
                      miny * CELL_HEIGHT_PX, zoom=1.0, which=overlays)
    if scale != 1.0:
        surf = pygame.transform.smoothscale(
            surf, (max(1, int(surf.get_width() * scale)),
                   max(1, int(surf.get_height() * scale))))
    return surf


def render_cell(model, coord, lighting=True, entities=True, backdrop=True,
                overlays=()) -> pygame.Surface:
    """A single window-sized (768x480) view of cell `coord` (camera at its
    world px origin, within its room), with optional overlays."""
    room = model.assemble(coord)
    cx, cy = coord
    out = render_view(model, room, cx * CELL_WIDTH_PX, cy * CELL_HEIGHT_PX,
                      lighting=lighting, entities=entities, backdrop=backdrop)
    if overlays:
        draw_overlays(out, model, room, cx * CELL_WIDTH_PX,
                      cy * CELL_HEIGHT_PX, zoom=1.0, which=overlays)
    return out


def world_map_layout(model, px_per_cell=24):
    """(origin offset, size) for the schematic world map.

    Grid cell (cx, cy) occupies screen rect
    (off[0] + (cx - minx) * px_per_cell, off[1] + (cy - miny) * px_per_cell,
    px_per_cell, px_per_cell), so the GUI can map clicks back to cells.
    """
    cells = model.cell_coords()
    if not cells:
        return (4, 4), (px_per_cell * 4, px_per_cell * 4)
    xs = [c[0] for c in cells]
    ys = [c[1] for c in cells]
    minx, miny, maxx, maxy = min(xs), min(ys), max(xs), max(ys)
    margin = 4
    off = (margin, margin)
    size = ((maxx - minx + 1) * px_per_cell + 2 * margin,
            (maxy - miny + 1) * px_per_cell + 2 * margin)
    return off, size


def render_world_map(model, px_per_cell=24, highlight=None,
                     show_doors=True) -> pygame.Surface:
    """Schematic world map: each cell a rectangle coloured by its zone
    accent (darkened); non-open edges draw the room borders thicker; doors
    are small bright marks on the edge; the start cell is marked;
    `highlight` (set of coords) is outlined white."""
    ensure_pygame(headless=True)
    activate(model)
    cells = model.cell_coords()
    origin, (w, h) = world_map_layout(model, px_per_cell)
    surf = pygame.Surface((w, h))
    surf.fill((10, 10, 14))
    if not cells:
        return surf
    off_x, off_y = origin
    minx = min(c[0] for c in cells)
    miny = min(c[1] for c in cells)
    ppc = px_per_cell
    for cx, cy in cells:
        zone = model.cell((cx, cy)).get("zone", "")
        accent = get_palette(zone)["accent"]
        dark = tuple(max(0, int(v * 0.6)) for v in accent)
        x = off_x + (cx - minx) * ppc
        y = off_y + (cy - miny) * ppc
        pygame.draw.rect(surf, dark, (x + 1, y + 1, ppc - 2, ppc - 2))
    sides = {
        "north": (lambda x0, y0, x1, y1: ((x0, y0), (x1, y0))),
        "south": (lambda x0, y0, x1, y1: ((x0, y1), (x1, y1))),
        "west": (lambda x0, y0, x1, y1: ((x0, y0), (x0, y1))),
        "east": (lambda x0, y0, x1, y1: ((x1, y0), (x1, y1))),
    }
    for cx, cy in cells:
        data = model.cell((cx, cy))
        x0 = off_x + (cx - minx) * ppc
        y0 = off_y + (cy - miny) * ppc
        x1 = x0 + ppc - 1
        y1 = y0 + ppc - 1
        for direction, endpoints in sides.items():
            edge = data.get("edges", {}).get(direction)
            if edge and edge.get("type") == "open":
                continue
            (ex0, ey0), (ex1, ey1) = endpoints(x0, y0, x1, y1)
            pygame.draw.line(surf, (60, 60, 82), (ex0, ey0), (ex1, ey1), 2)
            if show_doors and edge and edge.get("type") == "door":
                mx = (ex0 + ex1) // 2
                my = (ey0 + ey1) // 2
                pygame.draw.circle(surf, (255, 230, 120), (mx, my), 2)
    start = model.start_cell
    if start is not None and start in [(cx, cy) for cx, cy in cells]:
        sx = off_x + (start[0] - minx) * ppc + ppc // 2
        sy = off_y + (start[1] - miny) * ppc + ppc // 2
        pygame.draw.circle(surf, (255, 255, 255), (sx, sy), max(2, ppc // 5))
    highlight = highlight or ()
    for cx, cy in highlight:
        x = off_x + (cx - minx) * ppc
        y = off_y + (cy - miny) * ppc
        pygame.draw.rect(surf, (255, 255, 255), (x, y, ppc, ppc), 2)
    return surf


def save_png(surface, path) -> None:
    """Write a pygame Surface to a PNG file (parent dirs created)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(surface, str(path))
