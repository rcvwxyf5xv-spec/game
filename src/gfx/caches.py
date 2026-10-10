"""
Module-level render cache reset.

src.project.invalidate() routes here so an editor can pick up edited
palettes, zones and textures at runtime: every module-level cache in
src.gfx (memo dicts, lru-cached singletons) is dropped so the next
render regenerates from disk.
"""


def clear_all() -> None:
    """Clear every module-level render cache in src.gfx."""
    from src.gfx import (  # noqa: F401  (imported for side effects)
        backdrop,
        effects,
        palette,
        sprites,
        sprite_registry,
        texture_registry,
        tiles,
    )
    backdrop._CACHE.clear()
    effects.CALC.clear()
    effects._VIGNETTE = None
    palette._CONVERTED.clear()
    palette._ZONES_ID = None
    sprites._CACHE.clear()
    sprite_registry._DEFAULT = None
    texture_registry._DEFAULT = None
    tiles._CACHE.clear()
