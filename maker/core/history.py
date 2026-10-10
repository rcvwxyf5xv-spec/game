"""Snapshot-based transactions and undo/redo for ProjectModel.

Every edit runs inside model.transaction(label). Before mutating
anything an op records the pre-image of each item it will touch
(model._touch_cell / _touch_palette / _touch_sprite / _touch_zones /
_touch_index / _touch_manifest / _touch_texture_renames). On commit the
post-images are captured and stored in an UndoStep; undo/redo apply the
pre/post images and re-derive dirty state by comparing the model against
what is on disk. The model's *saved* state is what save() refreshed, so
an undo after a save correctly marks the affected items dirty again.

Nested transactions merge into the outermost one; an exception inside
rolls back every change since the outermost began and re-raises. Empty
transactions (nothing actually changed) record no step and fire no
listeners, so e.g. "set cell ascii back to its current value" is a
no-op.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Cell/entity kinds whose items are keyed by name/coord in snapshots.
_CELL = "cell"
_PALETTE = "palette"
_SPRITE = "sprite"
_ZONES = "zones"
_INDEX = "index"
_MANIFEST = "manifest"
_TEXTURE_RENAMES = "texture_renames"

MAX_UNDO_STEPS = 1000


@dataclass
class ChangeSet:
    """What one committed transaction / undo / redo step changed."""

    cells: set = field(default_factory=set)
    index: bool = False
    zones: bool = False
    palettes: set = field(default_factory=set)
    sprites: set = field(default_factory=set)
    manifest: bool = False
    label: str = ""

    def is_empty(self) -> bool:
        """True if nothing in this set actually changed."""
        return not (self.cells or self.index or self.zones or self.palettes
                    or self.sprites or self.manifest)


@dataclass
class UndoStep:
    """Pre- and post-images of every item touched by one transaction.

    `before` / `after` map kind -> {key: snapshot}; snapshots are plain
    deep copies of the pre/post item state (None = item absent), except
    for the whole-object kinds (_ZONES/_INDEX/_MANIFEST/_TEXTURE_RENAMES)
    which hold the whole object under the None key.
    """

    label: str
    before: dict
    after: dict


class _Transaction:
    """context manager; nested instances merge into the outermost."""

    def __init__(self, history: "History", label: str) -> None:
        self.history = history
        self.label = label
        self._outer = False
        self._before: Dict[str, dict] = {}

    def __enter__(self) -> "_Transaction":
        hist = self.history
        if hist._tx is None:
            hist._tx = self
            self._outer = True
            self._before = {}
        return self

    def __exit__(self, exc_type, exc, tb):
        if not self._outer:
            return False  # nested: outer transaction owns everything
        hist = self.history
        hist._tx = None
        if exc_type is not None:
            # Roll back everything the outermost transaction changed.
            hist.model._restore(self._before)
            return False  # re-raise
        if not self._before:
            return False  # nothing touched -> nothing to record
        after = hist._capture(self._before)
        changes = hist._diff(self._before, after)
        if changes.is_empty():
            return False  # touched but unchanged -> no-op
        changes.label = self.label
        hist._push(UndoStep(self.label, self._before, after))
        hist.model._apply_changes(changes)
        return False


class History:
    """Per-model undo/redo stacks plus the active transaction."""

    def __init__(self, model) -> None:
        self.model = model
        self.undo_stack: List[UndoStep] = []
        self.redo_stack: List[UndoStep] = []
        self._tx: Optional[_Transaction] = None

    # -- transaction plumbing -------------------------------------------

    def transaction(self, label: str) -> _Transaction:
        return _Transaction(self, label)

    def touch(self, kind: str, key=None) -> None:
        """Record the pre-image of one item if not already recorded in
        the current transaction. No-op outside a transaction."""
        if self._tx is None:
            return
        tx = self._tx
        if kind not in tx._before:
            tx._before[kind] = {}
        if key not in tx._before[kind]:
            tx._before[kind][key] = self.model._snapshot(kind, key)

    def _capture(self, before: dict) -> dict:
        out = {}
        for kind, items in before.items():
            out[kind] = {key: self.model._snapshot(kind, key)
                         for key in items}
        return out

    @staticmethod
    def _diff(before: dict, after: dict) -> ChangeSet:
        changes = ChangeSet()
        for kind, items in after.items():
            pre = before.get(kind, {})
            for key, post in items.items():
                if pre.get(key) == post:
                    continue
                if kind == _CELL:
                    changes.cells.add(key)
                elif kind == _PALETTE:
                    changes.palettes.add(key)
                elif kind == _SPRITE:
                    changes.sprites.add(key)
                elif kind == _ZONES:
                    changes.zones = True
                elif kind == _INDEX:
                    changes.index = True
                elif kind == _MANIFEST:
                    changes.manifest = True
                # _TEXTURE_RENAMES: save-time side effect only, not part
                # of any ChangeSet (rename_palette always also changes a
                # palette, so a step is still recorded).
        return changes

    # -- undo / redo -----------------------------------------------------

    def _push(self, step: UndoStep) -> None:
        self.undo_stack.append(step)
        if len(self.undo_stack) > MAX_UNDO_STEPS:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> Optional[str]:
        """Apply the most recent step's pre-images; returns its label or
        None when there is nothing to undo."""
        if not self.undo_stack:
            return None
        step = self.undo_stack.pop()
        self.model._restore(step.before)
        self.redo_stack.append(step)
        changes = self._diff(step.before, step.after)
        changes.label = f"undo:{step.label}"
        self.model._apply_changes(changes)
        return step.label

    def redo(self) -> Optional[str]:
        """Re-apply the most recently undone step's post-images; returns
        its label or None when there is nothing to redo."""
        if not self.redo_stack:
            return None
        step = self.redo_stack.pop()
        self.model._restore(step.after)
        self.undo_stack.append(step)
        changes = self._diff(step.before, step.after)
        changes.label = f"redo:{step.label}"
        self.model._apply_changes(changes)
        return step.label

    def mark(self) -> int:
        """Opaque position in the undo stack, for coalesce_since()."""
        return len(self.undo_stack)

    def coalesce_since(self, mark: int, label: Optional[str] = None) -> bool:
        """Merge every step recorded after `mark` into one step (e.g. all
        the per-frame paints of one mouse drag). Pre-images come from the
        first step touching each item, post-images from the last. Returns
        True if a merge happened (two or more steps)."""
        mark = max(0, min(mark, len(self.undo_stack)))
        steps = self.undo_stack[mark:]
        if len(steps) < 2:
            if steps and label:
                steps[0].label = label
            return False
        before: Dict[str, dict] = {}
        after: Dict[str, dict] = {}
        for step in steps:
            for kind, items in step.before.items():
                dst = before.setdefault(kind, {})
                for key, value in items.items():
                    if key not in dst:
                        dst[key] = value
            for kind, items in step.after.items():
                after.setdefault(kind, {}).update(items)
        del self.undo_stack[mark:]
        self.undo_stack.append(
            UndoStep(label or steps[0].label, before, after))
        return True

    @property
    def can_undo(self) -> bool:
        return bool(self.undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self.redo_stack)

    @property
    def undo_label(self) -> Optional[str]:
        return self.undo_stack[-1].label if self.undo_stack else None

    @property
    def redo_label(self) -> Optional[str]:
        return self.redo_stack[-1].label if self.redo_stack else None
