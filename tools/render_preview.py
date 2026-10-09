"""
Headless preview renderer for the voxel look.

Constructs the real WorldLoader/GameWorld, reuses src.main's frame
pipeline (render_frame / render_transition), and writes PNG screenshots
to /Users/ben/Documents/game/screenshots/:
  <zone_slug>.png      one per zone (Blue/Green/Pink/Red/Kraid rooms)
  transition.png       mid-door-transition frame
  upper_room.png       player on the floor of room (0,-1)
  shaft.png            player on a floor in room (4,2)'s Main Shaft
  sprites.png          contact sheet of procedural sprites + textures

Prints the lazy art-build time and the average frame time over 120
rendered frames in the Blue start room.

Run: SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \\
     .venv/bin/python -m tools.render_preview
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from src.engine.constants import MOVE_SPEED, TILE_SIZE  # noqa: E402
from src.engine.game_world import PLAYER_H, GameWorld  # noqa: E402
from src.engine.physics import CrumbleTracker  # noqa: E402
from src.engine.world_loader import WorldLoader  # noqa: E402
from src.gfx.sprites import get_item_frames, get_player_frame, get_save_station  # noqa: E402
from src.gfx.texture_registry import TextureRegistry  # noqa: E402
from src.main import (  # noqa: E402
    CELLS_DIR,
    ROOT,
    RoomTransition,
    VoxelRenderer,
    WINDOW_H,
    WINDOW_W,
    WORLD_INDEX_PATH,
    compute_camera,
)

ZONES = [
    ("Blue Brinstar", (0, 0), "blue"),
    ("Green Brinstar", (2, 3), "green"),
    ("Pink Brinstar", (-4, 3), "pink"),
    ("Red Brinstar", (8, 3), "red"),
    ("Kraid's Lair", (5, 9), "kraid"),
]

ART_BUILD_TIME = 0.0
AVG_FRAME_MS = 0.0


def _stand_on_floor(world):
    """Place the player standing on a floor near the room's center: find an
    empty tile column with solid below, near the middle third of the room."""
    room = world.current_room
    tiles = room.tiles
    h = len(tiles)
    w = len(tiles[0])
    mid = w // 2
    for offset in range(w // 3):
        for tx in (mid + offset, mid - offset):
            if tx < 0 or tx >= w:
                continue
            for ty in range(h - 2, 0, -1):
                if tiles[ty][tx] == 0 and tiles[ty + 1][tx] == 1:
                    world.body.x = float(tx * TILE_SIZE)
                    world.body.y = float((ty + 1) * TILE_SIZE - PLAYER_H)
                    world.body.vx = 0.0
                    world.body.vy = 0.0
                    world.body.on_ground = True
                    return True
    return False


def _set_room(world, loader, coord):
    room = loader.assemble_from(coord)
    world.current_room = room
    world.crumble = CrumbleTracker()
    _stand_on_floor(world)
    return room


def _slug_screens(screen, renderer, world, loader, tick, facing):
    cam_x, cam_y = compute_camera(world)
    renderer.render_frame(screen, world, cam_x, cam_y, tick,
                          facing=facing, loader=loader)


def capture_zone_shot(screen, renderer, world, loader, coord, slug, out_dir):
    _set_room(world, loader, coord)
    tick = 0
    direction = 1
    for i in range(90):
        if i % 20 == 0:
            direction = -direction
        world.set_horizontal_input(direction, MOVE_SPEED)
        world.update()
        tick += 1
    facing = direction
    _slug_screens(screen, renderer, world, loader, tick, facing)
    pygame.image.save(screen, str(out_dir / f"{slug}.png"))
    print(f"  saved {slug}.png")


def capture_transition(screen, renderer, world, loader, out_dir):
    """Walk east from the start until a door transition fires, capture the
    frame at roughly half the slide."""
    world.set_horizontal_input(0, MOVE_SPEED)
    tick = 0
    while True:
        world.set_horizontal_input(1, MOVE_SPEED)
        world.update()
        tick += 1
        if world.last_transition is not None:
            t = world.last_transition
            world.last_transition = None
            transition = RoomTransition(t, facing=1)
            half = transition.total // 2
            while transition.frame < half:
                transition.advance()
            transition.draw(screen, renderer, tick)
            pygame.image.save(screen, str(out_dir / "transition.png"))
            print(f"  saved transition.png (after {tick} frames walking east)")
            return
        if tick > 3000:
            print("  WARNING: no transition fired after 3000 frames")
            return


def capture_upper_room(screen, renderer, world, loader, out_dir):
    """Player standing on the floor of the upper room (0,-1) near x=60."""
    room = loader.assemble_from((0, -1))
    world.current_room = room
    world.crumble = CrumbleTracker()
    floor = None
    for ty in range(room.height - 2, 0, -1):
        if room.tiles[ty][3] == 0 and room.tiles[ty + 1][3] == 1:
            floor = ty
            break
    if floor is None:
        raise RuntimeError("no floor under x=60 in upper room (0,-1)")
    world.body.x = 60.0
    world.body.y = float((floor + 1) * TILE_SIZE - PLAYER_H)
    world.body.vx = 0.0
    world.body.vy = 0.0
    world.body.on_ground = True
    tick = 1
    for _ in range(30):
        world.update()
        tick += 1
    cam_x, cam_y = compute_camera(world)
    renderer.render_frame(screen, world, cam_x, cam_y, tick, facing=1, loader=loader)
    pygame.image.save(screen, str(out_dir / "upper_room.png"))
    print(f"  saved upper_room.png")


def capture_shaft(screen, renderer, world, loader, out_dir):
    """Player standing on a floor in room (4,2)'s assembled Main Shaft."""
    _set_room(world, loader, (4, 2))
    tick = 1
    for _ in range(30):
        world.update()
        tick += 1
    cam_x, cam_y = compute_camera(world)
    renderer.render_frame(screen, world, cam_x, cam_y, tick, facing=1, loader=loader)
    pygame.image.save(screen, str(out_dir / "shaft.png"))
    print(f"  saved shaft.png")


def capture_sprites(out_dir, registry):
    """Contact sheet of procedural sprites + palette textures (a few solid
    masks, oneway, spring frames, crumble stages for several palettes) on
    a dark gray background."""
    cell = 64
    cols = 16
    frames_per_palette = 3
    palettes = ["blue_brinstar", "green_brinstar", "kraids_lair"]
    solid_masks = (0, 5, 10, 15)
    n_player = 20  # 2 facings x (2 idle + 6 run + 1 jump + 1 fall)
    n_items = 12
    n_save = 1
    n_tex = len(palettes) * (len(solid_masks) + 1 + frames_per_palette + 4)
    total = n_player + n_items + n_save + n_tex
    rows = (total + cols - 1) // cols
    sheet = pygame.Surface((cols * cell, rows * cell))
    sheet.fill((38, 40, 46))

    def blit_centered(surf, x, y):
        sheet.blit(surf, (x + (cell - surf.get_width()) // 2,
                          y + (cell - surf.get_height()) // 2))

    ci = 0
    # player frames (left then right facing)
    for facing in (-1, 1):
        for state, n in (("idle", 2), ("run", 6), ("jump", 1), ("fall", 1)):
            for i in range(n):
                surf, ax, ay = get_player_frame(state, i, facing)
                cx = ci % cols
                cy = ci // cols
                sheet.blit(surf, (cx * cell + cell // 2 - ax, cy * cell + cell - 12 - ay))
                ci += 1
    # item rotation frames
    for surf in get_item_frames("item_varia_suit"):
        blit_centered(surf, (ci % cols) * cell, (ci // cols) * cell)
        ci += 1
    # save station
    surf, anchor = get_save_station()
    cx = ci % cols
    cy = ci // cols
    sheet.blit(surf, (cx * cell + cell // 2 - surf.get_width() // 2,
                      (cy + 1) * cell - 6 - anchor))
    ci += 1
    # palette textures: solid masks, oneway, spring frames, crumble stages
    for palette in palettes:
        for mask in solid_masks:
            surf, _ = registry.get(palette, "solid", mask=mask, variant=0)
            blit_centered(surf, (ci % cols) * cell, (ci // cols) * cell)
            ci += 1
        surf, _ = registry.get(palette, "oneway", variant=0)
        blit_centered(surf, (ci % cols) * cell, (ci // cols) * cell)
        ci += 1
        for frame in range(frames_per_palette):
            surf, _ = registry.get(palette, "spring", tick=frame * 6)
            blit_centered(surf, (ci % cols) * cell, (ci // cols) * cell)
            ci += 1
        for stage in range(4):
            surf, _ = registry.get(palette, "crumble", stage=stage)
            blit_centered(surf, (ci % cols) * cell, (ci // cols) * cell)
            ci += 1
    pygame.image.save(sheet, str(out_dir / "sprites.png"))
    print(f"  saved sprites.png ({sheet.get_width()}x{sheet.get_height()})")


def main():
    pygame.init()
    pygame.display.set_mode((WINDOW_W, WINDOW_H))
    screen = pygame.Surface((WINDOW_W, WINDOW_H))
    out_dir = ROOT / "screenshots"
    out_dir.mkdir(exist_ok=True)

    loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=32)
    world = GameWorld(loader)
    renderer = VoxelRenderer()

    # warm up: the Blue start room (lazy art builds here)
    t0 = time.time()
    cam_x, cam_y = compute_camera(world)
    renderer.render_frame(screen, world, cam_x, cam_y, 1, facing=1)
    global ART_BUILD_TIME, AVG_FRAME_MS
    ART_BUILD_TIME = time.time() - t0

    # average frame time over 120 rendered frames (alternating input)
    world = GameWorld(loader)  # fresh world at the start cell
    times = []
    direction = 1
    for i in range(120):
        if i % 20 == 0:
            direction = -direction
        world.set_horizontal_input(direction, MOVE_SPEED)
        world.update()
        cam_x, cam_y = compute_camera(world)
        t0 = time.time()
        renderer.render_frame(screen, world, cam_x, cam_y, i + 10,
                              facing=direction, loader=loader)
        times.append(time.time() - t0)
    AVG_FRAME_MS = sum(times) / len(times) * 1000.0

    print("zone screenshots:")
    for zone, coord, slug in ZONES:
        capture_zone_shot(screen, renderer, world, loader, coord, slug, out_dir)

    print("transition screenshot:")
    world = GameWorld(loader)
    renderer = VoxelRenderer()
    capture_transition(screen, renderer, world, loader, out_dir)

    print("upper room screenshot:")
    world = GameWorld(loader)
    renderer = VoxelRenderer()
    capture_upper_room(screen, renderer, world, loader, out_dir)

    print("main shaft screenshot:")
    world = GameWorld(loader)
    renderer = VoxelRenderer()
    capture_shaft(screen, renderer, world, loader, out_dir)

    print("sprite contact sheet:")
    capture_sprites(out_dir, renderer.registry)

    print(f"lazy art build time on first render: {ART_BUILD_TIME * 1000:.1f} ms")
    print(f"average frame time (120 frames, Blue start): {AVG_FRAME_MS:.2f} ms")
    print("done")


if __name__ == "__main__":
    main()
