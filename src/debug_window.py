"""
Separate OS window that shows live debug text (toggled with H in-game).

Uses pygame's SDL2 multi-window API (pygame._sdl2.video). If a second
window can't be created, `DebugWindow.open()` returns False and the caller
falls back to drawing the text over the game screen.
"""

from __future__ import annotations

import pygame

from src.engine.constants import TILE_SIZE

DEBUG_W = 560
DEBUG_H = 300
BG = (12, 14, 20)
FG = (220, 230, 240)
HEADER = (120, 200, 255)


def debug_lines(world, loader, clock=None, tick=0):
    """Debug text lines describing the current game state."""
    room = world.current_room
    b = world.body
    tx = int((b.x + b.width / 2) // TILE_SIZE)
    ty = int((b.y + b.height / 2) // TILE_SIZE)
    palette = ""
    if 0 <= ty < len(room.tile_palettes) and 0 <= tx < len(room.tile_palettes[0]):
        foot_y = min(ty + 1, room.height - 1)
        palette = room.tile_palettes[foot_y][tx] or f"(default) {room.default_palette}"
    lines = []
    if clock is not None:
        lines.append(f"fps: {clock.get_fps():5.1f}   tick: {tick}")
    lines += [
        f"zone: {room.zone or '(unnamed)'}",
        f"world cell under player: {world.current_world_cell()}",
        f"assembled playspace cells: {sorted(room.cells)}"
        + (" [capped]" if room.truncated else ""),
        f"room origin: {room.origin}   size: {room.width}x{room.height} tiles",
        f"room default palette: {room.default_palette}",
        f"player local px: ({b.x:7.2f}, {b.y:7.2f})   tile: ({tx}, {ty})",
        f"velocity: ({b.vx:5.2f}, {b.vy:5.2f})   on_ground: {b.on_ground}",
        f"jumps remaining: {world.jumps_remaining}",
        f"palette of tile below player: {palette}",
        f"loaded cells in memory: {len(loader.loaded_cell_coords())}"
        f"  (cache size {loader.cache_size})",
    ]
    return lines


class DebugWindow:
    def __init__(self):
        self.window = None
        self.renderer = None
        self.surface = None
        self.font = None
        self.failed = False

    @property
    def is_open(self):
        return self.window is not None

    def open(self):
        if self.window is not None:
            return True
        if self.failed:
            return False
        try:
            from pygame._sdl2.video import Renderer, Window

            main_win = Window.from_display_module()
            self.window = Window("Debug", size=(DEBUG_W, DEBUG_H))
            mx, my = main_win.position
            mw, _ = main_win.size
            self.window.position = (mx + mw + 12, my)
            self.renderer = Renderer(self.window)
            self.surface = pygame.Surface((DEBUG_W, DEBUG_H))
            self.font = pygame.font.SysFont("monospace", 14)
            main_win.focus()
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[debug] separate window unavailable ({exc}); using overlay")
            self.failed = True
            self.close()
            return False

    def close(self):
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.window = None
        self.renderer = None

    def owns_event(self, event):
        """True if this window-scoped event belongs to the debug window."""
        win = getattr(event, "window", None)
        return self.window is not None and win is not None and win == self.window

    def draw(self, lines):
        if self.window is None:
            return
        from pygame._sdl2.video import Texture

        self.surface.fill(BG)
        y = 8
        for i, line in enumerate(lines):
            color = HEADER if i == 0 else FG
            self.surface.blit(self.font.render(line, True, color), (10, y))
            y += 18
        tex = Texture.from_surface(self.renderer, self.surface)
        self.renderer.clear()
        tex.draw()
        self.renderer.present()
