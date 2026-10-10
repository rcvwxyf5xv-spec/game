"""
Build-time texture sheet generator: renders the palette texture sheets
(assets/textures/<palette>/solid.png, oneway.png, spring.png,
crumble.png) from each palette's JSON in assets/palettes/ using the
procedural voxel renderers in src.gfx.tiles.

Sheets are deterministic build outputs and should be committed; the game
at runtime loads them through src.gfx.texture_registry.

Run: SDL_VIDEODRIVER=dummy .venv/bin/python -m tools.build_textures
     [--project DIR] [palette_name ...]
     (no names = all palettes in the active project)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from src.gfx.tiles import (  # noqa: E402
    ROCK_DESIGN_H,
    VOXEL_PX,
    get_crumble_tile,
    get_oneway_tile,
    get_rock_tile,
    get_spring_frames,
)
from src.project import Project, active, set_active  # noqa: E402


def _fit_frame(surf):
    """Crop a rendered surface to its bottom 48 rows (the tile footprint)
    so it fits a 48x48 sheet frame; rock renderers return taller surfaces
    whose raised caps live above the footprint."""
    w, h = surf.get_size()
    if h <= 48:
        return surf
    return surf.subsurface((0, h - 48, w, 48))


def build_solid(pal, colors, name, spec):
    variants = spec["variants"]
    overhang = spec["overhang_px"]
    frame_w = pal["tile_px"]
    frame_h = pal["tile_px"] + overhang
    sheet = pygame.Surface((variants * frame_w, 16 * frame_h), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for mask in range(16):
        for variant in range(variants):
            surf, y_off = get_rock_tile(colors, name, mask, variant, 0)
            sheet.blit(surf, (variant * frame_w, mask * frame_h + overhang + y_off))
    return sheet


def build_oneway(pal, colors, name, spec):
    variants = spec["variants"]
    tile_px = pal["tile_px"]
    sheet = pygame.Surface((variants * tile_px, tile_px), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for variant in range(variants):
        sheet.blit(get_oneway_tile(colors, name, variant), (variant * tile_px, 0))
    return sheet


def build_spring(pal, colors, name, spec):
    frames = get_spring_frames(colors, name)
    tile_px = pal["tile_px"]
    sheet = pygame.Surface((len(frames) * tile_px, tile_px), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for i, surf in enumerate(frames):
        sheet.blit(_fit_frame(surf), (i * tile_px, 0))
    return sheet


def build_crumble(pal, colors, name, spec):
    stages = spec["stages"]
    tile_px = pal["tile_px"]
    sheet = pygame.Surface((stages * tile_px, tile_px), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for stage in range(stages):
        sheet.blit(_fit_frame(get_crumble_tile(colors, name, stage)), (stage * tile_px, 0))
    return sheet


_BUILDERS = {
    "solid": build_solid,
    "oneway": build_oneway,
    "spring": build_spring,
    "crumble": build_crumble,
}


def build_palette(name: str, palettes_dir: Path, textures_dir: Path) -> list:
    """Render one palette's texture sheets into textures_dir; returns the
    list of written paths."""
    palettes_dir = Path(palettes_dir)
    textures_dir = Path(textures_dir)
    path = palettes_dir / f"{name}.json"
    written = []
    if not path.exists():
        print(f"warning: no palette JSON at {os.path.relpath(path, ROOT)}")
        return written
    pal = json.loads(path.read_text())
    generator = pal.get("build", {}).get("generator", "")
    if generator != "voxel_rock":
        print(f"warning: unknown generator {generator!r} for {name}, skipping")
        return written
    colors = pal["build"]["colors"]
    locked = pal.get("build", {}).get("locked", [])
    out_dir = textures_dir / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind, spec in pal["textures"].items():
        if kind in locked:
            print(f"  skipping locked {kind} (imported texture kept as-is)")
            continue
        sheet = _BUILDERS[kind](pal, colors, name, spec)
        rel_out = out_dir / spec["file"]
        pygame.image.save(sheet, str(rel_out))
        written.append(rel_out)
        print(f"  wrote {os.path.relpath(rel_out, ROOT)} "
              f"{sheet.get_width()}x{sheet.get_height()}")
    return written


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Build palette texture sheets for a project")
    parser.add_argument("--project", metavar="DIR",
                        help="project folder to build textures for "
                             "(default: active project)")
    parser.add_argument("names", nargs="*",
                        help="palette names (default: all palettes)")
    args = parser.parse_args(argv)
    if args.project:
        set_active(Project.load(args.project))

    pygame.init()
    project = active()
    names = args.names or sorted(p.name[:-5] for p in
                                 project.palettes_dir.glob("*.json"))
    for name in names:
        build_palette(name, project.palettes_dir, project.textures_dir)


if __name__ == "__main__":
    main()
