"""Launch the Metroidvania Maker GUI.

    python -m maker [--project DIR] [--size 1440x900]
    python -m maker --screenshot out.png   # render one frame headlessly

Without --project the project to open is resolved at startup (see
maker.state.resolve_startup_project): METROIDVANIA_PROJECT env, the last
opened project, the first project in projects/, or a fresh
projects/my_game built from the starter template.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m maker",
                                 description="Metroidvania Maker editor")
    ap.add_argument("--project", help="project folder (contains project.json)")
    ap.add_argument("--size", default="1440x900", help="window size WxH")
    ap.add_argument("--screenshot", metavar="PNG",
                    help="render a few frames offscreen, save, and exit")
    args = ap.parse_args(argv)
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
    if args.screenshot:
        os.environ["SDL_VIDEODRIVER"] = "dummy"
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

    import pygame
    from maker.core.model import ProjectModel
    from maker.core.errors import OpError

    pygame.init()
    try:
        w, h = (int(v) for v in args.size.lower().split("x"))
    except ValueError:
        ap.error("--size must look like 1440x900")
    try:
        from maker.state import resolve_startup_project
        model = ProjectModel.open(resolve_startup_project(explicit=args.project))
    except (OpError, OSError, ValueError) as exc:
        print(f"cannot open project: {exc}", file=sys.stderr)
        return 1
    from maker.gui.app import App
    app = App(model, (w, h), headless=bool(args.screenshot))
    if args.screenshot:
        for _ in range(3):
            app.step([])
        pygame.image.save(app.screen, args.screenshot)
        print(args.screenshot)
        return 0
    app.run_loop()
    pygame.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
