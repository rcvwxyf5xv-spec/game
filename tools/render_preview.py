"""
Headless preview renderer for the voxel look.

Constructs the real WorldLoader/GameWorld, reuses src.main's frame
pipeline (render_frame / render_transition), and writes PNG screenshots
to /Users/ben/Documents/game/screenshots/:
  <zone_slug>.png      one per zone (Blue/Green/Pink/Red/Kraid rooms)
  springs_room.png     room cell (2,3) (spring pads)
  oneway_room.png      room cell (-1,3) (one-way platforms)
  save_room.png        room cell (5,1) (save station)
  transition.png       mid-door-transition frame
  upper_room.png       player on the floor of room (0,-1)
  shaft.png            player on a floor in room (4,2)'s Main Shaft
  sprites.png          contact sheet of sprites + spring/oneway textures
  player_closeup.png   2x close-up of every player frame

Prints the lazy art-build time and the average frame time over 120
rendered frames in the Blue start room.

Run: SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy \\
     .venv/bin/python -m tools.render_preview [--project DIR]
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
from src.gfx.sprites import get_item_frames  # noqa: E402
from src.gfx.sprite_registry import default_sprites  # noqa: E402
from src.gfx.texture_registry import TextureRegistry  # noqa: E402
from src.main import (  # noqa: E402
    RoomTransition,
    VoxelRenderer,
    WINDOW_H,
    WINDOW_W,
    compute_camera,
)
from src.project import Project, active, set_active  # noqa: E402

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


def capture_sprites(out_dir, registry, sprites):
    """Contact sheet of data-driven sprites + palette textures: every player
    frame (both facings) at the game scale, the save station frames, the
    spring 3 frames and oneway 4 variants for blue/green/red Brinstar, plus
    one item frame, on a dark gray background."""
    slot = 64
    cols = 16
    n_player = 2 * (2 + 6 + 1 + 1)
    n_save = 4
    n_spring = 3 * 3
    n_oneway = 3 * 4
    total = n_player + n_save + n_spring + n_oneway + 1
    rows = (total + cols - 1) // cols
    sheet = pygame.Surface((cols * slot, rows * slot))
    sheet.fill((38, 40, 46))

    def blit_centered(surf, x, y):
        sheet.blit(surf, (x + (slot - surf.get_width()) // 2,
                          y + (slot - surf.get_height()) // 2))

    def blit_anchored(surf, ax, ay, ci):
        cx = ci % cols
        cy = ci // cols
        by = cy * slot + slot - 12
        sheet.blit(surf, (cx * slot + slot // 2 - ax, by - ay))

    ci = 0
    # player frames, both facings, anchored at the feet base line
    for facing in (-1, 1):
        for anim, n, ticks in (("idle", 2, 30), ("run", 6, 5),
                               ("jump", 1, 1), ("fall", 1, 1)):
            for i in range(n):
                surf, ax, ay = sprites.get_frame("player", anim, i * ticks,
                                                 facing=facing, by_tick=True)
                blit_anchored(surf, ax, ay, ci)
                ci += 1
    # save station frames
    for i in range(4):
        surf, ax, ay = sprites.get_frame("save_station", "idle", i * 8)
        blit_anchored(surf, ax, ay, ci)
        ci += 1
    # spring frames + oneway variants for three palettes
    for palette in ("blue_brinstar", "green_brinstar", "red_brinstar"):
        for frame in range(3):
            surf, _ = registry.get(palette, "spring", tick=frame * 6)
            blit_centered(surf, (ci % cols) * slot, (ci // cols) * slot)
            ci += 1
        for variant in range(4):
            surf, _ = registry.get(palette, "oneway", variant=variant)
            blit_centered(surf, (ci % cols) * slot, (ci // cols) * slot)
            ci += 1
    # one item frame
    blit_centered(get_item_frames("item_varia_suit")[0],
                  (ci % cols) * slot, (ci // cols) * slot)
    ci += 1
    pygame.image.save(sheet, str(out_dir / "sprites.png"))
    print(f"  saved sprites.png ({sheet.get_width()}x{sheet.get_height()})")


def capture_player_closeup(out_dir, sprites):
    """2x-scaled contact strip of every player frame (both facings) for
    close inspection of the pixel art."""
    slot = 56
    strip = pygame.Surface((slot * 20, slot))
    strip.fill((38, 40, 46))
    ci = 0
    for facing in (-1, 1):
        for anim, n, ticks in (("idle", 2, 30), ("run", 6, 5),
                               ("jump", 1, 1), ("fall", 1, 1)):
            for i in range(n):
                surf, ax, ay = sprites.get_frame("player", anim, i * ticks,
                                                 facing=facing, by_tick=True)
                by = slot - 12
                strip.blit(surf, (ci * slot + slot // 2 - ax, by - ay))
                ci += 1
    closeup = pygame.transform.scale(strip, (strip.get_width() * 2,
                                             strip.get_height() * 2))
    pygame.image.save(closeup, str(out_dir / "player_closeup.png"))
    print(f"  saved player_closeup.png ({closeup.get_width()}x{closeup.get_height()})")


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Headless preview renderer")
    parser.add_argument("--project", metavar="DIR",
                        help="project folder to render (default: active project)")
    args = parser.parse_args(argv)
    if args.project:
        set_active(Project.load(args.project))

    pygame.init()
    pygame.display.set_mode((WINDOW_W, WINDOW_H))
    screen = pygame.Surface((WINDOW_W, WINDOW_H))
    out_dir = ROOT / "screenshots"
    out_dir.mkdir(exist_ok=True)

    loader = WorldLoader(active().world_index_path, active().cells_dir,
                         cache_size=32)
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

    print("feature room screenshots:")
    for coord, slug in ((2, 3), "springs_room"), ((-1, 3), "oneway_room"), ((5, 1), "save_room"):
        world = GameWorld(loader)
        renderer = VoxelRenderer()
        capture_zone_shot(screen, renderer, world, loader, coord, slug, out_dir)

    print("sprite contact sheet:")
    sprites = default_sprites()
    capture_sprites(out_dir, renderer.registry, sprites)
    capture_player_closeup(out_dir, sprites)

    print(f"lazy art build time on first render: {ART_BUILD_TIME * 1000:.1f} ms")
    print(f"average frame time (120 frames, Blue start): {AVG_FRAME_MS:.2f} ms")
    print("done")


if __name__ == "__main__":
    main()
