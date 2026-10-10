# AGENTS.md — working in the Metroidvania Maker repo

This repo is the **fixed engine + editor only**. Game data lives in
project folders under `projects/<name>/` (each game can be its own git
repo; this repo ignores `projects/`). Editing game data means editing a
project — through the Maker, never by hand.

## Owner preferences

- Late-90s / SNES-style flat 2D side-view look: chunky voxel-pixel art
  and restrained palettes.
- **No automatic lighting and no unrequested visual effects.** Anything
  like that must be authorable data (entities, or cell/room data)
  placed in the editor. Lighting today is authored-only: `light` and
  `ambient_light` entities (see docs/ARCHITECTURE.md).
- Fully modular and data-driven: the engine is fixed; different games
  are just different project folders made in the editor.
- The owner playtests navigation themself. No reachability/navigation
  tests are needed or wanted.
- Keep replies concise.
- Never install packages or modify anything outside
  `/Users/ben/Documents/game` without asking.
- No `.app` packaging without approval.
- Commit work when done (this repo, and a game repo when you changed
  game data).

## The one rule that matters

**Never hand-edit project JSON** (`world/cells/*.json`,
`world_index.json`, `palettes/*.json`, `zones.json`,
`sprite_src/*.json`, `project.json`). Everything goes through

```bash
.venv/bin/python -m maker.cli --project DIR ...
```

The CLI, the GUI and the Python API share one headless core
(`maker.core`): every edit is an op inside an undoable transaction,
validated, with atomic saves. `maker.cli ops` lists every op with its
signature and docstring:

```bash
.venv/bin/python -m maker.cli --project DIR ops
.venv/bin/python -m maker.cli --project DIR help-agent   # compact usage guide
```

Python API (same ops, see `maker/__init__.py`):
`ProjectModel.open(...)`, `ops.<op_name>(model, ...)`, `model.save()`.

## CLI cheat sheet

Every command prints ONE JSON object on stdout: `{"ok": true, "result":
..., "issues": [...]}`, exit 0/1/2 (ok/op error/usage error). Mutating
commands save automatically; `--dry-run` applies + validates without
saving.

```bash
.venv/bin/python -m maker.cli --project DIR info
.venv/bin/python -m maker.cli --project DIR validate
.venv/bin/python -m maker.cli --project DIR cell get 0,0 --ascii
.venv/bin/python -m maker.cli --project DIR cell new 3,0 --template walled
.venv/bin/python -m maker.cli --project DIR tiles set 0,0 --at 3,4 --kind spring
.venv/bin/python -m maker.cli --project DIR edge set 0,0 east open --carve
.venv/bin/python -m maker.cli --project DIR door link 0,0 east --to 1,0 --carve
.venv/bin/python -m maker.cli --project DIR entity add 0,0 save_station --at 5,3
.venv/bin/python -m maker.cli --project DIR entity add 0,0 light --at 8,6 \
    --prop radius=60 --prop 'color=[255,200,120]'
.venv/bin/python -m maker.cli --project DIR zone palette "Blue Brinstar" blue_brinstar
.venv/bin/python -m maker.cli --project DIR zone apply-palette "Blue Brinstar"
.venv/bin/python -m maker.cli --project DIR palette rename old_pal new_pal
.venv/bin/python -m maker.cli --project DIR render 0,0 --room \
    --overlay grid,cells,edges,entities -o room.png
.venv/bin/python -m maker.cli --project DIR render map -o map.png
.venv/bin/python -m maker.cli --project DIR playtest          # launch the game
.venv/bin/python -m maker.cli --project DIR export build/MyGame
.venv/bin/python -m maker.cli --project DIR batch commands.json
```

Without `--project`, the CLI resolves the project like the GUI but never
auto-creates one: `METROIDVANIA_PROJECT` env var, then the last opened
project (`.maker_state.json`), then the first folder in `projects/`; it
errors "no project; pass --project DIR or run `new`" when none exists.

- Multi-step edits: prefer a single `batch` (one transaction, one save,
  rolls back the whole batch on any error).
- `--json` arguments accept inline JSON, a file path, or `-` (stdin).
- Render and LOOK at the PNG: `render CX,CY --room --overlay
  grid,cells,edges,entities -o shot.png`, open it, iterate.

## Rules

- Always run `maker.cli validate` after edits and fix every `error`-level
  issue (they break the game).
- Game content never requires touching `src/`; never edit `src/engine`
  for content changes. The engine changes only for real engine features,
  and a new engine feature needs data support everywhere (see below).
- Never open `tests/fixtures/demo_project/` in the editor for real work
  (tests copy it to `.maker_tmp/` before mutating), and never edit
  `templates/starter/` via the editor.
- When you change game data, test in the game repo and commit there.

## Testing (before committing)

All tests are headless `python -m tests.*` scripts (no pytest); the SDL
dummy variables are required for anything headless that touches pygame
(Python 3.9 + pygame 2.6.1 in `.venv`; pygame is the only dependency):

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tests.test_maker_core
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tests.test_maker_cli
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tests.test_maker_gui
```

Expected output: `checks failed: 0` (core) and `0 failure(s)` (CLI and
GUI). The GUI test saves screenshots to `screenshots/maker_gui_*.png`.

Headless screenshot of the editor:

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m maker \
    --project DIR --screenshot out.png
```

Preview renders (real game frames, one PNG per zone + feature rooms):

```bash
SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy .venv/bin/python -m tools.render_preview \
    --project DIR
```

Texture / sprite sheet build tools (regenerate project PNGs):

```bash
SDL_VIDEODRIVER=dummy .venv/bin/python -m tools.build_textures --project DIR
SDL_VIDEODRIVER=dummy .venv/bin/python -m tools.build_sprites --project DIR
```

## Adding a new engine feature that needs data

To add data-driven behaviour (e.g. a new tile kind, entity type, or a
cell/zone/palette field):

1. Add the entity type or the cell/zone/palette field (see
   docs/ARCHITECTURE.md "Data formats").
2. Teach the engine (`src/`): load/assemble it and make the renderer
   and gameplay use it.
3. Teach the validator (`maker/core/validate.py`) a rule for it.
4. Teach the overlay (`maker/core/overlays.py`) to draw it in the editor.
5. Add the op in `maker/core/ops.py` (+ CLI subcommand in
   `maker/cli.py` and GUI support in `maker/gui/`).
6. Update these docs and add a test in `tests/`.

## Glossary

Full definitions in README.md and docs/ARCHITECTURE.md. Short version:

- **Cell** = one world-map grid square, 16x10 tiles, one JSON file.
- **Tile** = one 16x16 game-px square inside a cell (kind + palette).
- **Room** = cells joined by open edges (computed, never authored).
- **Edge** = one side of a cell: wall / open / door. **Door** = an edge
  linked to another cell's edge.
- **Entity** = object placed at tile coords inside a cell. **Zone** =
  cosmetic area label (visual theme, title card). **Palette** = named
  tile texture set. **Frame** = one sub-image of a texture or sprite
  sheet (NOT a cell). **Project** = one complete game's data folder.
