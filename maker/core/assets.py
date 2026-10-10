"""Immediate asset file operations for ProjectModel (textures + sprites).

These build/import/export PNG sheets and write them (plus the palette or
sprite source JSON) directly to disk. They are deliberate exceptions to
undo/redo: the *model data* part of an operation (e.g. the palette's
textures[kind] spec) is applied through ops.set_palette_data and is
undoable, but the files themselves are immediate side effects — undoing a
texture import does not delete the written PNG. Callers that want to
roll a generation back should rebuild from the older spec instead.

All pygame work here is headless (SDL_VIDEODRIVER=dummy) via
maker.core.render.ensure_pygame.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import List, Optional

import pygame

from maker.core import ops
from maker.core.errors import OpError
from maker.core.render import ensure_pygame
from src.project import active

TEXTURE_KINDS = ("solid", "oneway", "spring", "crumble")


def _invalidate_if_active(model) -> None:
    if active().root == model.project.root:
        from maker.core.render import reset_caches
        reset_caches()


def rebuild_textures(model, names: Optional[list] = None,
                     write: bool = True) -> List[Path]:
    """Regenerate texture sheets for palette `names` (default: all).

    Each palette's current JSON is written from the model to disk first
    (and its dirty flag cleared), then tools.build_textures.build_palette
    renders every non-locked kind. Immediate file operation, not part of
    undo. `write=False` only validates palette names.
    """
    ensure_pygame(headless=True)
    from tools.build_textures import build_palette

    if names is None:
        names = sorted(model.palettes)
    else:
        names = list(names)
    for name in names:
        if name not in model.palettes:
            raise OpError(f"no palette {name!r}")
    written: List[Path] = []
    if not write:
        return written
    palettes_dir = model.project.palettes_dir
    textures_dir = model.project.textures_dir
    for name in names:
        dest = palettes_dir / f"{name}.json"
        model._atomic_json_write(
            dest, json.dumps(model._palettes[name], indent=2) + "\n")
        model.mark_palette_saved(name)
        written.extend(build_palette(name, palettes_dir, textures_dir))
    _invalidate_if_active(model)
    return written


def _slice_horizontal(img, tile_px: int):
    """Slice a horizontal strip of tile_px-square tiles; 16px-tall rows are
    upscaled x3 (nearest). Returns (cell_imgs, n) or None if the dims do
    not match a strip."""
    w, h = img.get_size()
    if h == 16 and tile_px != 16:
        img = pygame.transform.scale(img, (w * 3, h * 3))
        w, h = img.get_size()
    if h != tile_px or w % tile_px or w < tile_px:
        return None
    n = w // tile_px
    return ([img.subsurface((i * tile_px, 0, tile_px, tile_px))
             for i in range(n)], n)


def _sheet_autotile(img, tile_px: int, variants: int, overhang: int):
    """A full autotile16 sheet of the given size, or None."""
    if img.get_height() != 16 * (tile_px + overhang):
        return None
    if img.get_width() != variants * tile_px:
        return None
    return img


def _new_spec(kind: str, tile_px: int, n: int, variants: int, overhang: int,
              frame_ticks: int) -> dict:
    if kind == "solid":
        return {"file": "solid.png", "layout": "autotile16",
                "variants": variants, "overhang_px": overhang}
    if kind == "oneway":
        return {"file": "oneway.png", "layout": "variants", "variants": n}
    if kind == "spring":
        return {"file": "spring.png", "layout": "frames", "frames": n,
                "frame_ticks": frame_ticks}
    return {"file": "crumble.png", "layout": "stages", "stages": n}


def import_texture(model, palette, kind, png_path, mode="auto",
                   variants=None, overhang_px=None, frames=None,
                   frame_ticks=None, stages=None, write=True) -> dict:
    """Import a texture sheet for `kind` into `palette` (immediate write).

    Accepted inputs (mode "auto" decides; "single"/"full"/"strip" force):
      * a full sheet already in the palette's layout for that kind. solid
        autotile16 sheets must be exactly variants*tile_px wide and
        16*(tile_px+overhang) tall; variants/frames/stages sheets are
        tile_px tall with the count inferred from the width;
      * one 16x16 or 48x48 tile image: oneway -> 1 variant, spring ->
        1 frame, crumble -> repeated for the existing stage count, solid
        -> an autotile16 sheet (variants=1, overhang 0) with the image
        repeated for all 16 masks;
      * a horizontal 16px or 48px strip of N tiles for non-solid kinds
        (upscaled x3 nearest when 16px) -> N variants/frames/stages.

    Writes textures/<palette>/<kind>.png, updates the palette's
    textures[kind] spec through ops.set_palette_data and adds the kind to
    build.locked, then writes the palette JSON to disk immediately so the
    renderer sees it, and invalidates. `write=False` skips the file
    writes (dry run).
    """
    if kind not in TEXTURE_KINDS:
        raise OpError(
            f"unknown texture kind {kind!r} (expected one of "
            f"{list(TEXTURE_KINDS)})")
    if palette not in model.palettes:
        raise OpError(f"no palette {palette!r}")
    if mode not in ("auto", "single", "full", "strip"):
        raise OpError(
            f"mode must be 'auto', 'single', 'full' or 'strip', got {mode!r}")
    ensure_pygame(headless=True)
    pal = model._palettes[palette]
    tile_px = int(pal.get("tile_px", 48))
    spec = (pal.get("textures") or {}).get(kind) or {}
    img = pygame.image.load(str(Path(png_path)))
    w, h = img.get_size()
    is_single = (w == 16 and h == 16) or (w == tile_px and h == tile_px)

    if mode == "auto":
        if is_single:
            chosen = "single"
        elif kind == "solid":
            chosen = "full"
        else:
            chosen = "strip"
    else:
        chosen = mode
    if chosen == "single" and not is_single:
        raise OpError(
            f"single-tile mode needs a 16x16 or {tile_px}x{tile_px} image, "
            f"got {w}x{h}")
    if kind == "solid" and chosen == "strip":
        raise OpError(
            "solid textures are autotile16 sheets; a horizontal strip is "
            "only accepted for oneway/spring/crumble")

    out_dir = model.project.textures_dir / palette
    if write:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{kind}.png"

    if kind == "solid":
        variants = 1 if variants is None else int(variants)
        overhang = 0 if overhang_px is None else int(overhang_px)
        if chosen == "full":
            sheet = _sheet_autotile(img, tile_px, variants, overhang)
            if sheet is None:
                raise OpError(
                    f"full solid sheet must be exactly "
                    f"{variants}x{tile_px} wide and "
                    f"16x{tile_px + overhang} tall for variants={variants} "
                    f"overhang={overhang}, got {w}x{h}")
            n = variants
        else:
            frame_h = tile_px + overhang
            sheet = pygame.Surface((variants * tile_px, 16 * frame_h),
                                   pygame.SRCALPHA)
            sheet.fill((0, 0, 0, 0))
            for mask in range(16):
                for v in range(variants):
                    sheet.blit(img, (v * tile_px, mask * frame_h))
            n = variants
        new_spec = _new_spec(kind, tile_px, n, variants, overhang, 0)
    else:
        if chosen == "single":
            if w == 16:
                img = pygame.transform.scale(img, (tile_px, tile_px))
            if kind == "oneway":
                n = variants if variants is not None else 1
                imgs = [img] * n
            elif kind == "spring":
                n = frames if frames is not None else 1
                imgs = [img] * n
            else:
                n = stages if stages is not None else int(spec.get("stages", 4))
                imgs = [img] * n
        else:
            sliced = _slice_horizontal(img, tile_px)
            if sliced is None:
                raise OpError(
                    f"strip for {kind} must be a horizontal run of tiles "
                    f"{tile_px}px tall (or 16px tall strips, upscaled x3), "
                    f"got {w}x{h}")
            imgs, n = sliced
            if kind == "oneway" and variants is not None and variants != n:
                raise OpError(f"image has {n} variants, --variants={variants}")
            if kind == "spring" and frames is not None and frames != n:
                raise OpError(f"image has {n} frames, --frames={frames}")
            if kind == "crumble" and stages is not None and stages != n:
                raise OpError(f"image has {n} stages, --stages={stages}")
        sheet = pygame.Surface((n * tile_px, tile_px), pygame.SRCALPHA)
        sheet.fill((0, 0, 0, 0))
        for i, cell in enumerate(imgs):
            sheet.blit(cell, (i * tile_px, 0))
        frame_ticks = frame_ticks if frame_ticks is not None else int(
            spec.get("frame_ticks", 6))
        new_spec = _new_spec(kind, tile_px, n, n, 0, frame_ticks)

    if write:
        pygame.image.save(sheet, str(out_path))

    new_pal = copy.deepcopy(model._palettes[palette])
    new_pal.setdefault("textures", {})[kind] = new_spec
    locked = new_pal.setdefault("build", {}).setdefault("locked", [])
    if kind not in locked:
        locked.append(kind)
    ops.set_palette_data(model, palette, new_pal)

    if write:
        dest = model.project.palettes_dir / f"{palette}.json"
        model._atomic_json_write(
            dest, json.dumps(model._palettes[palette], indent=2) + "\n")
        model.mark_palette_saved(palette)
        _invalidate_if_active(model)
    return {"palette": palette, "kind": kind, "file": f"{kind}.png",
            "sheet": list(sheet.get_size()), "layout": new_spec["layout"],
            "count": n, "mode": chosen}


def unlock_texture(model, palette, kind, write=True) -> List[Path]:
    """Remove `kind` from the palette's build.locked and regenerate that
    palette's textures. `write=False` only updates the locked list."""
    if palette not in model.palettes:
        raise OpError(f"no palette {palette!r}")
    if kind not in TEXTURE_KINDS:
        raise OpError(f"unknown texture kind {kind!r}")
    new_pal = copy.deepcopy(model._palettes[palette])
    locked = new_pal.setdefault("build", {}).setdefault("locked", [])
    removed = False
    if kind in locked:
        locked.remove(kind)
        removed = True
        ops.set_palette_data(model, palette, new_pal)
    if removed and write:
        return rebuild_textures(model, [palette])
    return []


def export_texture(model, palette, kind, out_path) -> Path:
    """Copy the palette's current <kind>.png sheet out (for external pixel
    editors)."""
    if kind not in TEXTURE_KINDS:
        raise OpError(f"unknown texture kind {kind!r}")
    src = model.project.textures_dir / palette / f"{kind}.png"
    if not src.exists():
        raise OpError(f"no {kind} texture for palette {palette!r} at {src}")
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(src), str(out))
    return out


def export_texture_tile(model, palette, kind, out_path, frame=0, mask=15
                        ) -> Path:
    """Save one tile frame of a palette texture sheet as a PNG."""
    if palette not in model.palettes:
        raise OpError(f"no palette {palette!r}")
    if kind not in TEXTURE_KINDS:
        raise OpError(f"unknown texture kind {kind!r}")
    src = model.project.textures_dir / palette / f"{kind}.png"
    if not src.exists():
        raise OpError(f"no {kind} texture for palette {palette!r} at {src}")
    ensure_pygame(headless=True)
    pal = model._palettes[palette]
    tile_px = int(pal.get("tile_px", 48))
    spec = (pal.get("textures") or {}).get(kind) or {}
    img = pygame.image.load(str(src))
    if spec.get("layout") == "autotile16":
        overhang = int(spec.get("overhang_px", 0))
        x = int(frame) * tile_px
        y = max(0, min(15, int(mask))) * (tile_px + overhang)
        rect = (x, y, tile_px, tile_px + overhang)
    else:
        count = max(1, int(spec.get("variants", spec.get(
            "frames", spec.get("stages", 1)))))
        col = int(frame) % count
        rect = (col * tile_px, 0, tile_px, tile_px)
    tile = img.subsurface(rect)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pygame.image.save(tile, str(out))
    return out


def create_palette_assets(model, name, base="default") -> List[Path]:
    """Generate textures for a freshly created palette.

    Call after ops.create_palette. When the base palette's generator is
    "imported", the base's textures directory is copied instead of a
    procedural rebuild.
    """
    if name not in model.palettes:
        raise OpError(f"no palette {name!r}")
    base_pal = model._palettes.get(base)
    if (base_pal is not None
            and base_pal.get("build", {}).get("generator") == "imported"):
        src = model.project.textures_dir / base
        dst = model.project.textures_dir / name
        if src.exists():
            shutil.copytree(str(src), str(dst))
            _invalidate_if_active(model)
            return [dst]
    return rebuild_textures(model, [name])


def build_sprites(model, names: Optional[list] = None,
                  write: bool = True) -> List[Path]:
    """Write sprite source JSONs from the model, then build their sheets.

    `names` defaults to every sprite in the model. Immediate file
    operation, not part of undo."""
    ensure_pygame(headless=True)
    from tools.build_sprites import build_sprite

    if names is None:
        names = sorted(model.sprites)
    else:
        names = list(names)
    for name in names:
        if name not in model.sprites:
            raise OpError(f"no sprite {name!r}")
    written: List[Path] = []
    if not write:
        return written
    for name in names:
        dest = model.project.sprite_src_dir / f"{name}.json"
        model._atomic_json_write(
            dest, json.dumps(model._sprites[name], indent=2) + "\n")
        model.mark_sprite_saved(name)
        written.extend(build_sprite(dest, model.project.sprites_dir))
    _invalidate_if_active(model)
    return written
