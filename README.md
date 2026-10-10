# Metroidvania Maker

A fixed 2D voxel-art metroidvania engine plus **Metroidvania Maker**, a
separate program for authoring games on top of that engine.

The engine never changes: it reads one "project" (a game's data folder)
and plays it, one room at a time. The Maker (the `maker/` package) edits
project folders — world, zones, palettes, textures, sprites, settings —
and can export each project as its own playable game. **This repo
carries no game data.** Games live in `projects/<name>/`, which this
repo's `.gitignore` ignores so each game can be its own git repository.

Look and feel: late-90s / SNES-style flat 2D side-view, chunky
voxel-pixel art, restrained palettes. Rooms are fully lit unless you
author lighting inside them (see "Lighting" below).

## Terminology (read this once)

The same words are used at two different scales. This project keeps them
strictly separate:

- **Cell** = one square of the WORLD MAP grid, addressed `(cx, cy)`; a
  fixed 16x10 tiles; one JSON file. Never say "cell" for a map tile or
  for a sprite/frame.
- **Tile** = one 16x16 game-pixel square INSIDE a cell, described by a
  collision `kind` plus a `palette` label. Never say "tile" for a map
  square.

Every other term (room, edge, door, entity, zone, palette, frame,
project) is defined in the [glossary](#glossary).

## What is where

- `src/` — the fixed engine (data model, physics, room assembly, voxel
  renderer). Never edited to change game content.
- `maker/` — Metroidvania Maker: a headless core (`maker/core`) shared
  by the editor window, a JSON command line (`maker.cli`) and the
  Python API, plus the pygame GUI (`maker/gui`).
- `templates/starter/` — the neutral starter project that `new` copies
  to start a game. Never edit it via the editor.
- `tests/` — headless test scripts plus `tests/fixtures/demo_project/`,
  a frozen copy of the sample game used ONLY by tests.
- `projects/` — the user's game projects; each is one complete game and
  can be its own git repo. `projects/brinstar/` is the bundled sample
  ("Metroidvania Voxel Demo": 111 cells, 5 zones).
- `Metroidvania Maker.command` — macOS double-click launcher for the
  editor.

Removed from this repo (still in git history): the old demo world in
`src/data/`, `assets/`, and the old generator/migration tools.

## Setup

A `.venv` virtualenv with Python 3.9 and pygame 2.6.1 is already
included. If you ever recreate it: `python3.9 -m venv .venv && .venv/bin/pip
install pygame==2.6.1` (there is no requirements file; pygame is the
only dependency).

## Quick start

Open the editor (double-click `Metroidvania Maker.command`, or):

```bash
.venv/bin/python -m maker
```

It opens your last-opened project, else the first in `projects/`, else
creates a fresh `projects/my_game` from the starter template. Play the
bundled sample:

```bash
.venv/bin/python -m src.main --project projects/brinstar
```

In-game: Left/Right or A/D move, Space/Up/W jump (double jump), Esc
quits, G toggles the debug world-grid, H toggles the debug info window.

## Where games live

A project is a folder with `project.json` and, in the default layout:

```
world/world_index.json      cell -> file map + start cell
world/cells/<x>_<y>.json    one file per cell
palettes/<name>.json        named texture-set definitions
textures/<palette>/         built PNG sheets per tile kind
sprite_src/<name>.json      hand-editable sprite definitions
sprites/<name>.json/.png    built sprite sheets + runtime metadata
zones.json                  zone colours + fog + ambient + default_palette
```

`project.json` (format, name, title, entity_types) may override the
layout with a `"paths"` dict (see `src/project.py` `DEFAULT_LAYOUT`).

## Making a new game

- **GUI:** the Project tab's "New project..." (defaults to
  `projects/new_game`); "starter" starts from the template, "copy"
  duplicates the current project.
- **CLI:**

```bash
.venv/bin/python -m maker.cli new projects/mygame --name mygame --title "My Game"
```

Putting a game in its own git repo: the editor repo ignores `projects/`,
so just `cd projects/mygame && git init` (ignore `builds/` and
`.DS_Store`). The sample game is at
`/Users/ben/Documents/game/projects/brinstar` and is meant to be its own
repo too (see its README).

## How the engine plays a project

- The world is an infinite integer grid of **cells** centred on `(0, 0)`;
  `world_index.json` is the only whole-world structure kept in memory.
- A **room** is not authored: entering a cell flood-fills every cell
  connected through `open` edges into one assembled room (capped at
  `MAX_ASSEMBLED_CELLS`); `wall` and `door` edges stop the fill.
- Cells load lazily from disk and are LRU-cached, so a world can be as
  large as you like without a memory cost.
- A `door` edge is an explicit link to a cell/edge; crossing it
  reassembles from the target cell and plays a Super Metroid-style
  camera-slide transition.

## Lighting is authored only

There is **no automatic lighting**. Rooms render flat and fully lit
unless they contain an `ambient_light` entity, which darkens the room to
its `color` (default: the zone's `ambient`); `light` entities then add
pools of light with props `radius` (game px, default 48), `color`
(default `[255,220,160]`) and optional `flicker`. `spawn_point`,
`light` and `ambient_light` are invisible in-game; the editor overlay
draws them (a light shows its radius ring). The GUI "Light" toolbar
toggle (Cmd+L) shows/hides authored lighting in the editor view — the
editor renders exactly what the game renders.

Add a light with the Entity tool, or:

```bash
.venv/bin/python -m maker.cli --project DIR entity add CX,CY light --at TX,TY \
    --prop radius=60 --prop 'color=[255,200,120]'
```

Non-light presentation still automatic per-zone (candidates for becoming
authored data later): the parallax backdrop + fog gradient, ambient zone
motes, landing dust, spring/crumble debris.

## Zones and palettes

- A **zone** is a cosmetic area label (colour theme, title card).
  zones.json entries carry `rock`/`cap` ramps, `accent`,
  `fog_top`/`fog_bottom`, `ambient`, and optionally
  `"default_palette": "<palette name>"`.
- A **palette** is a named tile-texture set, fully user-defined —
  nothing Metroid-specific is required.
- New cells in a zone, and the "auto" brush palette, use that zone's
  `default_palette`. The GUI Zones tab has a "Default palette" picker
  and "Apply to zone tiles"; the CLI has
  `zone palette ZONE PALETTE|-` and
  `zone apply-palette ZONE [--palette P] [--only-unset]`.
- Renaming a palette re-points every tile and default_palette that uses
  it (and renames `textures/<old>` on save); deleting a used palette
  requires `replace_with`. `validate` warns `zone_palette_missing` when
  a zone's default_palette is not a defined palette.

## Metroidvania Maker (editor)

### GUI

```bash
.venv/bin/python -m maker                   # last project / first in projects/
.venv/bin/python -m maker --project DIR     # a specific project
.venv/bin/python -m maker --screenshot out.png   # headless frame, then exit
```

- **Layout:** toolbar (top), tool strip (left), Room Editor / World Map
  viewport (centre), data pane with the tabs Inspect, JSON, Problems,
  Palettes, Zones, Sprites and Project (right), status bar (bottom).
- **Tools:** Select (V), Paint (B), Rectangle (R), Fill (F), Erase (X),
  Eyedropper (I), Entity (N), Edge/Door (E). Keys 1–5 pick the brush
  kind.
- **World Map (Tab):** click selects a cell, double-click opens its
  room; N creates a cell on an empty square, Del deletes, drag moves,
  Alt-drag duplicates, S sets the start cell.
- **Editing:** a whole drag is one undo step. Cmd+Z / Shift+Cmd+Z (or
  Cmd+Y) undo/redo, Cmd+S saves, Cmd+G grid, Cmd+K kinds, Cmd+L
  lighting, Cmd+Q quits; Tab switches room/map; F5 saves and playtests
  from the hovered tile.
- **Palettes tab:** create/rename/delete palettes, edit colours (sheets
  rebuild live), import PNG art per tile kind or export a sheet.
- **Zones tab:** new/rename/delete zones, edit colours, "Default
  palette" picker and "Apply to zone tiles".
- **Sprites tab:** edit the source JSON, then rebuild the sheet.
- **Project tab:** project settings, New/Open project, Build Game.

### CLI (for agents and scripts)

Every command prints one JSON object on stdout
(`{"ok": true, "result": ..., "issues": [...]}`, exit 0/1/2); mutating
commands save automatically.

```bash
.venv/bin/python -m maker.cli --project DIR info
.venv/bin/python -m maker.cli --project DIR validate
.venv/bin/python -m maker.cli --project DIR cell get 0,0 --ascii
.venv/bin/python -m maker.cli --project DIR render 0,0 --room \
    --overlay grid,cells,edges,entities -o room.png
.venv/bin/python -m maker.cli --project DIR export build/MyGame
.venv/bin/python -m maker.cli --project DIR playtest
.venv/bin/python -m maker.cli --project DIR batch commands.json
```

Without `--project`, the CLI resolves the project `METROIDVANIA_PROJECT`
env -> last opened -> first in `projects/` and errors ("no project; pass
--project DIR or run `new`") instead of auto-creating one. `--dry-run`
applies + validates without saving; `.venv/bin/python -m maker.cli ops`
lists every op with signature and docstring; `batch` runs a list of ops
in one transaction (rolls back on any error). The GUI, CLI and Python
API share one headless core, so an edit behaves identically whichever
way it is made.

### Export

"Build Game" writes a standalone folder (default
`<project>/builds/<name>`); the CLI equivalent is `export DIR`. The
export bundles the fixed engine (`src/`) with the project's data and a
`project.json` at the root, so the game runs anywhere with Python 3 +
pygame (`python3 -m src.main`, or play.command / play.sh). It refuses on
unsaved changes or validation errors (use `--force`) and only overwrites
previous exports.

## Tests

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tests.test_maker_core
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tests.test_maker_cli
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tests.test_maker_gui
```

The core test expects `checks failed: 0`; the CLI and GUI tests expect
`0 failure(s)`. The GUI test saves screenshots to `screenshots/`.

## More documentation

- [AGENTS.md](AGENTS.md) — rules for AI agents (owner preferences,
  commands, testing, how to add a data-driven engine feature).
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module-by-module map,
  data-format reference and the full glossary.

## Glossary

- **Cell** = one world-map grid square, 16x10 tiles, one JSON file.
  Never say "cell" for a map tile or a sprite.
- **Tile** = one 16x16 game-px square inside a cell (kind + palette).
  Kinds: `empty`, `solid`, `spring`, `oneway`, `crumble`.
- **Room** = runtime group of cells joined by open edges (computed by
  flood-fill, never authored); one room is assembled/rendered at a time.
- **Edge** = one side of a cell: `wall` / `open` / `door`.
- **Door** = an edge linked to another cell's edge (door id + target).
- **Entity** = object placed at tile coords inside a cell
  (`spawn_point`, `save_station`, `item_*`, `light`, `ambient_light`).
- **Zone** = cosmetic area label (visual theme, title card, ambient).
- **Palette** = named tile texture set (`palettes/` + `textures/`).
- **Texture** = a PNG sheet for one tile kind of one palette.
- **Sprite** = a built character/object sheet (`sprites/`) from a
  hand-editable definition (`sprite_src/`).
- **Frame** = one sub-image of a texture or sprite sheet (NOT a cell).
- **Project** = one complete game's data folder + `project.json`.
- **Template** = neutral starter project (`templates/starter/`) that
  `new` copies; never edited via the editor.
- **Fixture** = `tests/fixtures/demo_project/`, a frozen copy of the
  sample game used only by tests.
- **Export/Build** = bundling the fixed engine + a project's data into a
  standalone playable folder.
- **Playtest** = launching the project's game (optionally from a chosen
  cell/tile) to try a build.
