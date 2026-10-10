"""Startup and persistence state for the editor.

The editor repo itself carries no game data; this module remembers which
project was opened last and resolves which project to open at startup,
shared by the GUI launcher (python -m maker) and the JSON CLI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from src.project import (  # noqa: E402
    ENGINE_ROOT,
    PROJECTS_DIR,
    PROJECT_FILE,
)

STATE_PATH = ENGINE_ROOT / ".maker_state.json"


def load_last_project() -> Optional[Path]:
    """The last opened project root from .maker_state.json, only when that
    folder still contains a project.json."""
    try:
        data = json.loads(STATE_PATH.read_text())
        root = Path(data.get("last_project", ""))
    except (OSError, ValueError, AttributeError, TypeError):
        return None
    if root and (root / PROJECT_FILE).is_file():
        return root
    return None


def save_last_project(path) -> None:
    """Record the most recently opened project root. Never raises."""
    try:
        STATE_PATH.write_text(json.dumps({"last_project": str(path)}) + "\n")
    except OSError:
        pass


def resolve_startup_project(explicit: Optional[str] = None,
                            create_missing: bool = True) -> Optional[Path]:
    """The project root to open at startup.

    `explicit` (a --project argument) always wins. Otherwise the order
    is: METROIDVANIA_PROJECT env var, the last opened project
    (.maker_state.json, only when it still has a project.json), then the
    first (sorted) project folder under projects/ with a project.json.
    When nothing exists and create_missing is True (editor default), a
    fresh projects/my_game project is created from the starter template
    and returned; when create_missing is False (CLI), None is returned
    and the caller decides. The starter template itself is never opened
    for editing.
    """
    if explicit:
        return Path(explicit)
    env = os.environ.get("METROIDVANIA_PROJECT")
    if env:
        return Path(env)
    last = load_last_project()
    if last is not None:
        return last
    if PROJECTS_DIR.is_dir():
        for d in sorted(PROJECTS_DIR.iterdir()):
            if (d / PROJECT_FILE).is_file():
                return d
    if not create_missing:
        return None
    from maker.core.project_ops import create_project
    dest = PROJECTS_DIR / "my_game"
    if not (dest / PROJECT_FILE).is_file():
        create_project(dest, "my_game", "My Game")
    return dest
