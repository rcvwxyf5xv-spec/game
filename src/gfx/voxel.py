"""
Core voxel renderers for the late-90s voxel look.

Two renderers:

  render_relief  - heightfield relief maps (used for rock tiles + backdrops).
                   Each column of the heightmap becomes a stack of voxel
                   cubes with classic cube-edge idle shading (1px bright on
                   exposed top/left edges, 1px dark on exposed bottom/right).

  render_model   - full orthographic 3/4-projected VoxelModel sprites
                   (player, pickups, spring pads). Painter's algorithm,
                   per-voxel cube-face shading, rim light, and a darker
                   1px silhouette outline so sprites read as period-correct
                   pre-rendered art.

All math is stdlib-only (math + pygame draws). No numpy.
"""

from __future__ import annotations

import math

import pygame

# Projection constants -------------------------------------------------------
# Default light: from behind/left/above (negative x,y ; positive z).


def _normalize(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


DEFAULT_LIGHT = _normalize((-0.6, -0.8, 0.7))
AMBIENT = 0.32
RIM_AMOUNT = 0.30


def _brightness_for_normal(nx, ny, nz, light, ambient=AMBIENT, rim_strength=RIM_AMOUNT):
    """Lambert + ambient + a hint of rim light around the silhouette."""
    lambert = max(0.0, nx * light[0] + ny * light[1] + nz * light[2])
    # rim: light the faces pointing away from the viewer
    view_nudge = 1.0 - nz  # flat tops get no rim, steep sides do
    rim = rim_strength * view_nudge * view_nudge
    return min(1.0, ambient + (1.0 - ambient) * lambert + rim * 0.5)


# ---------------------------------------------------------------------------
# Heightfield relief rendering
# ---------------------------------------------------------------------------


def render_relief(heightmap, color_fn, voxel_px, light=DEFAULT_LIGHT):
    """Render a 2D heightmap ([y][x] of integer voxel heights, 0 = air) as
    a field of chunky raised voxel cubes.

    Geometry: grid cell (x, y) owns the pixel square
    [x*voxel_px, (x+1)*voxel_px) x [y*voxel_px, (y+1)*voxel_px) and a
    column of height h rises UP from the bottom of that square, so h=1
    fills the square exactly and h>1 pokes (h-1) layers above it. The
    tallest column in the map anchors the surface's top; callers that need
    a fixed 48px footprint (tiles) pass heightmaps whose max height is the
    design height and blit with a negative y-offset (see tiles.py).

    Cubes are emphasized with 1px classic voxel edges: bright top/left
    where the neighbor column is shorter, dark bottom/right where the
    neighbor is shorter (bigger step = stronger).

    color_fn(x, y, h, shade) -> rgb   (shade in 0..1, quantized by caller)
    Returns an SRCALPHA surface."""
    height = len(heightmap)
    if height == 0:
        return pygame.Surface((0, 0), pygame.SRCALPHA)
    width = len(heightmap[0])
    max_h = max(1, max(max(row) for row in heightmap))
    vp = max(1, int(voxel_px))
    shift = (max_h - 1) * vp  # room above the first slot for raised columns
    surf = pygame.Surface((width * vp, (height + max_h - 1) * vp), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))

    def shade_at(x, y, h):
        nw = heightmap[y - 1][x] if y > 0 else h
        ns = heightmap[y + 1][x] if y < height - 1 else h
        n_e = heightmap[y][x + 1] if x < width - 1 else h
        n_w = heightmap[y][x - 1] if x > 0 else h
        dhdx = (n_e - n_w)
        dhdy = (ns - nw)
        nx, ny, nz = _normalize((-dhdx, -dhdy, 1.0))
        return _brightness_for_normal(nx, ny, nz, light)

    for y in range(height):
        for x in range(width):
            h = heightmap[y][x]
            if h <= 0:
                continue
            shade = shade_at(x, y, h)
            color = color_fn(x, y, h, shade)
            px = x * vp
            py = (y + 1 - h) * vp + shift  # stack top
            surf.fill(color, (px, py, vp, h * vp))

            h_north = heightmap[y - 1][x] if y > 0 else 0
            h_west = heightmap[y][x - 1] if x > 0 else 0
            h_south = heightmap[y + 1][x] if y < height - 1 else 0
            h_east = heightmap[y][x + 1] if x < width - 1 else 0

            # ---------------------------------------------------------------
            # Per-voxel cube edges: step faces exposed where a neighbor
            # column is shorter than this one.
            # ---------------------------------------------------------------

            def _light_edge(strength):
                k = min(strength, 4)
                return (
                    min(255, color[0] + int(42 + 24 * k)),
                    min(255, color[1] + int(42 + 24 * k)),
                    min(255, color[2] + int(42 + 24 * k)),
                )

            def _dark_edge(strength):
                k = min(strength, 4)
                f = 0.55 - 0.08 * k
                return (int(color[0] * f), int(color[1] * f), int(color[2] * f))

            h_w = max(0, h - h_west)
            h_e = max(0, h - h_east)
            h_n = max(0, h - h_north)
            h_s = max(0, h - h_south)

            # left / right faces (west / east neighbors shorter)
            if h_w > 0:
                surf.fill(_light_edge(h - h_west), (px, py + (h - h_w) * vp, 1, h_w * vp))
            if h_e > 0:
                surf.fill(_dark_edge(h - h_east), (px + vp - 1, py + (h - h_e) * vp, 1, h_e * vp))
            # top / bottom bands (north / south neighbors shorter)
            if h_n > 0:
                top_face_y = py + (h - h_n) * vp
                surf.fill(_light_edge(h - h_north), (px, top_face_y, vp, 1))
                # flat lit cap over the raised top voxel
                surf.fill(_light_edge(h - h_north), (px, py, vp, vp))
            if h_s > 0:
                surf.fill(_dark_edge(h - h_south), (px, py + h * vp - 1, vp, 1))

    return surf


# ---------------------------------------------------------------------------
# VoxelModel: a dict of {(x, y, z): rgb}
# ---------------------------------------------------------------------------


class VoxelModel:
    """Sparse voxel model: dict of {(x, y, z): rgb}. x right, y toward the
    viewer, z up. Helpers build primitives; render_model projects it."""

    def __init__(self):
        self.voxels = {}

    def add_voxel(self, x, y, z, color):
        self.voxels[(int(x), int(y), int(z))] = tuple(int(c) for c in color)
        return self

    def add_box(self, x0, y0, z0, w, h, d, color):
        x0, y0, z0 = int(x0), int(y0), int(z0)
        for z in range(z0, z0 + d):
            for y in range(y0, y0 + h):
                for x in range(x0, x0 + w):
                    self.voxels[(x, y, z)] = tuple(int(c) for c in color)
        return self

    def add_ellipsoid(self, cx, cy, cz, rx, ry, rz, color, jagged=1.0):
        cx, cy, cz = float(cx), float(cy), float(cz)
        color = tuple(int(c) for c in color)
        # iterate bounding box in voxel units, keep shells near the 2D
        # ellipse; jagged < 1 makes the surface chunkier/noisier
        for z in range(int(cz - rz), int(cz + rz) + 1):
            for y in range(int(cy - ry), int(cy + ry) + 1):
                for x in range(int(cx - rx), int(cx + rx) + 1):
                    ex = (x - cx) / max(1e-6, rx)
                    ey = (y - cy) / max(1e-6, ry)
                    ez = (z - cz) / max(1e-6, rz)
                    d2 = ex * ex + ey * ey + ez * ez
                    if d2 <= 1.0:
                        self.voxels[(x, y, z)] = color
        return self

    def add_cylinder(self, cx, cy, z0, radius, height, color, axis="z"):
        color = tuple(int(c) for c in color)
        r2 = radius * radius
        for z in range(z0, z0 + height):
            zr = z - z0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if dx * dx + dy * dy <= r2:
                        if axis == "z":
                            self.voxels[(int(cx + dx), int(cy + dy), z)] = color
                        elif axis == "y":
                            self.voxels[(int(cx + dx), z, int(cy + dy))] = color
                        else:
                            self.voxels[(z, int(cx + dx), int(cy + dy))] = color
        return self

    def mirror(self, axis="x"):
        """Add a mirrored copy of the model across the given axis. Keeps the
        original so left+right copies coexist (used for symmetric parts)."""
        if axis == "x":
            return VoxelModel._mirror_axis(self, lambda x, y, z: (-x, y, z))
        if axis == "y":
            return VoxelModel._mirror_axis(self, lambda x, y, z: (x, -y, z))
        return VoxelModel._mirror_axis(self, lambda x, y, z: (x, y, -z))

    @staticmethod
    def _mirror_axis(src, fn):
        out = VoxelModel()
        # keep original voxels
        out.voxels.update(src.voxels)
        for (x, y, z), color in src.voxels.items():
            mx, my, mz = fn(x, y, z)
            out.voxels[(mx, my, mz)] = color
        return out

    def bounding_box(self):
        if not self.voxels:
            return (0, 0, 0, 1, 1, 1)
        xs = [v[0] for v in self.voxels]
        ys = [v[1] for v in self.voxels]
        zs = [v[2] for v in self.voxels]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


# ---------------------------------------------------------------------------
# Sprite projection
# ---------------------------------------------------------------------------

_DIRS6 = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1))


def render_model(model, voxel_px, yaw_deg=30, pitch_deg=20, light=DEFAULT_LIGHT):
    """Project a VoxelModel to an SRCALPHA sprite.

    Orthographic 3/4 projection (yaw about z, then pitch). Back-to-front
    painter's algorithm; per-voxel cube-face shading from empty 6-neighbors
    (fully hidden voxels are skipped); Lambert + ambient + rim light. Final
    pass darkens a 1px silhouette outline (period sprite look).

    Returns (surface, anchor) where anchor = (ax, ay) pixel position in the
    surface of the model's feet bottom-center."""
    vp = max(1, int(voxel_px))
    cos_y, sin_y = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    cos_p, sin_p = math.cos(math.radians(pitch_deg)), math.sin(math.radians(pitch_deg))
    light = _normalize(light)

    if not model.voxels:
        return pygame.Surface((1, 1), pygame.SRCALPHA), (0, 0)

    # project voxel centers to pixel positions
    projected = []
    for (x, y, z), color in model.voxels.items():
        xr = x * cos_y - y * sin_y
        yr = x * sin_y + y * cos_y
        sx = xr
        sy = yr * sin_p - z * cos_p
        projected.append((sx, sy, x, y, z, color))

    pad = vp + 1
    min_px_x = min(p[0] for p in projected) * vp - pad
    max_px_x = max(p[0] for p in projected) * vp + pad
    min_px_y = min(p[1] for p in projected) * vp - pad
    max_px_y = max(p[1] for p in projected) * vp + pad
    w = int(round(max_px_x - min_px_x)) + 1
    h = int(round(max_px_y - min_px_y)) + 1
    if w <= 0 or h <= 0:
        return pygame.Surface((1, 1), pygame.SRCALPHA), (0, 0)
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill((0, 0, 0, 0))

    # depth sort: farther (larger depth along the view axis) drawn first
    vx, vy, vz = sin_y * cos_p, cos_y * cos_p, sin_p
    projected.sort(key=lambda p: p[2] * vx + p[3] * vy + p[4] * vz, reverse=True)

    one_plus = vp + 1  # squares slightly larger than the voxel to avoid holes

    model_voxels = model.voxels

    for sx, sy2, x, y, z, color in projected:
        # Cull fully hidden voxels (all 6 neighbors present)
        hidden = True
        for dx, dy, dz in _DIRS6:
            if (x + dx, y + dy, z + dz) not in model_voxels:
                hidden = False
                break
        if hidden:
            continue

        # face normal from empty neighbors
        nx = ny = nz = 0.0
        for dx, dy, dz in _DIRS6:
            if (x + dx, y + dy, z + dz) not in model_voxels:
                nx += dx / 6.0
                ny += dy / 6.0
                nz += dz / 6.0
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length > 1e-9:
            nx, ny, nz = nx / length, ny / length, nz / length

        bright = _brightness_for_normal(nx, ny, nz, light)
        # tinted by base color
        r = min(255, int(color[0] * bright))
        g = min(255, int(color[1] * bright))
        b = min(255, int(color[2] * bright))

        px = int(round(sx * vp)) - int(round(min_px_x))
        py = int(round(sy2 * vp)) - int(round(min_px_y))
        surf.fill((r, g, b, 255), (px, py, one_plus, one_plus))

    # 1px outline around the silhouette
    outline(surf, (10, 8, 10), 255)

    # anchor: bottom-center in cropped coordinates
    cx_proj = 0.0
    cy_proj = 0.0
    count = 0
    max_sy = -1e18
    for sx, sy2, x, y, z, color in projected:
        cx_proj += sx
        max_sy = max(max_sy, sy2)
        count += 1
    ax = int(round(cx_proj / count * vp)) - int(round(min_px_x))
    ay = int(round(max_sy * vp)) - int(round(min_px_y))
    return surf, (ax, ay)


def outline(surf, color, alpha=255):
    """Dark 1px outline around the alpha silhouette of an SRCALPHA surface."""
    w, h = surf.get_size()
    if w < 2 or h < 2:
        return
    mask = pygame.mask.from_surface(surf)
    for y in range(h):
        for x in range(w):
            if mask.get_at((x, y)):
                continue
            # empty pixel bordering a filled one -> outside edge
            if (x > 0 and mask.get_at((x - 1, y))) or \
               (x < w - 1 and mask.get_at((x + 1, y))) or \
               (y > 0 and mask.get_at((x, y - 1))) or \
               (y < h - 1 and mask.get_at((x, y + 1))):
                surf.set_at((x, y), (color[0], color[1], color[2], alpha))
