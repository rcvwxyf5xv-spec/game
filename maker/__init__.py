"""Metroidvania Maker: a separate program for authoring games ("projects")
on top of the fixed engine.

Metroidvania Maker never changes the engine: each project owns its world
data, zones, palettes, textures, sprites and settings, and can be
exported as its own playable game. Every operation lives in the headless
core library (maker.core) that the editor window, a JSON command line
and a Python API all share, so agents and the window behave identically.

Entry points: `python -m maker` (GUI window, maker.gui) and
`python -m maker.cli` (JSON CLI, see AGENTS.md).

Python API:

    from maker.core import ProjectModel, ops

    m = ProjectModel.open("path/to/project")
    ops.set_tiles(m, (0, 0), [(3, 4)], "solid")   # runs in a transaction
    m.save()

Read-only helpers that are not ops (no undo step, no save needed):

    from maker.core import validate, ops as ops_mod
    issues = validate.validate(m)                  # list of Issue
    ops_mod.cell_ascii(m, (0, 0))                  # 10 strings of 16 chars

Assets (texture rebuilds / imports, sprite builds) write files directly
and are NOT part of undo:

    from maker.core import assets, project_ops, render

    assets.rebuild_textures(m)                     # PNG sheets for palettes
    assets.import_texture(m, "default", "solid", "tile.png")
    assets.build_sprites(m)
    project_ops.create_project("my_game", "my_game")   # new project
    project_ops.export_game(m, "build/MyGame")         # standalone game
    surf = render.render_room(m, (0, 0), overlays=("grid", "edges"))
    render.world_map_layout(m)                     # (offset, size) for clicks

The JSON command line (``python -m maker.cli``) is a thin mapping onto
the same functions, including a ``batch`` command that runs a list of ops
in one transaction.
"""
