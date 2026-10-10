"""
Build-time sprite sheet generator: renders each data-driven pixel-art
sprite definition in <project>/sprite_src/<name>.json (hand-editable ASCII
frame grids, a future editor's format) into a horizontal strip sheet
<project>/sprites/<name>.png plus a runtime metadata JSON (frame size,
anchor, per-animation frame ranges and tick rates).

Sheets are deterministic build outputs and should be committed; the game
at runtime loads them through src.gfx.sprite_registry.

Run: SDL_VIDEODRIVER=dummy .venv/bin/python -m tools.build_sprites
     [--project DIR] [name ...]
     (no names = all sprites in the active project)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pygame  # noqa: E402

from src.gfx.pixelart import render_pixel_art  # noqa: E402
from src.project import Project, active, set_active  # noqa: E402


def _validate(spec):
    """Check grid sizes (rows equal length, all frames the same grid,
    unknown chars) and return (grid_w, grid_h). Raises ValueError with a
    clear message otherwise."""
    name = spec.get("name", "?")
    colors = {str(ch): tuple(int(c) for c in rgb) for ch, rgb in spec.get("colors", {}).items()}
    heights = {str(ch): int(h) for ch, h in spec.get("heights", {}).items()}
    unknown_chars = set(heights) - set(colors)
    if unknown_chars:
        raise ValueError(f"{name}: heights reference chars not in colors: {sorted(unknown_chars)}")
    if spec.get("voxel_px", 3) < 1:
        raise ValueError(f"{name}: voxel_px must be >= 1")
    grid_w = grid_h = None
    for anim_name, anim in spec.get("animations", {}).items():
        frames = anim.get("frames", [])
        if not frames:
            raise ValueError(f"{name}: animation {anim_name!r} has no frames")
        for fi, frame in enumerate(frames):
            if not frame or len({len(row) for row in frame}) != 1:
                raise ValueError(
                    f"{name}: animation {anim_name!r} frame {fi} rows are "
                    f"not all the same length")
            if grid_w is None:
                grid_w, grid_h = len(frame[0]), len(frame)
            elif (len(frame[0]), len(frame)) != (grid_w, grid_h):
                raise ValueError(
                    f"{name}: animation {anim_name!r} frame {fi} is "
                    f"{len(frame[0])}x{len(frame)}, expected {grid_w}x{grid_h}")
            row_chars = {ch for row in frame for ch in row}
            bad = sorted(row_chars - set(colors) - {"."})
            if bad:
                raise ValueError(
                    f"{name}: animation {anim_name!r} frame {fi} has chars "
                    f"missing from colors: {bad}")
    if grid_w is None:
        raise ValueError(f"{name}: no animations defined")
    return grid_w, grid_h, colors, heights


def build_sprite(src_json_path: Path, out_dir: Path) -> list:
    """Render one sprite definition JSON into sheet + metadata written to
    out_dir; returns the written paths."""
    src_path = Path(src_json_path)
    out_dir = Path(out_dir)
    name = src_path.stem
    written = []
    if not src_path.exists():
        print(f"warning: no sprite source at {os.path.relpath(src_path, ROOT)}")
        return written
    spec = json.loads(src_path.read_text())
    if spec.get("name", name) != name:
        print(f"warning: sprite JSON name {spec.get('name')!r} != {name!r}")
    grid_w, grid_h, colors, heights = _validate(spec)
    vp = int(spec.get("voxel_px", 3))
    outline = tuple(int(c) for c in spec["outline"]) if spec.get("outline") else None
    ax, ay = spec.get("anchor", [0, 0])
    if not (0 <= ax < grid_w) or not (0 <= ay < grid_h):
        raise ValueError(
            f"{name}: anchor {ax},{ay} outside {grid_w}x{grid_h} grid")

    frames = []
    for anim_name, anim in spec["animations"].items():
        for frame in anim["frames"]:
            frames.append(render_pixel_art(frame, colors, heights, vp, outline))
    frame_w = max(f.get_width() for f in frames)
    frame_h = max(f.get_height() for f in frames)
    sheet = pygame.Surface((frame_w * len(frames), frame_h), pygame.SRCALPHA)
    sheet.fill((0, 0, 0, 0))
    for i, f in enumerate(frames):
        # bottom-aligned so the art grid (and anchor) stay fixed; raised
        # overhang details may make a frame taller and its top cropped
        sheet.blit(f, (i * frame_w, frame_h - f.get_height()))

    out_dir.mkdir(parents=True, exist_ok=True)
    png_path = out_dir / f"{name}.png"
    pygame.image.save(sheet, str(png_path))
    written.append(png_path)

    pad = vp if outline else 0
    anchor_px = [pad + ax * vp + vp // 2, pad + (ay + 1) * vp]
    meta = {
        "name": name,
        "frame_w": frame_w,
        "frame_h": frame_h,
        "anchor_px": anchor_px,
        "animations": {
            anim_name: {
                "start": sum(len(v["frames"]) for v in
                             list(spec["animations"].values())[:i]),
                "count": len(anim["frames"]),
                "frame_ticks": anim.get("frame_ticks", 1),
            }
            for i, (anim_name, anim) in enumerate(spec["animations"].items())
        },
    }
    meta_path = out_dir / f"{name}.json"
    meta_path.write_text(json.dumps(meta, indent=1))
    written.append(meta_path)
    print(f"  wrote {os.path.relpath(png_path, ROOT)} "
          f"{sheet.get_width()}x{sheet.get_height()}"
          f" | {os.path.relpath(meta_path, ROOT)}")
    return written


def build_all(sprite_src_dir: Path, sprites_dir: Path) -> list:
    """Build every sprite definition under sprite_src_dir into sprites_dir;
    returns all written paths."""
    src_dir = Path(sprite_src_dir)
    out_dir = Path(sprites_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for src_path in sorted(src_dir.glob("*.json")):
        written.extend(build_sprite(src_path, out_dir))
    return written


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(
        description="Build sprite sheets for a project")
    parser.add_argument("--project", metavar="DIR",
                        help="project folder to build sprites for "
                             "(default: active project)")
    parser.add_argument("names", nargs="*",
                        help="sprite names (default: all sprites)")
    args = parser.parse_args(argv)
    if args.project:
        set_active(Project.load(args.project))

    pygame.init()
    project = active()
    if args.names:
        for name in args.names:
            build_sprite(project.sprite_src_dir / f"{name}.json",
                         project.sprites_dir)
    else:
        build_all(project.sprite_src_dir, project.sprites_dir)


if __name__ == "__main__":
    main()
