"""
Project-aware data root.

A Project is one complete game's data folder; the engine stays fixed and
reads world, zones, palettes, textures and sprites from the active
project. The default project is the in-repo demo (project.json at the
engine root) unless METROIDVANIA_PROJECT points at another folder.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_FILE = "project.json"
FORMAT_VERSION = 1
ENGINE_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_LAYOUT = {
    "world_index": "world/world_index.json",
    "cells_dir": "world/cells",
    "palettes_dir": "palettes",
    "textures_dir": "textures",
    "sprite_src_dir": "sprite_src",
    "sprites_dir": "sprites",
    "zones": "zones.json",
}


@dataclass
class Project:
    """One game's data folder: root directory + parsed project.json."""

    root: Path
    manifest: dict

    @property
    def name(self) -> str:
        """Short project id (byline for the manifest)."""
        return self.manifest["name"]

    @property
    def title(self) -> str:
        """Display title, defaulting to the project name."""
        return self.manifest.get("title", self.name)

    def path(self, key: str) -> Path:
        """Resolve one layout key; falls back to the default layout."""
        return self.root / self.manifest.get("paths", {}).get(key, DEFAULT_LAYOUT[key])

    @property
    def world_index_path(self) -> Path:
        return self.path("world_index")

    @property
    def cells_dir(self) -> Path:
        return self.path("cells_dir")

    @property
    def palettes_dir(self) -> Path:
        return self.path("palettes_dir")

    @property
    def textures_dir(self) -> Path:
        return self.path("textures_dir")

    @property
    def sprite_src_dir(self) -> Path:
        return self.path("sprite_src_dir")

    @property
    def sprites_dir(self) -> Path:
        return self.path("sprites_dir")

    @property
    def zones_path(self) -> Path:
        return self.path("zones")

    @classmethod
    def load(cls, root: Path) -> "Project":
        """Read root/project.json; raises FileNotFoundError when missing
        and ValueError when the format is newer than this engine."""
        root = Path(root)
        path = root / PROJECT_FILE
        if not path.exists():
            raise FileNotFoundError(
                f"no project file at {path} (expected {PROJECT_FILE})")
        manifest = json.loads(path.read_text())
        fmt = manifest.get("format", 0)
        if fmt > FORMAT_VERSION:
            raise ValueError(
                f"{path} uses project format {fmt}, this engine only "
                f"supports format <= {FORMAT_VERSION}")
        return cls(root, manifest)

    def save_manifest(self) -> None:
        """Write project.json back to disk (pretty-printed)."""
        path = self.root / PROJECT_FILE
        path.write_text(json.dumps(self.manifest, indent=2) + "\n")


_ACTIVE = None


def set_active(project: Project) -> None:
    """Make `project` the active project for the whole process."""
    global _ACTIVE
    _ACTIVE = project


def active() -> Project:
    """The active project: set_active() if used, else METROIDVANIA_PROJECT
    env var (a directory), else the engine-root demo project."""
    global _ACTIVE
    if _ACTIVE is None:
        env = os.environ.get("METROIDVANIA_PROJECT")
        _ACTIVE = Project.load(env) if env else Project.load(ENGINE_ROOT)
    return _ACTIVE


_ZONE_CACHE = {}
_ZONE_OVERRIDE = None


def set_zone_override(zones) -> None:
    """Preview zones for the active project until set_zone_override(None).

    `zones` (dict name -> zone data) shadows zones.json in load_zones() so
    renders see unsaved zone edits. A shallow copy is stored on every call
    (even for identical content) so get_palette, which keys its conversion
    cache by id(load_zones()), notices the new object and drops its cache.
    """
    global _ZONE_OVERRIDE
    _ZONE_OVERRIDE = None if zones is None else dict(zones)


def load_zones(project: "Project" = None) -> dict:
    """Zone palette JSON as a dict ({} when missing), cached per
    (path, mtime) so an editor can rewrite it at runtime. A zone override
    set via set_zone_override() wins while it is set (for the active
    project only -- a specific non-active project ignores it)."""
    project = project or active()
    if _ZONE_OVERRIDE is not None and project is active():
        return _ZONE_OVERRIDE
    path = project.zones_path
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns)
    cached = _ZONE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    _ZONE_CACHE[key] = data
    return data


def invalidate() -> None:
    """Drop the zone cache and all src.gfx render caches so edited
    palettes/zones/textures are picked up at runtime."""
    _ZONE_CACHE.clear()
    from src.gfx import caches
    caches.clear_all()
