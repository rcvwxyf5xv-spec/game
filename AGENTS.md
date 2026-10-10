# AGENTS.md — editing Metroidvania game data

To edit game data (or build a new game), use the **Maker CLI**
(`python -m maker.cli`) instead of hand-editing JSON. The CLI runs the
same headless core as the GUI (maker.core), validates every
change, reports new issues, and saves atomically.

Every command prints ONE JSON object on stdout and exits 0 (ok) / 1
(op error) / 2 (usage error):

    {"ok": true, "result": ..., "issues": [...]}

## Core commands

    .venv/bin/python -m maker.cli info
    .venv/bin/python -m maker.cli validate
    .venv/bin/python -m maker.cli cell get 0,0 --ascii
    .venv/bin/python -m maker.cli cell ascii 0,0 --file cell.txt
    .venv/bin/python -m maker.cli door link 0,0 east --to 1,0 --carve
    .venv/bin/python -m maker.cli entity add 0,0 save_station --at 5,3
    .venv/bin/python -m maker.cli render 0,0 --room \
        --overlay grid,cells,edges,entities -o room.png
    .venv/bin/python -m maker.cli batch commands.json

Add `--project DIR` to target another project (default:
`METROIDVANIA_PROJECT` env var, else the engine-root demo project).
`--json` arguments accept inline JSON, a file path, or `-` (stdin).
`--dry-run` applies in memory and validates without saving; `--pretty`
indents JSON. `python -m maker.cli help-agent` prints a full usage guide.

## Examples

    # inspect a cell, then replace its tiles with a 16-char x 10-row map
    .venv/bin/python -m maker.cli cell get 2,3
    printf '################\\n#..............#\\n#..............#\\n' > c.txt
    .venv/bin/python -m maker.cli cell ascii 2,3 --file c.txt

    # create a room: new cell, carve a passable edge, then a door
    .venv/bin/python -m maker.cli cell new 3,0 --template walled
    .venv/bin/python -m maker.cli edge set 2,0 east open --carve
    .venv/bin/python -m maker.cli door link 2,0 east --to 3,0 --carve

    # put the player at a tile after launching
    .venv/bin/python -m maker.cli playtest --cell 0,0 --tile 3,8

    # build a new standalone game and export it
    .venv/bin/python -m maker.cli new my_game --name my_game --title "My Game"
    .venv/bin/python -m maker.cli --project my_game export build/MyGame

    # batch: one transaction, one save (rolls back whole batch on error)
    echo '[{"op": "set_tiles", "coord": [0,0], "points": [[4,4]],
            "kind": "spring"}]' | .venv/bin/python -m maker.cli batch -

## Rules

- Always run `maker.cli validate` after edits and fix every error (exit
  code 1 with "error" severity issues mean the game would break).
- Render and LOOK: `maker.cli render CX,CY --room --overlay
  grid,cells,edges,entities -o shot.png`, open the PNG, iterate.
- Never edit `src/engine` for content changes; game content lives in the
  project's data (cells, palettes, zones, sprites, textures).
- Prefer `maker.cli batch` for multi-step edits (single transaction).
- Don't hand-edit `world/cells/*.json` or `world_index.json`; use the CLI.

## Glossary (full definitions in README.md)

- **Cell** = one world-map square, 16x10 tiles, one JSON file. Never say
  "cell" for a sprite/frame.
- **Tile** = one 16x16 game-px square inside a cell (kind + palette).
- **Room** = cells joined by open edges (computed, never authored).
- **Edge** = one side of a cell: wall / open / door. **Door** = an edge
  linked to another cell's edge.
- **Frame** = one sub-image of a texture or sprite sheet (NOT "cell").
- **Entity** = object placed at tile coords inside a cell. **Zone** =
  cosmetic area label. **Palette** = named tile texture set.
- **Project** = one complete game's data folder (can be exported).
