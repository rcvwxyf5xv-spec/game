# Metroidvania Map Demo

A minimal Super Metroid-style engine skeleton built around a grid-addressed,
file-backed world. Graphics are plain polygons (no art assets) -- this is a
proof of the map/physics architecture, not a finished game.

## Glossary

- **Cell** = one square of the WORLD MAP grid, addressed (cx, cy); fixed
  16x10 tiles; one JSON file. Never use "cell" for anything else.
- **Tile** = one 16x16 game-pixel square INSIDE a cell (kind + palette).
  Never use "tile" for map squares.
- **Room** = runtime group of cells joined by open edges (computed, never
  authored).
- **Edge** = one side of a cell (wall / open / door). **Door** = an edge
  that links to another cell's edge.
- **Frame** = one sub-image of a texture sheet or sprite sheet (NOT "cell").
- **Entity** = object placed at tile coords inside a cell. **Zone** =
  cosmetic area label (visual theme, title card). **Palette** = named tile
  texture set. **Project** = one complete game's data folder.

Coordinates: cell coords (cx, cy) on the world grid, y down, (0,0) = map
centre; tile coords (tx, ty) local to a cell (0..15, 0..9) or room-local
in an assembled room. Units: 1 tile = 16 game px, SCALE 3 -> 1 game px =
3 screen px.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pygame
```

(A `.venv` with pygame already installed is included in this directory.)

## Run the playable demo

```bash
source .venv/bin/activate
python -m src.main
```

Runs another project (any folder with a project.json):

```bash
python -m src.main --project path/to/project
```

Controls: Left/Right or A/D to move, Space/Up/W to jump, Esc to quit.

Walk off the right edge of the starting cell to reach `(1, 0)`, or off the
left edge to reach the L-shaped playspace spanning `(-1,0)`, `(-1,1)`,
`(0,1)`. The HUD shows the current zone label, the world grid cell under
the player, every cell the currently assembled playspace occupies, and
which individual Cell objects are currently resident in memory.

## Metroidvania Maker (editor)

Metroidvania Maker (`maker/`) is a separate program for authoring games
("projects") on top of this fixed engine. Each project owns its world
data, zones, palettes, textures, sprites and settings, and can be
exported as its own playable game. It never changes the engine's source.

### GUI

```bash
.venv/bin/python -m maker                      # the demo project
.venv/bin/python -m maker --project my_game    # any project folder
```

Or double-click `Metroidvania Maker.command`.

- **Layout:**
  - Left: the live room, rendered by the game's own renderer.
  - Right: the data pane, with the tabs Inspect, JSON, Problems,
    Palettes, Zones, Sprites and Project.
- **Tools:**
  - Select (V), Paint (B), Rectangle (R), Fill (F), Erase (X),
    Eyedropper (I), Entity (N) and Edge/Door (E).
  - Keys 1–5 pick the brush kind.
  - Right-drag erases.
- **World Map (Tab):**
  - Click to select a cell; double-click to open its room.
  - N creates a cell on an empty square, Del deletes, drag moves,
    Alt-drag duplicates, and S sets the start cell.
- **Editing and saving:**
  - Cmd+Z / Shift+Cmd+Z undo and redo; a whole drag counts as one
    step.
  - Cmd+S saves.
  - F5 saves, then playtests from the hovered tile.
  - "Build Game" exports a standalone folder.
- **Palettes:** editing colours rebuilds the textures live. Import or
  export PNG art per tile kind.
- **Zones:** edit the zone colours.
- **Sprites:** edit the source JSON, then rebuild the sheet.

### CLI (for agents and scripts)

```bash
.venv/bin/python -m maker.cli info                 # project summary (JSON)
.venv/bin/python -m maker.cli validate             # rule checks
.venv/bin/python -m maker.cli cell get 0,0 --ascii # one cell as 10 lines of 16 chars
.venv/bin/python -m maker.cli render 0,0 --room --overlay grid,cells,edges,entities -o room.png
.venv/bin/python -m maker.cli --project my_game export build/MyGame   # standalone game
```

The GUI, the CLI and the Python API share one headless core
(`maker.core`), so an edit behaves the same whichever way you make it.
The core validates every edit, reports new issues and saves atomically.
The `batch` command runs a list of ops in one transaction.

[AGENTS.md](AGENTS.md) has the full command guide, examples and the
terminology glossary.

Tests: `python -m tests.test_maker_core`, `python -m tests.test_maker_cli`
and `python -m tests.test_maker_gui` (headless; the GUI test saves
screenshots to `screenshots/maker_gui_*.png`).

## Run the headless constraint checks

```bash
source .venv/bin/activate
python -m tests.test_world_constraints
```

This exercises the real engine code (no pygame window) and verifies:
- the world grid is centered on `(0, 0)`, with `(1, 0)` directly to its right
- a playspace can span multiple, oddly-arranged grid cells (the L-shaped
  group) purely because those cells' shared edges are `open` -- never
  because of any declared "these cells belong to room X" label
- flood-filling from any member cell of a group reaches the same group
- only a bounded number of `Cell` objects are ever loaded in memory at once
  (LRU eviction), regardless of how large the authored world is
- walking through doors between cell-groups, and moving seamlessly within
  a single multi-cell group, both work through the real physics/transition
  code -- including a door anchored to a cell that sits on a concave/
  internal part of its group's bounding box (not just the outer edge)

## Regenerate the sample world data

```bash
source .venv/bin/activate
python -m tools.generate_sample_world
```

This (re)writes `src/data/world_index.json` and `src/data/cells/*.json` from
the cell-building code in `tools/generate_sample_world.py`. It's a tool, not
part of the runtime -- a future level editor, mod loader, or procedural
generator would produce the same JSON shape through `src/data/serialize.py`.

## Architecture

```
src/data/        -- the "database": pure data, no engine/pygame imports
  schema.py        dataclasses: Cell, Edge, WorldIndex; direction vocabulary
  serialize.py     dataclasses <-> JSON
  world_index.json cell -> filename map (small, always fully loaded)
  cells/*.json     one file per 1x1 grid cell (tiles, 4 edges, entities)

src/engine/      -- the engine: no knowledge of JSON or specific cells
  constants.py     tile/cell sizes, physics tuning, MAX_ASSEMBLED_CELLS cap
  physics.py       tile-grid AABB collision, gravity (map-data agnostic)
  room_assembler.py flood-fills a cell's `open`-edge group into one
                    AssembledRoom; the only place "what counts as one
                    loaded room" is decided
  world_loader.py  lazy-loads Cell objects from the data layer (LRU cache),
                    delegates assembly to room_assembler
  game_world.py    glue: owns player position, drives door transitions

src/main.py      -- pygame rendering + input, the only gameplay pygame code
tools/           -- offline content-generation tooling (not imported by
                    the engine), e.g. tools/generate_sample_world.py
tests/           -- headless checks against the real engine code
```

### Why it's split this way

- **World addressing**: the world is an infinite integer grid centered on
  `(0, 0)`. `WorldIndex.cell_files` is the only thing that must describe
  the whole world, and it's tiny (just cell -> filename). Individual cells
  are loaded from their own JSON file on first use and can be evicted,
  so the authored world can grow arbitrarily large without growing memory
  use -- see `WorldLoader`'s LRU cache and the eviction log line it prints.
- **Room membership is computed, not declared**: each `Cell` only knows its
  own tiles and what's on its own 4 edges -- `wall`, `open`, or `door`
  (with a target cell/edge/door id). There is no "room name" anywhere in
  the data model. When the player enters a cell, `room_assembler.assemble()`
  flood-fills outward through every `open` edge (stopping at `wall`/`door`
  edges) and stitches the result into one `AssembledRoom`, capped at
  `MAX_ASSEMBLED_CELLS` so a very large open area can't pull the whole map
  into memory at once (for a real design that needs a single huge room, the
  plan is to slice it with an "invisible wall" -- a wall+door pair placed
  as an artificial screen transition -- rather than relaxing this cap).
  This is what makes the L-shaped playspace in the demo span 3 cells with
  no "room" concept declaring it so: it's emergent purely from edge data.
- **Doors are per-cell-edge, not per-room**: a `door` edge lists exactly
  which cell/edge it leads to. `room_assembler` anchors each door to the
  specific member cell it belongs to (`DoorCrossing.cell_rect`), not to
  the assembled room's overall bounding box -- so a door on a cell that
  sits on a concave/internal part of an oddly-shaped group's bounding box
  (like the L-room's notch) still triggers correctly.
- **Cosmetic-only zone labels**: a `Cell` may carry an optional `zone`
  string (e.g. "L-Shaped Passage") purely for HUD/music/ambience display.
  It is never read by the flood-fill/grouping logic -- only edge data
  decides what's loaded together.
- **Decoupling data from physics**: `src/engine/physics.py` only knows about
  a 2D tile array and an AABB body -- it never imports `src/data`.
  `src/engine/world_loader.py` only knows about data + caching -- it never
  imports pygame or physics. `game_world.py` is the single place that wires
  them together. This means a mod/DLC/expansion (or a procedural generator)
  can ship a different `world_index.json` + `cells/` directory -- or even
  build cells purely in memory -- and nothing in the physics or rendering
  code needs to change.

## Known simplifications (this is a base, not a finished game)

- No sprites/animation, no enemies/combat, no save system.
- Only straight east/west/south/north doors between axis-aligned rooms.
- Camera centers on the player and clamps to the current room's bounds;
  there's no cross-room scroll blending like retro Metroid's door transitions.
