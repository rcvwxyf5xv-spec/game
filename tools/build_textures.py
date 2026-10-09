"""
Build-time texture sheet generator: renders the palette texture sheets
(assets/textures/<palette>/solid.png, oneway.png, spring.png,
crumble.png) from each palette's JSON in assets/palettes/ using the
procedural voxel renderers in src.gfx.tiles.

Sheets are deterministic build outputs and should be committed; the game
at runtime loads them through src.gfx.texture_registry.

Run: SDL_VIDEODRIVER=dummy .venv/bin/python -m tools.build_textures
     [palette_name ...]   (no names = all palettes in assets/palettes)
"""

from __future__ import annotations

import json
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

PALETTES_DIR = ROOT / "assets" / "palettes"
TEXTURES_DIR = ROOT / "assets" / "textures"


def _fit_cell(surf):
    """Crop a rendered surface to its bottom 48 rows (the tile footprint)
    so it fits a 48x48 sheet cell; rock renderers return taller surfaces
    whose raised caps live above the footprint."""
    w, h = surf.get_size()
    if h <= 48:
        return surf
    return surf.subsurface((0, h - 48, w, 48))


def build_solid(pal, colors, name, spec):
    variants = spec["variants"]
    overhang = spec["overhang_px"]
    cell_w = pal["tile_px"]
    cell_h = pal["tile_px"] + overhang
    sheet = pygame.Surface((variants * cell_w, 16 * cell_h), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for mask in range(16):
        for variant in range(variants):
            surf, y_off = get_rock_tile(colors, name, mask, variant, 0)
            sheet.blit(surf, (variant * cell_w, mask * cell_h + overhang + y_off))
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
        sheet.blit(_fit_cell(surf), (i * tile_px, 0))
    return sheet


def build_crumble(pal, colors, name, spec):
    stages = spec["stages"]
    tile_px = pal["tile_px"]
    sheet = pygame.Surface((stages * tile_px, tile_px), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for stage in range(stages):
        sheet.blit(_fit_cell(get_crumble_tile(colors, name, stage)), (stage * tile_px, 0))
    return sheet


_BUILDERS = {
    "solid": build_solid,
    "oneway": build_oneway,
    "spring": build_spring,
    "crumble": build_crumble,
}


def build_palette(name: str):
    path = PALETTES_DIR / f"{name}.json"
    if not path.exists():
        print(f"warning: no palette JSON at {path.relative_to(ROOT)}")
        return
    pal = json.loads(path.read_text())
    generator = pal.get("build", {}).get("generator", "")
    if generator != "voxel_rock":
        print(f"warning: unknown generator {generator!r} for {name}, skipping")
        return
    colors = pal["build"]["colors"]
    out_dir = TEXTURES_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    for kind, spec in pal["textures"].items():
        sheet = _BUILDERS[kind](pal, colors, name, spec)
        rel_out = out_dir / spec["file"]
        pygame.image.save(sheet, str(rel_out))
        print(f"  wrote {rel_out.relative_to(ROOT)} {sheet.get_width()}x{sheet.get_height()}")


def main(argv):
    pygame.init()
    names = argv[1:] or sorted(p.name[:-5] for p in PALETTES_DIR.glob("*.json"))
    for name in names:
        build_palette(name)


if __name__ == "__main__":
    main(sys.argv)
