# Metroidvania Maker — Architecture

Two layers, one data contract:

- **The fixed engine** (`src/`) plays a "project": it reads world,
  zones, palettes, textures and sprites from the active project folder
  and never knows anything else. Content changes never touch `src/`.
- **Metroidvania Maker** (`maker/`) edits project folders. Its headless
  core (`maker.core`) is shared by the editor window (`maker/gui`), the
  JSON command line (`maker.cli`) and the Python API, so every edit is
  an undoable, validated transaction no matter who makes it.

A project is a folder with `project.json` + `world/` + `palettes/` +
`textures/` + `sprite_src/` + `sprites/` + `zones.json` (see "Data
formats"). The engine finds a project via `src/project.py`; the editor
remembers the last opened one via `maker/state.py`.

---

## 1. The fixed engine (`src/`)

### src/data/ — the "database" (pure data, no pygame/physics)

- **schema.py** — plain dataclasses for the world. `WorldIndex`
  (cell -> file map + start cell), `Cell` (coord, 16x10 tiles, 4 edges,
  zone label, entities, background_color), `Edge` (wall/open/door with
  door_id + target), `Tile` (kind + palette). Direction vocabulary
  (north/south/east/west, `DIRECTION_DELTA`, `OPPOSITE_DIRECTION`) and
  tile kinds (`empty`, `solid`, `spring`, `oneway`, `crumble`) live
  here too, plus legacy integer tile ids. No loading, no caching, no
  flood-fill.
- **serialize.py** — dataclasses <-> JSON: `cell_to_dict`/
  `cell_from_dict`, `world_index_to_dict`/`from_dict`, and writers
  (`save_cell`, `save_world_index`, `load_cell`, `load_world_index`).
  `save_cell` writes each tile row compactly on one line so a cell file
  stays small; output is deterministic.

### src/engine/ — the engine (no JSON knowledge)

- **constants.py** — rendering/physics scale: `TILE_SIZE`=16 px,
  16x10 tiles per cell, the play window is 768x480 screen px at SCALE=3
  (= 256x160 game px, exactly one cell visible), FPS 60, tile kind -> id
  map, physics tuning (gravity, move/jump speeds, crumble timings, double
  jump elsewhere), and `MAX_ASSEMBLED_CELLS`=12 (flood-fill cap for one
  room).
- **world_loader.py** — lazy loading: parses `world_index.json`, loads
  individual cells with an LRU cache (`cache_size`, eviction log line),
  and exposes `assemble_from(coord)` as the single entry point that
  turns "player is at this cell" into a playable room. Key functions:
  `get_cell`, `loaded_cell_coords`, `start_assembled_room`.
- **room_assembler.py** — **the one place "what counts as one room" is
  decided**, purely from edge data: `assemble()` flood-fills outward
  through `open` edges (stopping at wall/door), stitches cells into an
  `AssembledRoom` (tile grid, parallel per-tile palette labels, derived
  `default_palette`, `door_crossings`, entities, zone, background color,
  `truncated` flag), and anchors each door to its owning cell rect
  (`DoorCrossing.cell_rect`) so doors on concave parts of a group work.
  Helpers: `room_origin_px` (world-pixel top-left, shared across rooms,
  what makes the camera slide between rooms possible), `local_tile_offset`.
- **physics.py** — pure tile-grid AABB collision (no rooms/doors/pygame):
  `AABBBody` (x, y, w, h, vx, vy, on_ground), `move_and_collide`,
  `apply_gravity`, spring bounce, one-way platforms (blocks only when
  falling), and `CrumbleTracker` (delay then collapse, then respawn).
- **game_world.py** — glue: owns the player body + room-local position,
  detects door crossings per owning-cell (`_crossed`, `>=` leading edge),
  reassembles from the door target and repositions the player inside the
  target door's rect (`_transition_through`), returns a `TransitionEvent`
  for presentation. Also double jump (`max_jumps=2`), crumble tracking,
  `current_world_cell()` for the HUD.

### src/gfx/ — presentation

- **palette.py** — per-ZONE colour data: rock ramp, cap ramp, accent,
  fog gradient, ambient (built-in defaults; applies zones the cells use).
  `get_palette`, `ramp_color`, `dither_pick` (quantization) — zones
  override these defaults via `src.project.load_zones`.
- **voxel.py** — the two core renderers for the late-90s look:
  `render_relief` (heightfield -> voxel cubes with edge shading) and
  `VoxelModel`/`render_model` (sprite models). Deterministic.
- **tiles.py** — build-time voxel tile renderers (rock with autotile
  masks, springs, one-way planks, crumble stages). Build-time only; the
  runtime loads the pre-built sheets.
- **pixelart.py** — flat 2D ASCII art upscaled through the voxel relief
  renderer so sprites match tiles (chunky `voxel_px` blocks).
- **texture_registry.py** — runtime loader/cache for palette texture
  sheets (`default_registry()`, `get()` returns surface + y-offset).
- **sprite_registry.py** — runtime loader/cache for sprite sheets
  (`default_sprites().get_frame(...)`, anchor-aware, mirrored frames,
  magenta placeholder on missing).
- **sprites.py** — voxel item-pickup sprites (12 yaw angles,
  `get_item_frames`).
- **backdrop.py** — parallax cave backdrops + fog gradients per zone.
- **effects.py** — `LightMap` (per-frame ambient surface; `begin(zone
  ambient)` fills, `add_light`/`apply` multiply over the scene — used
  ONLY by authored `ambient_light`/`light` entities), cached glow
  sprites, and a small world-space particle system (ambient zone motes,
  landing dust, crumble debris, spring sparks).
- **caches.py** — `clear_all()` drops every module-level render cache so
  the editor can pick up edited palettes/zones/textures at runtime.

### src/project.py — project resolution and data root

- `DEFAULT_LAYOUT` — mapping of logical keys (world_index, cells_dir,
  palettes_dir, textures_dir, sprite_src_dir, sprites_dir, zones) to
  relative paths; a manifest `"paths"` dict may override any key.
- `Project` — root + parsed `project.json` (`name`, `title`,
  `path(key)`); `Project.load(root)` guards the `format` version.
- `default_project_root()` — the engine/tools default root:
  `METROIDVANIA_PROJECT` env -> <engine root>/project.json (this is how
  EXPORTED games work: the export puts its own project.json at the
  export root) -> first project under `projects/` -> `templates/starter/`
  as a last resort for tools that need a valid project.
- `set_active`/`active`, `load_zones` (cached, overridable via
  `set_zone_override` so the editor can preview unsaved zone edits),
  `invalidate()` (drops zone + gfx caches).

### src/main.py — VoxelRenderer + the game loop

- `VoxelRenderer` — reusable presentation state + the full frame
  pipeline, shared by the game and the editor renders
  (`render_frame`, `render_static`, `render_transition`,
  `apply_room_lighting`, title card, minimap, particles, debug overlays).
- Tile drawing uses pre-built texture sheets (autotiled rock by
  neighbour mask + depth shading + deterministic variant; springs
  animate; crumble shakes and collapses).
- `apply_room_lighting(screen, room, ...)` — authored-only lighting: no
  `ambient_light` entity -> room stays flat and fully lit. With one, the
  `LightMap` is filled with its colour (default zone `ambient`) and
  every `light` entity adds a glow (`radius` game px default 48,
  `color` default [255,220,160], optional `flicker`).
  `_INVISIBLE_ENTITIES = ("spawn_point", "light", "ambient_light")` are
  never drawn in-game.
- `RoomTransition` — Super Metroid-style door transition (freeze + pan
  across the shared world-pixel grid via `room_origin_px`), built from
  `GameWorld.last_transition`.
- `main()` — argparse flags `--project DIR`, `--start-cell CX,CY`,
  `--start-tile TX,TY`; key loop: input -> `GameWorld.update()` ->
  `RoomTransition` if a door was crossed, else `renderer.render_frame`.

### src/debug_window.py — in-game debug output

Separate SDL2 OS window (H in-game) showing `debug_lines(world, loader,
clock, tick)`: zone, world cell under the player, assembled room cells,
room origin/size, default palette, player position/velocity, jumps
remaining, tile palette below the player, loaded cells in memory /
cache size. Falls back to an on-screen overlay when a second window
cannot be created.

---

## 2. Metroidvania Maker (`maker/`)

### maker/core/ — the shared headless brain

- **model.py** — `ProjectModel`: in-memory mirror of one project
  (canonical raw dicts for every cell, palettes, zones, sprites,
  manifest) with dirty tracking against what is on disk. Transactions
  (`model.transaction(label)`), undo/redo, change listeners (a
  `ChangeSet`), `cell_objects` (schema.Cell cache), `assemble(coord)`
  (engine room semantics), `palette_usage`, `zone_names`, atomic `save()`
  (temp-file + `os.replace`), `invalidate()` routing when the active
  project is saved.
- **history.py** — snapshot-based transactions: `touch()` records
  pre-images; on commit post-images are captured into an `UndoStep`; an
  exception rolls back. `coalesce_since(mark)` merges a drag's many
  steps into one undo step. `ChangeSet` tells listeners what changed.
- **ops.py** — every edit the editor can do, one function per op,
  registered in `OPS` by name (each wrapped in a transaction of its own
  name). 36 ops: cells (new/delete/move/duplicate/set/props/ascii),
  tiles (set/rect/fill/flood,paint_world/put_region/replace), edges and
  doors (set_edge, link_door, unlink_door with auto door ids), entities
  (add/move/update/remove; `entity_types()` = manifest list +
  `ENGINE_ENTITY_TYPES` `["spawn_point", "save_station", "light",
  "ambient_light"]` + any found in cells), zones (set/rename/delete/
  color/`set_zone_palette`/`apply_zone_palette`), palettes
  (create/set/colors/color/rename/delete; rename re-points tiles and
  zone defaults, delete needs `replace_with` when used), sprites, and
  manifest values. `set_tiles` palette semantics: explicit palette wins,
  else keep the tile's, else the cell's zone `default_palette`, else the
  cell's dominant palette.
- **validate.py** — rule checks -> sorted `Issue`s (error/warning/info):
  start cell exists, exactly one spawn point, tile-grid dimensions and
  kinds, open edges paired on both sides, doors matched back (door id,
  target exists, reverse door), oversized rooms, unknown palettes /
  `zone_palette_missing`, missing texture files, unknown entity types,
  entities in solid tiles, etc.
- **render.py** — headless pygame rendering of a project: `activate()`
  points the game renderer at the model's project (+ unsaved zones via
  `set_zone_override`), then `render_room`/`render_cell`/`render_view`
  (one 768x480 view per cell of the room bbox), `render_world_map`
  (schematic map with zone colours, walls, door dots, start marker),
  `world_map_layout`, `save_png`, `reset_caches`.
- **overlays.py** — `draw_overlays`: editor annotations on top of a
  rendered view (tile grid, cell borders/labels, edge strips and door
  labels, per-kind translucent fills, entity boxes — including a radius
  ring for `light` / labels for `ambient_light` — selection outlines,
  validation issue outlines).
- **assets.py** — immediate asset file operations (exceptions to undo,
  files are side effects): `rebuild_textures`, `import_texture`
  (single tile / strip / full autotile16 sheet; sets the palette spec
  and adds the kind to `build.locked`), `export_texture(_tile)`,
  `unlock_texture`, `create_palette_assets`, `build_sprites`.
- **project_ops.py** — whole-project operations: `create_project(dest,
  name, title, template="starter"|"copy", source=...)` (starter = one
  walled cell + spawn point, zone "Start", palette "default";
  source optional; copy = duplicate a source project), `export_game`
  (bundles `src/` minus the demo data + the project's data + play.sh /
  play.command + README.txt + `.metroidvania_export` marker; refuses
  unsaved changes or validation errors unless `force`; refuses to
  delete a non-export folder), `playtest_command` (argv for
  `python -m src.main --project ... [--start-cell ...]`).

### maker/cli.py — the JSON command line

Thin mapping onto the core. Global flags: `--project DIR`, `--dry-run`,
`--no-validate`, `--pretty`. Commands: `info`, `validate`, `ops`,
`help-agent`, `cell`, `cells`, `room`, `tiles`, `edge`, `door`,
`entity`, `start`, `zone`, `palette`, `texture`, `sprite`, `render`,
`new`, `export`, `playtest`, `batch`. Project resolution via
`maker.state.resolve_startup_project(explicit, create_missing=False)`:
env -> last opened -> first in `projects/` -> error ("no project; pass
--project DIR or run `new`"). Mutating commands validate before/after,
save the model, and print `{"ok": true, "result": ..., "issues": [...]}`;
batch runs a JSON list of ops in one transaction.

### maker/state.py — editor startup state

- `load_last_project`/`save_last_project` — `.maker_state.json`
  (git-ignored; **not** written by headless runs) remembers the last
  opened project.
- `resolve_startup_project(explicit, create_missing=True)` — the GUI
  order: `--project` -> `METROIDVANIA_PROJECT` env -> last opened ->
  first folder in `projects/` with a `project.json` -> (create_missing,
  editor only) create `projects/my_game` from the starter template. The
  starter template itself is never opened for editing.

### maker/gui/ — the editor window (pygame)

- **app.py** — the `App`: toolbar (Room/Map, Undo/Redo/Save, Playtest,
  Build Game, overlays Grid/Kinds/Edges/Entities/Issues, **Light**
  toggle — the latter switches game-lighting in the editor view),
  tool strip, tab bar, status bar; routes every edit through
  `App.run()` -> ops; model listener re-renders/rebuilds; autosave
  (120 s), playtest launching, build-game dialog (default
  `<project>/builds/<name>`), new/open project dialogs, shortcuts
  (Cmd+Z/Shift+Cmd+Z/Cmd+Y, Cmd+S, Cmd+G/K/L, F5, Tab, tool keys, 1-5).
- **roomview.py** — the live-rendered room: the room containing the
  focused cell, rendered at game SCALE through `maker.core.render`
  (the game's own renderer, `lighting` on/off), zoom/pan/overlays.
  Tools (`TOOLS`): Select (V), Paint (B), Rectangle (R), Fill (F),
  Erase (X), Eyedropper (I), Entity (N), Edge/Door (E). A drag's ops
  are coalesced into one undo step.
- **mapview.py** — the schematic world map (Tab): select/double-click
  cells, N creates, Del deletes, drag moves, Alt-drag duplicates,
  S sets the start cell; uses `render_world_map` + `world_map_layout`
  so clicks map exactly back to cells.
- **panels.py** — the data pane: Inspect (selected cell/tile/entity),
  JSON (raw, validated edit), Problems (validation issues), Palettes
  (create/rename/delete, colour editor, import/export PNGs, rebuild),
  Zones (new/rename/delete, colour editor, **"Default palette" picker +
  "Apply to zone tiles"**), Sprites (source JSON + rebuild), Project
  (settings, open/new project, build).
- **widgets.py** — a small retained-mode widget toolkit (Button, Label,
  ListView, TextInput, Dialog, PopupMenu, TabBar, Container) on plain
  pygame; `cmd_down` detects Cmd (macOS) / Ctrl (elsewhere).
- **theme.py** — colours, metrics and fonts for the GUI (one place to
  restyle).

---

## 3. Tools (`tools/`) and tests (`tests/`)

Build-time / offline tooling — never imported by the engine runtime and
never shipped in exports:

- **tools/build_textures.py** — regenerate `<palette>/solid.png`,
  `oneway.png`, `spring.png`, `crumble.png` sheets from each palette's
  JSON. Run: `SDL_VIDEODRIVER=dummy .venv/bin/python -m
  tools.build_textures [--project DIR] [palette ...]` (no names = all).
- **tools/build_sprites.py** — render each `sprite_src/<name>.json`
  (ASCII frame grids) into `sprites/<name>.png` + runtime metadata JSON.
  Same flags. `_validate` checks grid consistency.
- **tools/render_preview.py** — headless previews with the real
  `GameWorld`: one PNG per zone + feature rooms + a door-transition
  frame, into `screenshots/`. Flags: `--project DIR`.
- **tests/test_maker_core.py** — headless core checks on a copy of
  `tests/fixtures/demo_project/` (`reset_copy()` -> `.maker_tmp/
  proj_copy`): 111 cells, ops + error cases, undo/redo, edge pairing,
  ascii round trip, save+reopen, validate(), listener changes. Prints
  `checks failed: N`.
- **tests/test_maker_cli.py** — end-to-end CLI runs against throwaway
  projects under `.maker_tmp/` (never the demo data proper). Prints
  `N failure(s)`.
- **tests/test_maker_gui.py** — drives the real App with synthetic
  pygame events (SDL dummy) on a copy of the demo project; saves
  `screenshots/maker_gui_*.png`. Prints `N failure(s)`.

## 4. Templates and fixtures

- **templates/starter/** — the neutral starter project copied by `new`:
  one walled cell `0_0` with a `spawn_point`, zone "Start" (whose
  `default_palette` is "default"), palette "default", player +
  save_station sprites. Never edited via the editor.
- **tests/fixtures/demo_project/** — a frozen copy of the sample game
  used ONLY by tests; tests copy it to `.maker_tmp/` before mutating.
  Never open it in the editor for real work.

---

## 5. Data formats

### project.json (manifest)

```json
{
  "format": 1,
  "name": "brinstar",
  "title": "Metroidvania Voxel Demo",
  "entity_types": ["spawn_point", "save_station", "item_*", "light", "ambient_light"],
  "paths": {"cells_dir": "world/cells"}   // optional override of any DEFAULT_LAYOUT key
}
```

`format` is managed by the engine (`FORWARD>=2` is refused). The editor
always offers `ENGINE_ENTITY_TYPES` (`spawn_point`, `save_station`,
`light`, `ambient_light`) even when `entity_types` predates them.

### world/world_index.json

```json
{
  "start_cell": [0, 0],
  "cells": {"0,0": "0_0.json", "1,0": "1_0.json"}
}
```

The only whole-world structure; every cell listed here has a file in
`world/cells/`. Coords not listed do not exist (solid rock / void).

### world/cells/<x>_<y>.json — one JSON file per cell

```json
{
  "coord": [0, 0],
  "zone": "Blue Brinstar",
  "tiles": [ [ {"kind": "solid", "palette": "blue_brinstar"}, ... 16 per row ], ... 10 rows ],
  "edges": {
    "north": {"type": "door", "door_id": "d03", "target_cell": [0, -1], "target_edge": "south"},
    "east":  {"type": "open"}
  },
  "entities": [ {"type": "spawn_point", "tile_x": 8, "tile_y": 7} ],
  "background_color": [20, 30, 70]
}
```

- `tiles` is always 10 rows x 16 (see `CELL_HEIGHT_TILES` /
  `CELL_WIDTH_TILES` in `src/engine/constants.py`). A tile is
  `{"kind": ...}` plus optional `"palette"`; legacy integer ids 0-4 are
  still accepted on load. Kinds: `empty`, `solid`, `spring`, `oneway`,
  `crumble`. `serialize.py` writes each row on one line.
- `edges`: only non-wall edges are stored. `wall` (default, omitted),
  `open` (joined to the neighbour), `door` (with `door_id`, exact
  `target_cell`, `target_edge`).
- `entities`: `{"type", "tile_x", "tile_y"}` + any extra props stored
  flat on the dict — e.g. lights carry `radius`, `color` [r,g,b] and
  `flicker`. See "Entities" below.
- `background_color` overrides the room's backdrop tint.
- `zone`: cosmetic label only; never affects what loads together.

### zones.json

```json
{
  "Blue Brinstar": {
    "rock":  [[12,20,34], ... 8 RGB],
    "cap":   [[60,140,168], ... 8 RGB],
    "accent": [150,220,255],
    "fog_top": [8,14,26],
    "fog_bottom": [16,28,48],
    "ambient": [190,205,225],
    "default_palette": "blue_brinstar"
  }
}
```

`rock`/`cap` are 8-RGB material ramps for the voxel tile renderers;
`accent` themes the minimap/title card; `fog_top`/`fog_bottom` drive the
background gradient; `ambient` is the fallback colour for a room's
`ambient_light`; `default_palette` (optional) is used by new cells in
the zone and the "auto" brush palette. Everything is fully
user-defined — nothing Metroid-specific is required.

### palettes/<name>.json — named tile texture sets

```json
{
  "name": "default",
  "display_name": "Default Rock",
  "tile_px": 48,
  "build": {
    "generator": "voxel_rock",
    "colors": {"rock": [8 RGB], "cap": [8 RGB], "accent": RGB},
    "locked": ["solid"]          // optional: kinds whose PNGs are authored
  },
  "textures": {
    "solid":   {"file": "solid.png",   "layout": "autotile16", "variants": 4, "overhang_px": 9},
    "oneway":  {"file": "oneway.png",  "layout": "variants",   "variants": 4},
    "spring":  {"file": "spring.png",  "layout": "frames",     "frames": 3, "frame_ticks": 6},
    "crumble": {"file": "crumble.png", "layout": "stages",     "stages": 4}
  }
}
```

`textures` maps each tile kind to a PNG sheet in
`textures/<palette>/` plus its layout (`autotile16` for solid, else
variants/frames/stages). Procedural generators are `voxel_rock` (the
built-in voxel relief renderers) and `mac_os9` (classic Mac OS 9
platinum desktop chrome drawn by `tools/desktop_textures.py`, an
autotile16 window grammar: exposed tops become titlebar chrome with
close/zoom boxes, exposed sides are window borders, exposed bottoms a
status-like edge, interiors the light client area).
`generator: "imported"` means the
textures/ folder is copied rather than procedurally rebuilt. `build.locked` marks
kinds edited by PNG import.

### sprite_src/<name>.json — hand-editable sprite definitions

```json
{
  "name": "player",
  "voxel_px": 3,
  "outline": [10, 8, 10],
  "colors": {"O": [240,170,40], ...},
  "heights": {"O": 3, ...},          // optional per-char bevel height
  "anchor": [7, 17],                 // anchor in art px (base point)
  "animations": {
    "idle": {"frame_ticks": 30, "frames": [ ["....OO....", ...], ... ]}
  }
}
```

Each animation frame is an ASCII grid (rows of equal length); `tools/
build_sprites.py` renders it into a sprite sheet.

### sprites/<name>.json — built runtime metadata (build output)

```json
{
  "name": "player", "frame_w": 48, "frame_h": 60, "anchor_px": [25, 57],
  "animations": {"idle": {"start": 0, "count": 2, "frame_ticks": 30}, ...}
}
```

Produced by `tools/build_sprites.py` alongside `sprites/<name>.png`;
consumed by `src/gfx/sprite_registry.py`.

### texture sheets

`textures/<palette>/*.png` are build outputs referenced by the palette
JSON (`tools/build_textures.py`), loaded at runtime by
`src/gfx/texture_registry.py`. solid uses autotile16 (16 neighbour
masks, 4 variants, optional overhang); oneway uses 4 plain variants;
spring animates 3 frames at `frame_ticks`; crumble has 4 collapse
stages.

---

## 6. Entities (placed in cells)

Engine-understood types (always offered by the editor regardless of the
manifest list):

- `spawn_point` — where the player starts; adding one removes any other
  and sets the start cell. Invisible in-game.
- `save_station` — drawn as a resting station (sprite).
- `light` — authored light pool: props `radius` (game px, default 48),
  `color` [r,g,b] (default `[255,220,160]`), `flicker` (bool).
  Invisible in-game; the editor overlay draws its radius ring.
- `ambient_light` — one per room optionally darkens the whole room to
  `color` (default the zone's `ambient`). Without one, a room is flat
  and fully lit. Invisible in-game.
- `item_*` (`item_missile`, `item_energy`, `item_super_missile`,
  `item_power_bomb`, `item_reserve_tank`, `item_varia_suit`, `item_spazer`,
  `item_xray`, `item_map`) — drawn as rotating voxel pickups
  (presentation only; no pickup logic).

Adding an engine feature that needs data = add a type here (or a
cell/zone/palette field), teach engine + validator + overlay + ops +
CLI + GUI + docs + tests (see AGENTS.md).

---

## 7. Execution model

1. `python -m src.main --project DIR` -> `Project.load` -> active
   project -> `WorldLoader` (parses world_index, LRU cache).
2. `GameWorld(loader)` -> `loader.start_assembled_room()` ->
   `room_assembler.assemble()` flood-fills from the start cell over
   `open` edges into one `AssembledRoom` (cap 12 cells).
3. Each frame: input -> physics on the room-local tile grid ->
   `_try_transition` (per-owning-cell door rects; on crossing,
   reassemble from the target cell, reposition relative to the target
   door rect, record a `TransitionEvent`) -> `VoxelRenderer.render_frame`
   (backdrop+fog, tiles from texture sheets, entities, player, particles,
   authored lighting, title card, minimap, debug).
4. A door crossing plays `RoomTransition` (freeze + camera pan across
   the shared world-pixel grid) then swaps in the new room.
5. Exported games: `export_game` copies `src/` (no demo data, no
   `tools`/`maker`) + the project's data + `project.json` at the export
   root; `default_project_root()` step 2 finds it ("the export root's
   own project.json"), so the same engine code plays the exported game
   with no modifications.

---

## 8. Glossary

- **Cell** = one world-map grid square, addressed (cx, cy), fixed
  16x10 tiles, one JSON file. Never "cell" for a map tile or a sprite.
- **Tile** = one 16x16 game-px square inside a cell (kind + palette).
- **Room** = runtime group of cells joined via open edges (computed by
  flood-fill, never authored); one room is assembled and simulated at a
  time, with lazy LRU loading of cells.
- **Edge** = one side of a cell: `wall` / `open` / `door`.
- **Door** = an edge explicitly linked to another cell's edge (door id,
  target cell/edge); crossing reassembles the room.
- **Entity** = object placed at tile coords inside a cell
  (spawn/save/items/lights).
- **Zone** = cosmetic area label (visual theme, title card, ambient).
- **Palette** = named tile texture set (palettes/ + textures/).
- **Texture** = a PNG sheet for one tile kind of one palette.
- **Sprite** = built character/object sheet from a sprite_src
  definition.
- **Project** = one complete game's data folder.
- **Template** = neutral starter project copied by `new`.
- **Fixture** = frozen demo copy used only by tests.
- **Export/Build** = engine + one project's data bundled into a
  standalone playable folder.
- **Playtest** = launching the project's game, optionally from a chosen
  cell/tile.
