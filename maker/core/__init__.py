"""Headless core of Metroidvania Maker.

This is the single shared implementation behind the editor window, the
JSON command line and the Python API:

- ProjectModel (model.py): in-memory mirror of one project's data with
  dirty tracking and atomic saves.
- history.py: snapshot-based transactions and undo/redo.
- ops.py: every edit the editor can do, callable by name through OPS.
- validate.py: rule checks over the model, returning Issues.
- render.py / overlays.py: headless rendering and editor overlays
  (rooms, cells, the schematic world map).
- assets.py: immediate texture / sprite sheet generation and PNG import
  (not part of undo).
- project_ops.py: new-project creation, standalone game export and
  playtest launching.

Front-ends must go through ops -> model.transaction so that undo/redo
and listeners stay consistent for every caller.
"""

from maker.core import model
from maker.core import history
from maker.core import ops
from maker.core import validate
from maker.core.errors import OpError
from maker.core.model import ProjectModel
from maker.core.validate import Issue

__all__ = ["ProjectModel", "OpError", "ops", "validate", "Issue"]
