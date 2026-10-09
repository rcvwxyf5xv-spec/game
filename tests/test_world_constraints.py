"""
Headless (no pygame window) checks that the map constraints the user
asked for actually hold, driven straight through the real engine code
(world_loader, room_assembler, game_world, physics) against the
generated sample world.

Run with:  python -m tests.test_world_constraints
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.engine.constants import MOVE_SPEED  # noqa: E402
from src.engine.game_world import GameWorld  # noqa: E402
from src.engine.world_loader import WorldLoader  # noqa: E402

WORLD_INDEX_PATH = ROOT / "src" / "data" / "world_index.json"
CELLS_DIR = ROOT / "src" / "data" / "cells"

failures = []


def check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def run_frames(world: GameWorld, n: int) -> None:
    for _ in range(n):
        world.update()


def run_until_transition(world: GameWorld, max_frames: int) -> bool:
    for _ in range(max_frames):
        world.update()
        if world.last_transition is not None:
            return True
    return False


def main() -> None:
    loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=4)

    # 1. World addressing: center is (0,0), right of center is (1,0).
    check("start cell is (0, 0)", loader.start_cell_coord() == (0, 0))
    check("a cell exists at (0,0)", loader.cell_exists((0, 0)))
    check("a cell exists at (1,0) (right of center)", loader.cell_exists((1, 0)))

    # 2. Assembling from a single isolated cell (doors on all sides, no
    #    open edges) yields a one-cell playspace -- confirms grouping is
    #    NOT happening just because cells are adjacent/addressable.
    hub_room = loader.assemble_from((0, 0))
    check("assembling from (0,0) yields just that one cell", hub_room.cells == [(0, 0)])

    # 3. The L-shaped playspace: assembling from any one of its three
    #    cells floods out through `open` edges to the other two, and
    #    nothing else -- this is the "load the target, then load
    #    everything connected by non-wall/non-door edges" behavior.
    scratch_loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=8)
    l_group = scratch_loader.assemble_from((-1, 0))
    expected_cells = {(-1, 0), (-1, 1), (0, 1)}
    check(
        "flood-fill from (-1,0) assembles exactly the 3-cell L shape",
        set(l_group.cells) == expected_cells,
    )
    check(
        "assembled L playspace is NOT a simple rectangle (more than 1 cell)",
        len(l_group.cells) > 1,
    )
    check(
        "the L's bounding-box 'missing corner' (0,0) is excluded (separated by a door, not open)",
        (0, 0) not in l_group.cells,
    )
    same_from_other_member = scratch_loader.assemble_from((0, 1))
    check(
        "flood-filling from a different member cell of the L yields the same group",
        set(same_from_other_member.cells) == expected_cells,
    )

    # 4. Lazy loading: only requested cells are in memory, bounded by cache size.
    #    Use a fresh loader here since `loader` above was already used for
    #    addressing checks (loading cells is expected at that point).
    fresh_loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=4)
    check("no cells loaded before any access", fresh_loader.loaded_cell_coords() == [])
    fresh_loader.get_cell((0, 0))
    check("exactly one cell loaded after one access", len(fresh_loader.loaded_cell_coords()) == 1)
    small_cache_loader = WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=2)
    small_cache_loader.get_cell((0, 0))
    small_cache_loader.get_cell((1, 0))
    small_cache_loader.get_cell((-1, 0))
    check(
        f"loaded cell count never exceeds cache_size ({small_cache_loader.cache_size})",
        len(small_cache_loader.loaded_cell_coords()) <= small_cache_loader.cache_size,
    )
    check(
        "oldest cell (0,0) was evicted once cache exceeded",
        (0, 0) not in small_cache_loader.loaded_cell_coords(),
    )

    # 5. Gameplay-level: walking the player east from the hub actually
    #    triggers a live door transition into the (1,0) cell group via
    #    the real physics/transition code (not just data-layer checks).
    world = GameWorld(WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=8))
    check("player starts in the (0,0) cell", world.current_world_cell() == (0, 0))
    world.set_horizontal_input(1, MOVE_SPEED)
    transitioned = run_until_transition(world, 400)
    check("player walking east triggered a door transition", transitioned)
    check(
        "player's world cell after walking east is (1, 0)",
        world.current_world_cell() == (1, 0),
    )

    # 6. Walking west from the hub transitions into the L-shaped group,
    #    landing specifically in (-1, 0) (the door's owning cell, not
    #    just "somewhere in the bounding box").
    world2 = GameWorld(WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=8))
    world2.set_horizontal_input(-1, MOVE_SPEED)
    transitioned2 = run_until_transition(world2, 400)
    check("player walking west triggered a door transition", transitioned2)
    check(
        "player's world cell right after entering the L group is (-1, 0)",
        world2.current_world_cell() == (-1, 0),
    )
    check(
        "the assembled room after that transition is the full 3-cell L group",
        set(world2.current_room.cells) == expected_cells,
    )

    # 7. Moving within the L group (crossing an `open` edge, not a door)
    #    must NOT fire a transition event or swap the assembled room --
    #    it should be invisible/seamless, same as moving within any
    #    single-cell room.
    world2.last_transition = None
    room_before = world2.current_room
    # The player lands near the door on the east side of (-1,0); the open
    # seam down into (-1,1) is a floor gap further west, so walk toward it
    # before letting gravity carry the player through.
    world2.set_horizontal_input(-1, MOVE_SPEED)
    run_frames(world2, 60)
    world2.set_horizontal_input(0, MOVE_SPEED)
    run_frames(world2, 240)
    check(
        "moving within the L group does not fire a transition event",
        world2.last_transition is None,
    )
    check(
        "moving within the L group keeps the same assembled room object",
        world2.current_room is room_before,
    )
    check(
        "player fell into the L group's second cell (-1, 1) via an open edge",
        world2.current_world_cell() == (-1, 1),
    )

    # 8. Round trip: walking back east out of the L group through the
    #    door on (-1,0) returns to the (0,0) hub group. This specifically
    #    exercises a door anchored to a cell that is NOT on the outer
    #    edge of its group's overall bounding box (the L's bbox is 2x2,
    #    but this door sits on an internal column of that bbox) --
    #    proving transitions are detected per-cell, not per-bounding-box.
    world3 = GameWorld(WorldLoader(WORLD_INDEX_PATH, CELLS_DIR, cache_size=8))
    world3.set_horizontal_input(-1, MOVE_SPEED)
    run_until_transition(world3, 400)
    world3.last_transition = None
    world3.set_horizontal_input(1, MOVE_SPEED)
    transitioned3 = run_until_transition(world3, 400)
    check(
        "a door on a cell that is internal to its group's bounding box still triggers",
        transitioned3,
    )
    check(
        "walking back east from the L group returns to the (0,0) hub",
        world3.current_world_cell() == (0, 0),
    )

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("All checks passed.")


if __name__ == "__main__":
    main()
