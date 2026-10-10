"""
Entry point / demo harness - late-90s / early-2000s PC voxel-art look.

Everything on screen is generated procedurally from the
src/gfx package: per-zone palettes, voxel rock relief tiles, voxel
sprites, parallax cave backdrops, particles and a minimap. Lighting is
authored only (light / ambient_light entities placed in the editor).
Tile textures are pre-built into shared sheets under textures/
of the active project (see tools/build_textures.py) and loaded at runtime by the texture
registry; no other image assets are used.

Run with:  python -m src.main [--project DIR]
Controls:  left/right arrows or A/D to move, space/up to jump,
           G to toggle the debug grid overlay (default OFF),
           H to toggle the debug info window (default OFF),
           Esc to quit.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pygame

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engine.constants import (  # noqa: E402
    CELL_HEIGHT_TILES,
    CELL_WIDTH_TILES,
    CRUMBLE_DELAY_FRAMES,
    FPS,
    JUMP_SPEED,
    MOVE_SPEED,
    TILE_CRUMBLE,
    TILE_EMPTY,
    TILE_ONEWAY_PLAT,
    TILE_SIZE,
    TILE_SOLID,
    TILE_SPRING,
)
from src.engine.game_world import PLAYER_H, PLAYER_W, GameWorld, TransitionEvent  # noqa: E402
from src.engine.physics import CrumbleTracker  # noqa: E402
from src.engine.room_assembler import AssembledRoom, room_origin_px  # noqa: E402
from src.engine.world_loader import WorldLoader  # noqa: E402
from src.debug_window import DebugWindow, debug_lines  # noqa: E402
from src.gfx.backdrop import get_backdrop_layers, get_fog_gradient  # noqa: E402
from src.gfx.effects import LightMap, ParticleSystem  # noqa: E402
from src.gfx.palette import get_palette  # noqa: E402
from src.gfx.sprites import get_item_frames  # noqa: E402
from src.gfx.sprite_registry import default_sprites  # noqa: E402
from src.gfx.texture_registry import default_registry  # noqa: E402
from src.project import Project, active, set_active  # noqa: E402

SCALE = 3
WINDOW_W = CELL_WIDTH_TILES * TILE_SIZE * SCALE
WINDOW_H = CELL_HEIGHT_TILES * TILE_SIZE * SCALE

GRID_LINE_COLOR = (255, 255, 255)

ROCK_VARIANTS = 4

# Deep rock (distance from air in tiles) darkens by this factor, so
# tunnel interiors read as near-black masses.
DEPTH_BRIGHTNESS = (1.0, 0.7, 0.45, 0.25)

_TILE_KIND = {TILE_SOLID: "solid", TILE_SPRING: "spring",
              TILE_ONEWAY_PLAT: "oneway", TILE_CRUMBLE: "crumble"}


def camera_for_point(room: AssembledRoom, local_cx: float, local_cy: float):
    """World-space camera (top-left, in game px) centered on a point that
    is `room`-local, clamped to that room's bounds. Returns WORLD-space
    (not room-local) so a transition can slide the camera across two
    rooms' tile grids in one consistent shared coordinate space."""
    room_ox, room_oy = room_origin_px(room)
    room_w_px = room.width * TILE_SIZE
    room_h_px = room.height * TILE_SIZE
    view_w = WINDOW_W / SCALE
    view_h = WINDOW_H / SCALE
    cam_x = local_cx - view_w / 2
    cam_y = local_cy - view_h / 2
    cam_x = max(0.0, min(cam_x, max(0.0, room_w_px - view_w)))
    cam_y = max(0.0, min(cam_y, max(0.0, room_h_px - view_h)))
    return room_ox + cam_x, room_oy + cam_y


def compute_camera(world: GameWorld):
    center_x = world.body.x + world.body.width / 2
    center_y = world.body.y + world.body.height / 2
    return camera_for_point(world.current_room, center_x, center_y)


def _round_cam(cam_x: float, cam_y: float):
    """Round the camera to whole window px so parallax/tiles never shimmer."""
    return (
        round(cam_x * SCALE) / SCALE,
        round(cam_y * SCALE) / SCALE,
    )


def draw_dashed_line(surface, start, end, color, dash_len=6):
    x1, y1 = start
    x2, y2 = end
    length = max(abs(x2 - x1), abs(y2 - y1))
    if length == 0:
        return
    steps = int(length // dash_len)
    for i in range(0, steps, 2):
        t0 = i / steps
        t1 = min((i + 1) / steps, 1.0)
        p0 = (x1 + (x2 - x1) * t0, y1 + (y2 - y1) * t0)
        p1 = (x1 + (x2 - x1) * t1, y1 + (y2 - y1) * t1)
        pygame.draw.line(surface, color, p0, p1, 1)


# ---------------------------------------------------------------------------
# Tile classification helpers
# ---------------------------------------------------------------------------

_AIR_LIKE = (TILE_EMPTY, TILE_ONEWAY_PLAT, TILE_SPRING)


def air_depth_map(room: AssembledRoom):
    """Per-room BFS distance to the nearest air tile, capped at 4. Solid
    rock at distance d multiplies its brightness by DEPTH_BRIGHTNESS[d]."""
    h = len(room.tiles)
    w = len(room.tiles[0])
    dist = [[4] * w for _ in range(h)]
    from collections import deque

    queue = deque()
    for y in range(h):
        for x in range(w):
            if room.tiles[y][x] in _AIR_LIKE:
                dist[y][x] = 0
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        d = dist[y][x] + 1
        if d > 3:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and dist[ny][nx] > d \
                    and room.tiles[ny][nx] == TILE_SOLID:
                dist[ny][nx] = d
                queue.append((nx, ny))
    return dist


def tile_mask(room: AssembledRoom, tx: int, ty: int) -> int:
    """4-bit SOLID-like neighbor mask (N=1,E=2,S=4,W=8). Out-of-bounds
    counts as solid; TILE_SOLID/TILE_CRUMBLE/TILE_SPRING count as solid."""
    w = room.width
    h = room.height
    mask = 0

    def _solid(x, y):
        if x < 0 or x >= w or y < 0 or y >= h:
            return True
        return room.tiles[y][x] in (TILE_SOLID, TILE_CRUMBLE, TILE_SPRING)

    if _solid(tx, ty - 1):
        mask |= 1
    if _solid(tx + 1, ty):
        mask |= 2
    if _solid(tx, ty + 1):
        mask |= 4
    if _solid(tx - 1, ty):
        mask |= 8
    return mask


def variant_for(tx: int, ty: int, ox: int, oy: int) -> int:
    """Deterministic variant from the absolute world tile position."""
    wx = ox * CELL_WIDTH_TILES + tx
    wy = oy * CELL_HEIGHT_TILES + ty
    v = (wx * 73856093) ^ (wy * 19349663)
    return abs(v) % ROCK_VARIANTS


# ---------------------------------------------------------------------------
# VoxelRenderer: draws a full frame with the gfx package
# ---------------------------------------------------------------------------


_INVISIBLE_ENTITIES = ("spawn_point", "light", "ambient_light")


def _rgb(value, default):
    """An authored [r, g, b] prop as a tuple, or `default` when invalid."""
    try:
        r, g, b = (max(0, min(255, int(c))) for c in value)
        return (r, g, b)
    except (TypeError, ValueError):
        return tuple(default)


class VoxelRenderer:
    """Reusable presentation state + frame pipeline. Created once per
    process and shared by main() and tools/render_preview.py."""

    def __init__(self):
        self.light_map = LightMap()
        self.registry = default_registry()
        # reused full-screen surfaces (never reallocated per frame)
        self.particles = ParticleSystem()
        self.depth_cache = {}  # id(room) -> (room, depth_map)
        self.prev_collapsed = set()
        self.visited = {}  # world cell -> accent color
        self.last_room = None
        self.title_text = ""
        self.title_alpha = 0.0
        self.title_timer = 0.0
        self.zone_font = None
        self.hud_font = None
        self.saved_rooms = set()
        self._was_on_ground = False
        self._ambient_zone = "Blue Brinstar"
        # reused per-frame overlays (never reallocated)
        self._title_overlay = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)

    # -- fonts --------------------------------------------------------------

    def _get_hud_font(self):
        if self.hud_font is None:
            self.hud_font = pygame.font.SysFont("monospace", 13)
        return self.hud_font

    def _get_zone_font(self):
        if self.zone_font is None:
            self.zone_font = pygame.font.SysFont("Verdana,Tahoma,Arial", 32, bold=True)
        return self.zone_font

    # -- depth cache ---------------------------------------------------------

    def depth_for(self, room):
        key = id(room)
        entry = self.depth_cache.get(key)
        if entry is not None and entry[0] is room:
            return entry[1]
        if len(self.depth_cache) > 8:
            self.depth_cache.clear()
        depth = air_depth_map(room)
        self.depth_cache[key] = (room, depth)
        return depth

    # -- room-change bookkeeping ---------------------------------------------

    def on_room_change(self, world):
        """Track visited cells + zone title cards whenever the room
        changes (called at startup and after each transition)."""
        room = world.current_room
        if self.last_room is room:
            return
        accent = get_palette(room.zone)["accent"]
        for cell in room.cells:
            self.visited[cell] = accent
        if self.last_room is None or self.last_room.zone != room.zone:
            self.title_text = room.zone or ""
            self.title_timer = 0.0
        self.last_room = room

    # -- backdrop ------------------------------------------------------------

    def draw_backdrop(self, surface, zone, cam_x, cam_y):
        cam_px_x = cam_x * SCALE
        cam_px_y = cam_y * SCALE
        for layer, factor in get_backdrop_layers(zone):
            lw, lh = layer.get_size()
            off_x = -int(cam_px_x * factor) % lw
            off_y = -int(cam_px_y * factor) % lh
            for bx in (off_x - lw, off_x):
                for by in (off_y - lh, off_y):
                    surface.blit(layer, (bx, by))
        surface.blit(get_fog_gradient(zone), (0, 0))

    # -- tiles ---------------------------------------------------------------

    def draw_room_tiles(self, surface, room, cam_x, cam_y, tick,
                        crumble_active, crumble_collapsed):
        room_ox, room_oy = room_origin_px(room)
        depth = self.depth_for(room)
        cam_px = (round(cam_x * SCALE), round(cam_y * SCALE))
        size = TILE_SIZE * SCALE

        # cam_x/cam_y are WORLD game px; cull in room-local space.
        local_cam_x = cam_x - room_ox
        local_cam_y = cam_y - room_oy
        t_start_x = max(0, int(local_cam_x // TILE_SIZE) - 1)
        t_start_y = max(0, int(local_cam_y // TILE_SIZE) - 1)
        t_end_x = min(room.width - 1, int((local_cam_x + WINDOW_W / SCALE) // TILE_SIZE) + 1)
        t_end_y = min(room.height - 1, int((local_cam_y + WINDOW_H / SCALE) // TILE_SIZE) + 1)

        for ty in range(t_start_y, t_end_y + 1):
            for tx in range(t_start_x, t_end_x + 1):
                tile = room.tiles[ty][tx]
                if tile == TILE_EMPTY:
                    continue
                px = int(room_ox * SCALE + tx * size - cam_px[0])
                py = int(room_oy * SCALE + ty * size - cam_px[1])
                if px + size < 0 or py + size < 0 or px > WINDOW_W or py > WINDOW_H:
                    continue
                palette = room.tile_palettes[ty][tx] or room.default_palette
                kind = _TILE_KIND[tile]

                if tile == TILE_SOLID:
                    mask = tile_mask(room, tx, ty)
                    d = depth[ty][tx]
                    variant = variant_for(tx, ty, room.origin[0], room.origin[1])
                    surf, y_off = self.registry.get(
                        palette, kind, mask=mask, variant=variant,
                        shade=DEPTH_BRIGHTNESS[d])
                    surface.blit(surf, (px, py + y_off))
                elif tile == TILE_SPRING:
                    surf, y_off = self.registry.get(palette, kind, tick=tick)
                    surface.blit(surf, (px, py + y_off))
                elif tile == TILE_ONEWAY_PLAT:
                    variant = variant_for(tx, ty, room.origin[0], room.origin[1])
                    surf, y_off = self.registry.get(palette, kind, variant=variant)
                    surface.blit(surf, (px, py + y_off))
                elif tile == TILE_CRUMBLE:
                    self._draw_crumble(surface, palette, tx, ty, px, py, tick,
                                       crumble_active)

    def _draw_crumble(self, surface, palette, tx, ty, px, py, tick, crumble_active):
        frames_left = crumble_active.get((tx, ty))
        if frames_left is None:
            surf, y_off = self.registry.get(palette, "crumble", stage=0)
            surface.blit(surf, (px, py + y_off))
            return
        progress = 1.0 - frames_left / max(1, CRUMBLE_DELAY_FRAMES)
        level = min(3, int(progress * 3.4))
        shake = ((tick * 3 + tx * 5 + ty * 7) % 3) - 1  # -1..1 px
        surf, y_off = self.registry.get(palette, "crumble", stage=level)
        surface.blit(surf, (px + shake, py + shake + y_off))

    # -- entities ------------------------------------------------------------

    def draw_room_entities(self, surface, room, cam_x, cam_y, tick):
        room_ox, room_oy = room_origin_px(room)
        cam_px = (round(cam_x * SCALE), round(cam_y * SCALE))
        for entity in room.entities:
            etype = entity.get("type", "")
            if etype in _INVISIBLE_ENTITIES:
                continue
            tx = entity.get("tile_x", 0)
            ty = entity.get("tile_y", 0)
            cx = room_ox + tx * TILE_SIZE + TILE_SIZE / 2
            cy = room_oy + ty * TILE_SIZE + TILE_SIZE / 2
            sx = int(cx * SCALE - cam_px[0])
            sy = int(cy * SCALE - cam_px[1])
            if sx < -60 or sy < -60 or sx > WINDOW_W + 60 or sy > WINDOW_H + 60:
                continue
            if etype.startswith("item_"):
                frames = get_item_frames(etype)
                surf = frames[(tick // 4) % len(frames)]
                bob = int(round(math.sin(tick * 0.08 + tx * 0.7) * 3.0)) - 6
                surface.blit(surf, (sx - surf.get_width() // 2, sy - surf.get_height() // 2 + bob))
            elif etype == "save_station":
                surf, ax, ay = default_sprites().get_frame("save_station", "idle", tick)
                bx = int((room_ox + tx * TILE_SIZE + TILE_SIZE / 2) * SCALE - cam_px[0])
                by = int((room_oy + (ty + 1) * TILE_SIZE) * SCALE - cam_px[1])
                surface.blit(surf, (bx - ax, by - ay))

    # -- player --------------------------------------------------------------

    def player_anim(self, world, tick):
        """(state, frame_index) from the physics state; also spawns landing
        dust / spring sparks while observing the on-ground transition edges."""
        b = world.body
        room_ox, room_oy = room_origin_px(world.current_room)
        if self._was_on_ground and not b.on_ground and b.vy < -9.5:
            # just bounced off a spring (vy = SPRING_BOUNCE_SPEED)
            self.particles.spawn_sparks(room_ox + b.x + b.width / 2, room_oy + b.y + b.height)
        if not self._was_on_ground and b.on_ground and b.vy >= 0:
            self.particles.spawn_dust(room_ox + b.x + b.width / 2, room_oy + b.y + b.height)
        self._was_on_ground = b.on_ground
        if b.vy < -0.01 and not b.on_ground:
            return ("jump", 0)
        if b.vy > 0.01 and not b.on_ground:
            return ("fall", 0)
        if b.on_ground and abs(b.vx) > 0.01:
            return ("run", (tick // 5) % 6)
        return ("idle", (tick // 30) % 2)

    def draw_player(self, surface, world, cam_x, cam_y, tick, facing,
                    state_override=None):
        room_ox, room_oy = room_origin_px(world.current_room)
        wx = world.body.x + room_ox
        wy = world.body.y + room_oy
        state, frame = (state_override or ("fall", 0))
        if state_override is None:
            state, frame = self.player_anim(world, tick)
        surf, ax, ay = default_sprites().get_frame("player", state, tick,
                                                   facing=facing, by_tick=True)
        feet_x = (wx + world.body.width / 2) * SCALE - round(cam_x * SCALE)
        feet_y = (wy + world.body.height) * SCALE - round(cam_y * SCALE)
        surface.blit(surf, (int(feet_x - ax), int(feet_y - ay)))

    # -- particles ----------------------------------------------------------

    def draw_particles(self, surface, cam_x, cam_y, tick):
        self.particles.spawn_motes(self._ambient_zone, (cam_x, cam_y,
                                                        WINDOW_W / SCALE, WINDOW_H / SCALE), 180)
        self.particles.update(1.0)
        self.particles.draw(surface, cam_x, cam_y, tick)

    # -- static scene core ---------------------------------------------------

    def _render_scene(self, screen, room, cam_x, cam_y, tick,
                      crumble_active, crumble_collapsed, entities=True,
                      backdrop=True):
        """Backdrop + tiles + entities; the non-character core shared by
        render_frame and render_static. Authored lighting, player,
        particles, title card and minimap stay in the callers so
        render_frame's exact draw order is preserved."""
        if backdrop:
            self.draw_backdrop(screen, room.zone, cam_x, cam_y)
        self.draw_room_tiles(screen, room, cam_x, cam_y, tick,
                             crumble_active, crumble_collapsed)
        if entities:
            self.draw_room_entities(screen, room, cam_x, cam_y, tick)

    def render_static(self, screen, room, cam_x, cam_y, tick=0,
                      lighting=True, entities=True, backdrop=True):
        """Static render of one AssembledRoom with no GameWorld: backdrop,
        tiles (no crumble activity), entities, and the room's authored
        lighting when `lighting`. No player, particles, title card or
        minimap. Used by the maker's editor renders (render.py)."""
        cam_x, cam_y = _round_cam(cam_x, cam_y)
        self._ambient_zone = room.zone
        self._render_scene(screen, room, cam_x, cam_y, tick, {}, {},
                           entities=entities, backdrop=backdrop)
        if lighting:
            self.apply_room_lighting(screen, room, cam_x, cam_y, tick)

    # -- authored lighting ---------------------------------------------------

    def apply_room_lighting(self, screen, room, cam_x, cam_y, tick):
        """Lighting is authored data, never automatic. A room is flat and
        fully lit unless it holds an `ambient_light` entity, which darkens
        the whole screen to its `color` (default: the zone's `ambient`).
        Only then do `light` entities add glow: props `radius` (game px,
        default 48), `color` [r,g,b] (default [255,220,160]) and optional
        `flicker` (bool)."""
        ambient = None
        for entity in room.entities:
            if entity.get("type") == "ambient_light":
                ambient = _rgb(entity.get("color"),
                               get_palette(room.zone)["ambient"])
                break
        if ambient is None:
            return
        self.light_map.begin(ambient)
        room_ox, room_oy = room_origin_px(room)
        for entity in room.entities:
            if entity.get("type") != "light":
                continue
            tx = entity.get("tile_x", 0)
            ty = entity.get("tile_y", 0)
            lx = room_ox + tx * TILE_SIZE + TILE_SIZE / 2
            ly = room_oy + ty * TILE_SIZE + TILE_SIZE / 2
            try:
                radius = float(entity.get("radius", 48))
            except (TypeError, ValueError):
                radius = 48.0
            if entity.get("flicker"):
                radius *= 0.9 + 0.1 * math.sin(tick * 0.15 + tx * 1.7 + ty)
            self.light_map.add_light((lx - cam_x) * SCALE, (ly - cam_y) * SCALE,
                                     radius * SCALE,
                                     _rgb(entity.get("color"), (255, 220, 160)))
        self.light_map.apply(screen)

    # -- title card ----------------------------------------------------------

    def update_title(self, tick):
        if not self.title_text:
            return
        self.title_timer += 1.0
        total = 154  # 0.4s fade in + 1.6s hold + 0.6s fade out
        t = self.title_timer
        if t < 24:
            self.title_alpha = t / 24.0
        elif t >= total:
            self.title_alpha = 0.0
            self.title_text = ""
        else:
            fade_out = max(0.0, (total - t) / 36.0)
            self.title_alpha = fade_out

    def draw_title(self, surface, accent):
        if not self.title_text or self.title_alpha <= 0:
            return
        font = self._get_zone_font()
        text = self.title_text.upper()
        spacing = 2
        width = 0
        glyphs = []
        shadows = []
        for ch in text:
            g = font.render(ch, True, (255, 255, 255))
            shadows.append(font.render(ch, True, (20, 20, 28)))
            glyphs.append(g)
            width += g.get_width() + spacing
        width -= spacing
        x0 = (WINDOW_W - width) // 2
        y = 70
        glyph_h = max(g.get_height() for g in glyphs)
        alpha = int(255 * min(1.0, self.title_alpha))
        overlay = self._title_overlay
        overlay.fill((0, 0, 0, 0))
        cx = x0
        for g in shadows:
            overlay.blit(g, (cx + 2, y + 2))  # dark drop shadow
            cx += g.get_width() + spacing
        cx = x0
        for g in glyphs:
            overlay.blit(g, (cx, y))
            cx += g.get_width() + spacing
        rule_color = (accent[0], accent[1], accent[2], int(alpha * 0.9))
        pygame.draw.rect(overlay, rule_color, (x0 - 14, y - 12, width + 28, 2))
        pygame.draw.rect(overlay, rule_color, (x0 - 14, y + glyph_h + 6, width + 28, 2))
        overlay.set_alpha(alpha)
        surface.blit(overlay, (0, 0))

    # -- minimap -------------------------------------------------------------

    def draw_minimap(self, surface, world, tick):
        """Top-right 90s beveled metal panel tracking visited cells."""
        cell = world.current_world_cell()
        win_w, win_h = 9, 7
        cw, ch = 7, 5
        pad = 5
        pw = win_w * cw + pad * 2 + 8
        ph = win_h * ch + pad * 2 + 8
        px = WINDOW_W - pw - 8
        py = 8
        if self.last_room is not None:
            accent = get_palette(self.last_room.zone)["accent"]
        else:
            accent = (200, 200, 220)
        # beveled metal panel
        panel = pygame.Surface((pw, ph))
        panel.fill((96, 100, 118))
        pygame.draw.rect(panel, (210, 214, 228), (2, 2, pw - 4, 2))
        pygame.draw.rect(panel, (210, 214, 228), (2, 2, 2, ph - 4))
        pygame.draw.rect(panel, (52, 54, 64), (0, ph - 2, pw, 2))
        pygame.draw.rect(panel, (52, 54, 64), (pw - 2, 0, 2, ph))
        pygame.draw.rect(panel, (72, 76, 90), (2, 2, pw - 4, ph - 4), 1)
        surface.blit(panel, (px, py))

        x0 = cell[0] - win_w // 2
        y0 = cell[1] - win_h // 2
        blink = 0.6 + 0.4 * math.sin(tick * 0.2)
        for cy in range(win_h):
            for cx in range(win_w):
                coord = (x0 + cx, y0 + cy)
                if coord not in self.visited:
                    continue
                color = self.visited[coord]
                rx = px + 8 + pad + cx * cw
                ry = py + 8 + pad + cy * ch
                if coord == cell:
                    c = tuple(int(v * (0.6 + 0.4 * blink)) for v in (255, 255, 255))
                    pygame.draw.rect(surface, c, (rx, ry, cw, ch))
                else:
                    dark = tuple(int(v * 0.45) for v in color)
                    pygame.draw.rect(surface, dark, (rx, ry, cw, ch))
                pygame.draw.rect(surface, (10, 10, 14), (rx, ry, cw, ch), 1)

    # -- debug overlays ------------------------------------------------------

    def draw_debug_grid(self, surface, room, cam_x, cam_y):
        grid_surf = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
        cell_w_px = CELL_WIDTH_TILES * TILE_SIZE
        cell_h_px = CELL_HEIGHT_TILES * TILE_SIZE
        faint = (*GRID_LINE_COLOR, 70)
        room_ox, room_oy = room_origin_px(room)
        for gx in range(0, room.width * TILE_SIZE + 1, cell_w_px):
            x = (room_ox + gx - cam_x) * SCALE
            if 0 <= x <= WINDOW_W:
                draw_dashed_line(grid_surf, (x, 0), (x, WINDOW_H), faint)
        for gy in range(0, room.height * TILE_SIZE + 1, cell_h_px):
            y = (room_oy + gy - cam_y) * SCALE
            if 0 <= y <= WINDOW_H:
                draw_dashed_line(grid_surf, (0, y), (WINDOW_W, y), faint)
        surface.blit(grid_surf, (0, 0))

    def draw_hud(self, screen, world, loader):
        font = self._get_hud_font()
        lines = debug_lines(world, loader)
        for i, line in enumerate(lines):
            text = font.render(line, True, (255, 255, 255))
            shadow = font.render(line, True, (0, 0, 0))
            screen.blit(shadow, (9, 9 + i * 16))
            screen.blit(text, (8, 8 + i * 16))

    # -- full-frame pipeline -------------------------------------------------

    def render_frame(self, screen, world, cam_x, cam_y, tick, facing=1,
                     show_grid=False, show_hud=False, loader=None):
        """Draw one complete frame for the current room. Shared by main()
        and tools/render_preview.py."""
        room = world.current_room
        cam_x, cam_y = _round_cam(cam_x, cam_y)
        self._ambient_zone = room.zone
        room_ox, room_oy = room_origin_px(room)

        self.on_room_change(world)

        # 1-3. fog gradient + parallax backdrop, tiles, entities
        self._render_scene(screen, room, cam_x, cam_y, tick,
                           world.crumble.active, world.crumble.collapsed)

        # 4. player
        self.draw_player(screen, world, cam_x, cam_y, tick, facing)

        # 5. particles (ambient motes, dust, debris, sparks)
        self.draw_particles(screen, cam_x, cam_y, tick)

        # crumble debris: fire once when a block first collapses
        collapsed_now = set(world.crumble.collapsed.keys())
        for key in collapsed_now - self.prev_collapsed:
            self.particles.spawn_debris(room_ox + key[0] * TILE_SIZE + TILE_SIZE / 2,
                                        room_oy + key[1] * TILE_SIZE + TILE_SIZE / 2,
                                        room.zone)
        self.prev_collapsed = collapsed_now

        # 6. authored lighting (only rooms with an ambient_light entity)
        self.apply_room_lighting(screen, room, cam_x, cam_y, tick)

        # 10. zone title card
        self.update_title(tick)
        self.draw_title(screen, get_palette(room.zone)["accent"])

        # 11. minimap
        self.draw_minimap(screen, world, tick)

        # 12. debug grid (G) + HUD (H)
        if show_grid:
            self.draw_debug_grid(screen, room, cam_x, cam_y)
        if show_hud and loader is not None:
            self.draw_hud(screen, world, loader)

    # -- transition pipeline --------------------------------------------------

    def render_transition(self, screen, from_room, to_room, cam_x, cam_y,
                          player_wx, player_wy, tick, facing=1,
                          show_grid=False, loader=None):
        """Draw one frame of a RoomTransition: backdrop of the destination
        room, tiles/entities of both rooms, the player on a jump/fall
        frame, and the destination room's authored lighting."""
        cam_x, cam_y = _round_cam(cam_x, cam_y)
        self._ambient_zone = to_room.zone

        screen.fill((0, 0, 0))
        self.draw_backdrop(screen, to_room.zone, cam_x, cam_y)

        empty_crumble = {}
        for room in (from_room, to_room):
            self.draw_room_tiles(screen, room, cam_x, cam_y, tick,
                                 empty_crumble, empty_crumble)
        for room in (from_room, to_room):
            self.draw_room_entities(screen, room, cam_x, cam_y, tick)

        # player on a jump/fall frame at the interpolated world position
        state = "fall"
        surf, ax, ay = default_sprites().get_frame("player", state, tick,
                                                   facing=facing)
        feet_x = (player_wx + PLAYER_W / 2) * SCALE - round(cam_x * SCALE)
        feet_y = (player_wy + PLAYER_H) * SCALE - round(cam_y * SCALE)
        screen.blit(surf, (int(feet_x - ax), int(feet_y - ay)))

        self.draw_particles(screen, cam_x, cam_y, tick)
        self.apply_room_lighting(screen, to_room, cam_x, cam_y, tick)
        self.update_title(tick)
        self.draw_title(screen, get_palette(to_room.zone)["accent"])
        if show_grid:
            self.draw_debug_grid(screen, to_room, cam_x, cam_y)


# ---------------------------------------------------------------------------
# Door transition (presentation only)
# ---------------------------------------------------------------------------

TRANSITION_HOLD_FRAMES = 10
TRANSITION_SLIDE_FRAMES = 20
TRANSITION_SETTLE_FRAMES = 6


def _ease_in_out(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


class RoomTransition:
    """Super Metroid-style door transition: freeze + camera pan across the
    shared world-pixel grid. Built entirely from the TransitionEvent
    GameWorld hands back; draws through VoxelRenderer."""

    def __init__(self, event: TransitionEvent, facing: int = 1, show_grid: bool = False) -> None:
        self.facing = facing
        self.show_grid = show_grid
        self.from_room = event.from_room
        self.to_room = event.to_room
        from_ox, from_oy = room_origin_px(self.from_room)
        to_ox, to_oy = room_origin_px(self.to_room)
        self.from_world_pos = (event.from_pos[0] + from_ox, event.from_pos[1] + from_oy)
        self.to_world_pos = (event.to_pos[0] + to_ox, event.to_pos[1] + to_oy)
        self.from_cam = camera_for_point(
            self.from_room, self.from_world_pos[0] - from_ox + PLAYER_W / 2,
            self.from_world_pos[1] - from_oy + PLAYER_H / 2,
        )
        self.to_cam = camera_for_point(
            self.to_room, self.to_world_pos[0] - to_ox + PLAYER_W / 2,
            self.to_world_pos[1] - to_oy + PLAYER_H / 2,
        )
        self.frame = 0
        self.total = TRANSITION_HOLD_FRAMES + TRANSITION_SLIDE_FRAMES + TRANSITION_SETTLE_FRAMES

    def done(self) -> bool:
        return self.frame >= self.total

    def advance(self) -> None:
        self.frame += 1

    def state(self):
        """(cam_x, cam_y, player_world_x, player_world_y) for the frame."""
        if self.frame < TRANSITION_HOLD_FRAMES:
            return (*self.from_cam, *self.from_world_pos)
        if self.frame < TRANSITION_HOLD_FRAMES + TRANSITION_SLIDE_FRAMES:
            t = (self.frame - TRANSITION_HOLD_FRAMES) / TRANSITION_SLIDE_FRAMES
            eased = _ease_in_out(t)
            cam_x = self.from_cam[0] + (self.to_cam[0] - self.from_cam[0]) * eased
            cam_y = self.from_cam[1] + (self.to_cam[1] - self.from_cam[1]) * eased
            px = self.from_world_pos[0] + (self.to_world_pos[0] - self.from_world_pos[0]) * eased
            py = self.from_world_pos[1] + (self.to_world_pos[1] - self.from_world_pos[1]) * eased
            return (cam_x, cam_y, px, py)
        return (*self.to_cam, *self.to_world_pos)

    def draw(self, surface, renderer, tick):
        cam_x, cam_y, px, py = self.state()
        renderer.render_transition(surface, self.from_room, self.to_room,
                                   cam_x, cam_y, px, py, tick,
                                   facing=self.facing, show_grid=self.show_grid)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv=None) -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Metroidvania demo runner")
    parser.add_argument("--project", metavar="DIR",
                        help="project folder to run (default: "
                             "METROIDVANIA_PROJECT env, the export root's "
                             "project.json when running an exported game, "
                             "or the first project in projects/)")
    parser.add_argument("--start-cell", metavar="CX,CY",
                        help="spawn in cell CX,CY instead of the start cell")
    parser.add_argument("--start-tile", metavar="TX,TY",
                        help="spawn at tile TX,TY inside --start-cell "
                             "(player feet bottom-aligned on the tile)")
    args = parser.parse_args(argv)
    if args.project:
        set_active(Project.load(args.project))
    if args.start_tile and not args.start_cell:
        parser.error("--start-tile requires --start-cell")

    pygame.init()
    pygame.display.set_caption(active().title)
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    clock = pygame.time.Clock()
    renderer = VoxelRenderer()

    loader = WorldLoader(active().world_index_path, active().cells_dir,
                         cache_size=32)
    world = GameWorld(loader)
    if args.start_cell:
        cx, cy = (int(v) for v in args.start_cell.split(","))
        room = world.loader.assemble_from((cx, cy))
        world.current_room = room
        world.crumble = CrumbleTracker()
        if args.start_tile:
            tx, ty = (int(v) for v in args.start_tile.split(","))
        else:
            tx, ty = world._spawn_tile(room)
        if not (0 <= tx < CELL_WIDTH_TILES and 0 <= ty < CELL_HEIGHT_TILES):
            parser.error(f"--start-tile {tx},{ty} is outside the 16x10 cell")
        tx_room = tx + (cx - room.origin[0]) * CELL_WIDTH_TILES
        ty_room = ty + (cy - room.origin[1]) * CELL_HEIGHT_TILES
        world.body.x = float(tx_room * TILE_SIZE)
        world.body.y = float((ty_room + 1) * TILE_SIZE - PLAYER_H)
    transition = None
    facing = 1
    show_grid = False
    show_hud = False
    debug_window = DebugWindow()
    tick = 0

    running = True
    while running:
        tick += 1
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.WINDOWCLOSE:
                if debug_window.owns_event(event):
                    debug_window.close()
                    show_hud = False
                else:
                    running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key in (pygame.K_SPACE, pygame.K_UP, pygame.K_w):
                    if transition is None:
                        world.jump(JUMP_SPEED)
                elif event.key == pygame.K_g:
                    show_grid = not show_grid
                elif event.key == pygame.K_h:
                    if debug_window.is_open:
                        debug_window.close()
                        show_hud = False
                    elif debug_window.open():
                        show_hud = False
                    else:
                        show_hud = not show_hud

        if transition is None:
            keys = pygame.key.get_pressed()
            direction = 0
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                direction -= 1
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                direction += 1
            if direction != 0:
                facing = direction
            world.set_horizontal_input(direction, MOVE_SPEED)

            world.update()
            if world.last_transition is not None:
                t = world.last_transition
                print(f"[transition] {t.from_cell} -> {t.to_cell} (went {t.direction})")
                transition = RoomTransition(t, facing=facing, show_grid=show_grid)
                world.last_transition = None

        if transition is not None:
            transition.draw(screen, renderer, tick)
            transition.advance()
            if transition.done():
                renderer.on_room_change(world)
                transition = None
        else:
            cam_x, cam_y = compute_camera(world)
            renderer.render_frame(screen, world, cam_x, cam_y, tick,
                                  facing=facing, show_grid=show_grid,
                                  show_hud=show_hud, loader=loader)

        if debug_window.is_open:
            debug_window.draw(debug_lines(world, loader, clock, tick))

        pygame.display.flip()
        clock.tick(FPS)

    debug_window.close()
    pygame.quit()


if __name__ == "__main__":
    main()
