"""
Global constants for the engine.

These describe *rendering/physics* scale, not map content. Map content
(room layouts, doors, world grid assignment) lives entirely under
src/data/ and is loaded at runtime by engine.world_loader.
"""

TILE_SIZE = 16  # pixels per tile

# A "grid cell" is the unit of the world grid the user described
# (the (0,0), (1,0), etc. addressing scheme). Every room occupies one or
# more grid cells. Each grid cell is a fixed number of tiles so that a
# room spanning multiple cells tiles seamlessly.
CELL_WIDTH_TILES = 16
CELL_HEIGHT_TILES = 10

CELL_WIDTH_PX = CELL_WIDTH_TILES * TILE_SIZE
CELL_HEIGHT_PX = CELL_HEIGHT_TILES * TILE_SIZE

SCREEN_WIDTH = CELL_WIDTH_PX
SCREEN_HEIGHT = CELL_HEIGHT_PX
FPS = 60

# Tile type ids used by the cell data files.
TILE_EMPTY = 0
TILE_SOLID = 1
TILE_SPRING = 2
TILE_ONEWAY_PLAT = 3
TILE_CRUMBLE = 4

# Map from the data layer's tile *kind* strings (src.data.schema) to the
# integer ids the physics code consumes. Kept here with plain string
# literals so this module stays standalone (no import from src.data).
TILE_KIND_IDS = {
    "empty": TILE_EMPTY,
    "solid": TILE_SOLID,
    "spring": TILE_SPRING,
    "oneway": TILE_ONEWAY_PLAT,
    "crumble": TILE_CRUMBLE,
}

# Physics tuning
GRAVITY = 0.45
MAX_FALL_SPEED = 9.0
MOVE_SPEED = 2.6
JUMP_SPEED = -8.2
SPRING_BOUNCE_SPEED = -10.5
CRUMBLE_DELAY_FRAMES = 15
CRUMBLE_RESPAWN_FRAMES = 180

# Safety cap for engine.room_assembler's flood fill: the maximum number
# of cells that can be merged into one loaded, contiguous playspace via
# `open` edges. Keeps a careless or procedurally-generated open area from
# loading the whole map; oversized areas should be sliced with a
# wall+door pair instead (an "invisible wall" screen transition).
MAX_ASSEMBLED_CELLS = 12

# Directions and edge vocabulary live in src.data.schema (the data layer
# that both the data files and this engine depend on), not here, to
# avoid defining them twice.
