"""Flat 2D side-view pixel art rendered through the voxel relief renderer.

Each art pixel of an ASCII grid becomes a chunky voxel_px x voxel_px block
beveled exactly like the rock tiles (render_relief), so sprites and tiles
share the same late-90s surface look. Optionally adds a 1-art-pixel dark
outline around the opaque silhouette for readability.

Deterministic: no RNG, no hash randomization is involved.
"""

from __future__ import annotations

import pygame

from src.gfx.voxel import render_relief


def render_pixel_art(rows, colors, heights=None, voxel_px=3, outline=None):
    """Render an ASCII pixel-art grid to an SRCALPHA surface.

    rows: list of equal-length strings; '.' is transparent and any other
      char must be a key of colors.
    colors: dict char -> (r, g, b).
    heights: optional dict char -> int voxel height (default 1; 2 for
      raised details like rivets, sparingly).
    voxel_px: screen pixels per art pixel (default 3).
    outline: optional (r, g, b); draws a 1-art-pixel outline outside the
      silhouette and grows the surface by voxel_px on every side.

    The bottom row of art aligns with the surface bottom (voxel_px above
    it when an outline is drawn). Width = len(row)*voxel_px (+2*voxel_px
    when outlined). Height = (nrows + max_height - 1)*voxel_px (+2*voxel_px
    when outlined); callers that need a fixed footprint crop the raised
    overhang.
    """
    vp = max(1, int(voxel_px))
    if not rows:
        return pygame.Surface((0, 0), pygame.SRCALPHA)
    widths = {len(r) for r in rows}
    if len(widths) != 1:
        raise ValueError("pixel art rows must all be the same length")
    grid_w = widths.pop()
    grid_h = len(rows)
    heights = heights or {}

    unknown = sorted({ch for row in rows for ch in row if ch not in colors and ch != "."})
    if unknown:
        raise ValueError(f"unknown color chars in pixel art: {unknown}")
    if any(h < 1 for h in heights.values()):
        raise ValueError("heights must be >= 1")

    hmap = []
    for row in rows:
        hmap.append([heights.get(ch, 1) if ch != "." else 0 for ch in row])

    def color_fn(x, y, h, shade):
        return colors[rows[y][x]]

    art = render_relief(hmap, color_fn, vp)

    if outline is None:
        return art
    outline_rgb = tuple(int(c) for c in outline)
    aw, ah = art.get_size()
    out = pygame.Surface((aw + 2 * vp, ah + 2 * vp), pygame.SRCALPHA)
    out.fill((0, 0, 0, 0))
    # 1-art-pixel outline outside the silhouette (transparent art pixels
    # bordering an opaque one); the art blitted on top overwrites any
    # interior artifacts, leaving only the exterior ring visible.
    for y in range(ah):
        for x in range(aw):
            if art.get_at((x, y))[3] > 0:
                continue
            if (x > 0 and art.get_at((x - 1, y))[3] > 0) or \
               (x < aw - 1 and art.get_at((x + 1, y))[3] > 0) or \
               (y > 0 and art.get_at((x, y - 1))[3] > 0) or \
               (y < ah - 1 and art.get_at((x, y + 1))[3] > 0):
                out.set_at((x + vp, y + vp), (*outline_rgb, 255))
    out.blit(art, (vp, vp))
    return out


def flip_x(surf):
    """Mirror a sprite horizontally."""
    return pygame.transform.flip(surf, True, False)
