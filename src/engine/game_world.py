"""
Glue layer: owns "where is the player in the world" and drives
transitions between assembled cell-groups. This is the only module that
talks to the data/assembly layer (via WorldLoader) and the physics layer
(physics.py) -- neither of those two depend on each other or on this
module.

Player position is tracked as room-local pixel coordinates relative to
the top-left corner of the CURRENT AssembledRoom's stitched tile grid.
Crossing a `door` edge (detected per-owning-cell, not by the group's
overall bounding box -- see engine.room_assembler.DoorCrossing) triggers
re-assembling a new AssembledRoom starting from the door's target cell
and repositions the player inside it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.data.schema import EAST, NORTH, SOUTH, WEST
from src.engine.constants import CELL_HEIGHT_TILES, CELL_WIDTH_TILES, TILE_SIZE
from src.engine.physics import AABBBody, apply_gravity, move_and_collide
from src.engine.room_assembler import AssembledRoom, DoorCrossing, local_tile_offset
from src.engine.world_loader import WorldLoader

PLAYER_W = 10
PLAYER_H = 14
DOOR_INSET = TILE_SIZE * 1.0


@dataclass
class TransitionEvent:
    from_cell: tuple
    to_cell: tuple
    direction: str


class GameWorld:
    def __init__(self, loader: WorldLoader) -> None:
        self.loader = loader
        self.current_room: AssembledRoom = loader.start_assembled_room()
        start_tile_x, start_tile_y = self._spawn_tile(self.current_room)
        self.body = AABBBody(
            x=start_tile_x * TILE_SIZE,
            y=start_tile_y * TILE_SIZE,
            width=PLAYER_W,
            height=PLAYER_H,
        )
        self.last_transition: Optional[TransitionEvent] = None

    @staticmethod
    def _spawn_tile(room: AssembledRoom):
        for entity in room.entities:
            if entity.get("type") == "spawn_point":
                return entity["tile_x"], entity["tile_y"]
        return room.width // 2, room.height // 2

    def _crossed(self, crossing: DoorCrossing) -> bool:
        # Trigger as soon as the player's LEADING edge reaches (>=, not
        # strictly >) the cell's boundary in the direction of travel.
        # ">=" matters because physics may stop the body flush against a
        # solid tile just beyond the doorway (e.g. the placeholder solid
        # filling a bounding-box slot with no authored cell), in which
        # case the leading edge never gets to go strictly past the
        # boundary -- it lands exactly on it and then vx is zeroed.
        rx, ry, rw, rh = crossing.cell_rect
        bx, by, bw, bh = self.body.x, self.body.y, self.body.width, self.body.height
        if crossing.direction == EAST:
            return bx + bw >= rx + rw and by + bh > ry and by < ry + rh
        if crossing.direction == WEST:
            return bx <= rx and by + bh > ry and by < ry + rh
        if crossing.direction == SOUTH:
            return by + bh >= ry + rh and bx + bw > rx and bx < rx + rw
        if crossing.direction == NORTH:
            return by <= ry and bx + bw > rx and bx < rx + rw
        return False

    def _try_transition(self) -> None:
        for crossing in self.current_room.door_crossings:
            if self._crossed(crossing):
                self._transition_through(crossing)
                return
        self._clamp_to_room_bounds()

    def _transition_through(self, crossing: DoorCrossing) -> None:
        from_coord = self._owning_cell(crossing)
        new_room = self.loader.assemble_from(crossing.target_cell)

        # Find the matching door on the target side (the target cell's own
        # edge that points back to where the player came from) so we spawn
        # relative to ITS cell rect in the newly assembled room -- never
        # relative to the overall bounding box, which is what lets this
        # work correctly even for concave/oddly-shaped groups.
        target_crossing = self._find_reverse_crossing(new_room, from_coord, crossing)
        rx, ry, rw, rh = crossing.cell_rect
        tx, ty, tw, th = target_crossing.cell_rect

        if crossing.direction in (EAST, WEST):
            # Travel was horizontal: preserve the player's vertical offset
            # within the doorway's cell across the transition.
            rel_y = self.body.y - ry
            new_y = ty + max(0.0, min(rel_y, th - self.body.height))
            if crossing.target_edge == EAST:
                new_x = tx + tw - DOOR_INSET - self.body.width
            else:
                new_x = tx + DOOR_INSET
        else:
            rel_x = self.body.x - rx
            new_x = tx + max(0.0, min(rel_x, tw - self.body.width))
            if crossing.target_edge == SOUTH:
                new_y = ty + th - DOOR_INSET - self.body.height
            else:
                new_y = ty + DOOR_INSET

        self.last_transition = TransitionEvent(
            from_cell=from_coord,
            to_cell=crossing.target_cell,
            direction=crossing.direction,
        )
        self.current_room = new_room
        self.body.x = new_x
        self.body.y = new_y
        # Velocity is deliberately preserved (not zeroed) so movement
        # through a doorway feels continuous rather than resetting speed.

    def _owning_cell(self, crossing: DoorCrossing):
        rx, ry, _, _ = crossing.cell_rect
        for coord in self.current_room.cells:
            cx, cy = local_tile_offset(self.current_room, coord)
            if (cx * TILE_SIZE, cy * TILE_SIZE) == (rx, ry):
                return coord
        return None

    @staticmethod
    def _find_reverse_crossing(
        new_room: AssembledRoom, from_coord, crossing: DoorCrossing
    ) -> DoorCrossing:
        for candidate in new_room.door_crossings:
            if candidate.direction == crossing.target_edge and candidate.target_cell == from_coord:
                return candidate
        raise ValueError(
            f"Door {crossing.door_id!r} targets {crossing.target_cell}/"
            f"{crossing.target_edge}, but no matching door back to {from_coord} "
            f"was found there -- check the two cells' edge data agree."
        )

    def _clamp_to_room_bounds(self) -> None:
        width_px = self.current_room.width * TILE_SIZE
        height_px = self.current_room.height * TILE_SIZE
        self.body.x = max(0.0, min(self.body.x, width_px - self.body.width))
        self.body.y = max(0.0, min(self.body.y, height_px - self.body.height))

    def set_horizontal_input(self, direction: int, move_speed: float) -> None:
        self.body.vx = direction * move_speed

    def jump(self, jump_speed: float) -> None:
        if self.body.on_ground:
            self.body.vy = jump_speed

    def update(self) -> None:
        apply_gravity(self.body)
        move_and_collide(self.body, self.current_room.tiles)
        self._try_transition()

    def current_world_cell(self):
        """World grid cell (x, y) the player's center currently occupies.
        For HUD/debug display only -- collision and transitions never
        need this, since they operate on room-local pixel coordinates."""
        center_x = self.body.x + self.body.width / 2
        center_y = self.body.y + self.body.height / 2
        local_cx = int(center_x // (CELL_WIDTH_TILES * TILE_SIZE))
        local_cy = int(center_y // (CELL_HEIGHT_TILES * TILE_SIZE))
        origin_x, origin_y = self.current_room.origin
        return origin_x + local_cx, origin_y + local_cy
