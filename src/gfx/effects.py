"""
Presentation effects: the zone light map (ambient + additive light
sprites multiplied over the scene), cached glow sprites, a small world-
space particle system (ambient motes, landing dust, crumble debris,
spring sparks) and a cached vignette.
"""

from __future__ import annotations

import math
import random

import pygame

WINDOW_W = 768
WINDOW_H = 480
SCALE = 3

CALC = {}


def glow_sprite(radius, color):
    """Cached radial additive glow sprite (SRCALPHA), bright center fading
    to transparent at `radius` px."""
    key = (int(radius), tuple(color))
    cached = CALC.get(key)
    if cached is not None:
        return cached
    r = max(2, int(radius))
    size = r * 2
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = r
    for y in range(size):
        for x in range(size):
            d = math.hypot(x - cx, y - cy) / float(r)
            if d >= 1.0:
                continue
            a = int((1.0 - d) ** 2 * 255)
            surf.set_at((x, y), (color[0], color[1], color[2], a))
    CALC[key] = surf
    return surf


class LightMap:
    """Per-frame ambient light surface. Begin each frame by refilling with
    the zone's ambient color, add light sources additively, then blit onto
    the scene with BLEND_RGB_MULT. The 768x480 surface is allocated once."""

    def __init__(self):
        self.surf = pygame.Surface((WINDOW_W, WINDOW_H))
        self._lights = []

    def begin(self, ambient_color):
        self.surf.fill(ambient_color)

    def add_light(self, x, y, radius, color):
        self._lights.append((x, y, radius, color))

    def _flush(self):
        for x, y, radius, color in self._lights:
            sprite = glow_sprite(radius, color)
            self.surf.blit(sprite, (int(x - radius), int(y - radius)),
                           special_flags=pygame.BLEND_RGB_ADD)
        self._lights = []

    def apply(self, screen):
        if self._lights:
            self._flush()
        screen.blit(self.surf, (0, 0), special_flags=pygame.BLEND_RGB_MULT)


class Particle:
    __slots__ = ("x", "y", "vx", "vy", "age", "life", "size", "color",
                 "gravity", "kind")

    def __init__(self, x, y, vx, vy, life, size, color, gravity=0.0, kind="dust"):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.age = 0.0
        self.life = life
        self.size = size
        self.color = color
        self.gravity = gravity
        self.kind = kind


class ParticleSystem:
    """Simple world-space particle list. Coordinates are world pixels."""

    def __init__(self, rng_seed=0):
        self.particles = []
        self.rng = random.Random(rng_seed)
        self._mote_timer = 0.0

    def update(self, dt_frames=1.0):
        alive = []
        for p in self.particles:
            p.age += dt_frames
            p.x += p.vx * dt_frames
            p.y += p.vy * dt_frames
            if p.gravity:
                p.vy += p.gravity * dt_frames
            if p.age < p.life:
                alive.append(p)
        self.particles = alive

    def spawn_motes(self, zone, view_rect_world, count_max):
        """Ambient drifting zone motes (dust / spores / embers)."""
        self._mote_timer -= 1.0
        if self._mote_timer > 0 or len(self.particles) > count_max:
            return
        self._mote_timer = 3.0
        pal = {"Blue Brinstar": (150, 210, 255), "Green Brinstar": (140, 255, 150),
               "Pink Brinstar": (255, 170, 230), "Red Brinstar": (255, 140, 80),
               "Kraid's Lair": (220, 200, 120)}
        color = pal.get(zone, (200, 200, 220))
        vx, vy, vw, vh = view_rect_world
        x = vx + self.rng.uniform(0, vw)
        y = vy + self.rng.uniform(0, vh)
        drift_slow = (zone == "Green Brinstar")
        vy0 = self.rng.uniform(-0.25, 0.12)
        vx0 = self.rng.uniform(-0.2, 0.35) if drift_slow else self.rng.uniform(-0.05, 0.05)
        size = self.rng.uniform(1.0, 2.2)
        blink = self.rng.random() < 0.25
        kind = "ember" if zone == "Red Brinstar" else ("snow" if zone == "Blue Brinstar" else "mote")
        life = self.rng.uniform(60, 200)
        self.particles.append(Particle(x, y, vx0, vy0, life, size, color, kind=kind))

    def spawn_dust(self, x, y, n=8):
        for _ in range(n):
            self.particles.append(Particle(
                x + self.rng.uniform(-12, 12), y + self.rng.uniform(-4, 2),
                self.rng.uniform(-1.4, 1.4), self.rng.uniform(-0.6, -0.1),
                self.rng.uniform(15, 30), self.rng.uniform(1.5, 3.5),
                (170, 150, 120), gravity=0.02, kind="dust"))

    def spawn_debris(self, x, y, zone):
        """Voxel chunks with gravity when a crumble block collapses. x/y
        are the collapse center in world game px."""
        pal = {"Blue Brinstar": (96, 130, 176), "Green Brinstar": (116, 172, 126),
               "Pink Brinstar": (170, 82, 144), "Red Brinstar": (196, 80, 52),
               "Kraid's Lair": (158, 136, 56)}
        color = pal.get(zone, (120, 120, 140))
        for _ in range(10):
            self.particles.append(Particle(
                x, y,
                self.rng.uniform(-4.5, 4.5), self.rng.uniform(-6.0, -1.0),
                self.rng.uniform(25, 55), self.rng.uniform(2.0, 4.0),
                color, gravity=0.32, kind="debris"))

    def spawn_sparks(self, x, y):
        for _ in range(10):
            ang = self.rng.uniform(0, 2 * math.pi)
            speed = self.rng.uniform(1.5, 4.5)
            self.particles.append(Particle(
                x, y, math.cos(ang) * speed, math.sin(ang) * speed - 1.0,
                self.rng.uniform(12, 26), self.rng.uniform(1.0, 2.2),
                (80, 255, 150), gravity=0.08, kind="spark"))

    def draw(self, screen, cam_x, cam_y, tick=0):
        """cam_x/cam_y are in game pixels (world px); particles are stored
        in game-px world coordinates."""
        for p in self.particles:
            sx = (p.x - cam_x) * SCALE
            sy = (p.y - cam_y) * SCALE
            if sx < -20 or sy < -20 or sx > WINDOW_W + 20 or sy > WINDOW_H + 20:
                continue
            fade = 1.0 - p.age / max(1.0, p.life)
            alpha = int(255 * fade)
            base = p.color
            if p.kind == "ember":
                fl = 0.6 + 0.4 * math.sin(p.age * (6.0 + p.size * 3))
                base = tuple(min(255, int(c * fl)) for c in base)
            size = max(1, int(p.size * fade))
            if p.kind == "debris":
                r = (base[0], base[1], base[2], alpha)
                pygame.draw.rect(screen, r, (int(sx), int(sy), size + 1, size + 1))
                pygame.draw.line(screen, (15, 15, 18),
                                 (int(sx), int(sy)), (int(sx) + size, int(sy) + size))
            else:
                col = (base[0], base[1], base[2], alpha)
                pygame.draw.circle(screen, col, (int(sx), int(sy)), size)


_VIGNETTE = None


def get_vignette():
    """Cached 768x480 SRCALPHA darkening at the screen edges."""
    global _VIGNETTE
    if _VIGNETTE is not None:
        return _VIGNETTE
    surf = pygame.Surface((WINDOW_W, WINDOW_H), pygame.SRCALPHA)
    cx, cy = WINDOW_W / 2.0, WINDOW_H / 2.0
    rmax = math.hypot(cx, cy)
    for y in range(WINDOW_H):
        for x in range(WINDOW_W):
            d = math.hypot(x - cx, y - cy) / rmax
            if d < 0.55:
                continue
            a = int((d - 0.55) / 0.45 * 120)
            surf.set_at((x, y), (0, 0, 0, min(160, a)))
    _VIGNETTE = surf
    return surf
