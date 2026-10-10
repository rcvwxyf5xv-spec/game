"""A small retained-mode widget toolkit on plain pygame.

Every widget has a `rect` (screen coords), `draw(surface, ui)` and
`handle(event, ui) -> bool` (True = consumed). `ui` is the App: it owns
keyboard focus (`ui.focus`), the mouse position (`ui.mouse`), modifier
state (`ui.mods`), the clipboard and popup menus. Containers dispatch
events to children front-to-back and draw back-to-front.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence

import pygame

from maker.gui import theme as T

CMD_MODS = pygame.KMOD_META | pygame.KMOD_CTRL


def cmd_down(mods: int) -> bool:
    return bool(mods & CMD_MODS)


class Widget:
    def __init__(self, rect=None, tooltip: str = "") -> None:
        self.rect = pygame.Rect(rect or (0, 0, 0, 0))
        self.visible = True
        self.tooltip = tooltip

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)

    def draw(self, surface, ui) -> None:
        pass

    def handle(self, ev, ui) -> bool:
        return False

    def hit(self, pos) -> bool:
        return self.visible and self.rect.collidepoint(pos)


class Container(Widget):
    def __init__(self, rect=None) -> None:
        super().__init__(rect)
        self.children: List[Widget] = []

    def add(self, w: Widget) -> Widget:
        self.children.append(w)
        return w

    def clear(self) -> None:
        self.children = []

    def draw(self, surface, ui) -> None:
        for c in self.children:
            if c.visible:
                c.draw(surface, ui)

    def handle(self, ev, ui) -> bool:
        for c in reversed(self.children):
            if c.visible and c.handle(ev, ui):
                return True
        return False

    def widget_at(self, pos):
        for c in reversed(self.children):
            if not c.hit(pos):
                continue
            if isinstance(c, Container):
                inner = c.widget_at(pos)
                if inner is not None:
                    return inner
            return c
        return None


class Label(Widget):
    def __init__(self, text="", color=T.TEXT, size=13, bold=False,
                 mono=False, rect=None) -> None:
        super().__init__(rect)
        self.text = text
        self.color = color
        self.size = size
        self.bold = bold
        self.mono = mono

    def draw(self, surface, ui) -> None:
        s = self.text() if callable(self.text) else self.text
        h = T.font(self.size, self.mono, self.bold).get_height()
        T.text(surface, s, (self.rect.x, self.rect.centery - h // 2),
               self.color, self.size, self.mono, self.bold,
               max_w=self.rect.w)


class Button(Widget):
    """Push button; `selected` (bool or callable) draws it highlighted,
    `enabled` (bool or callable) greys it out; `swatch` draws a colour
    chip left of the text."""

    def __init__(self, text, on_click: Optional[Callable] = None,
                 selected=False, enabled=True, tooltip="", swatch=None,
                 rect=None, size=13, align="center") -> None:
        super().__init__(rect, tooltip)
        self.text = text
        self.on_click = on_click
        self.selected = selected
        self.enabled = enabled
        self.swatch = swatch
        self.size = size
        self.align = align
        self._down = False

    def is_selected(self) -> bool:
        return self.selected() if callable(self.selected) else self.selected

    def is_enabled(self) -> bool:
        return self.enabled() if callable(self.enabled) else self.enabled

    def draw(self, surface, ui) -> None:
        r = self.rect
        hover = r.collidepoint(ui.mouse) and ui.modal_ok(self)
        if self.is_selected():
            bg = T.SELECT
        elif self._down and hover:
            bg = T.PANEL_3
        elif hover and self.is_enabled():
            bg = T.HOVER
        else:
            bg = T.PANEL_2
        pygame.draw.rect(surface, bg, r, border_radius=4)
        pygame.draw.rect(surface, T.BORDER, r, 1, border_radius=4)
        label = self.text() if callable(self.text) else self.text
        color = T.TEXT if self.is_enabled() else T.TEXT_DIM
        x = r.x + 6
        sw = self.swatch() if callable(self.swatch) else self.swatch
        if sw is not None:
            chip = pygame.Rect(r.x + 5, r.centery - 6, 12, 12)
            pygame.draw.rect(surface, sw, chip)
            pygame.draw.rect(surface, T.BORDER, chip, 1)
            x = chip.right + 5
        f = T.font(self.size)
        w = f.size(str(label))[0]
        if self.align == "center" and sw is None:
            x = r.centerx - w // 2
        T.text(surface, label, (x, r.centery - f.get_height() // 2), color,
               self.size, max_w=r.right - x - 4)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 \
                and self.rect.collidepoint(ev.pos):
            self._down = True
            return True
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self._down:
            self._down = False
            if self.rect.collidepoint(ev.pos) and self.is_enabled() \
                    and self.on_click:
                self.on_click()
            return True
        return False


class TextInput(Widget):
    """Single-line text field. Enter (or losing focus) calls on_submit;
    Esc reverts. `get_value` (callable) refreshes the text whenever the
    field is not focused, so it always mirrors the model."""

    def __init__(self, get_value: Optional[Callable] = None,
                 on_submit: Optional[Callable] = None, placeholder="",
                 rect=None, mono=True, tooltip="") -> None:
        super().__init__(rect, tooltip)
        self.get_value = get_value
        self.on_submit = on_submit
        self.placeholder = placeholder
        self.mono = mono
        self.value = ""
        self.cursor = 0
        self.scroll = 0
        self.error = False
        self._sync()

    def _sync(self) -> None:
        if self.get_value is not None:
            v = self.get_value()
            self.value = "" if v is None else str(v)
            self.cursor = len(self.value)

    def focused(self, ui) -> bool:
        return ui.focus is self

    def submit(self, ui) -> None:
        if self.on_submit is not None:
            ok = self.on_submit(self.value)
            self.error = ok is False

    def on_blur(self, ui) -> None:
        self.submit(ui)

    def draw(self, surface, ui) -> None:
        if not self.focused(ui) and not self.error:
            self._sync()
        r = self.rect
        pygame.draw.rect(surface, T.INPUT_BG, r, border_radius=3)
        border = T.ERROR if self.error else (
            T.FOCUS if self.focused(ui) else T.BORDER)
        pygame.draw.rect(surface, border, r, 1, border_radius=3)
        f = T.font(13, self.mono)
        clip = surface.get_clip()
        inner = r.inflate(-8, 0)
        surface.set_clip(inner.clip(clip) if clip else inner)
        cx = f.size(self.value[:self.cursor])[0]
        if cx - self.scroll > inner.w - 2:
            self.scroll = cx - inner.w + 2
        if cx < self.scroll:
            self.scroll = cx
        ty = r.centery - f.get_height() // 2
        if self.value:
            surface.blit(f.render(self.value, True, T.TEXT),
                         (inner.x - self.scroll, ty))
        elif not self.focused(ui):
            surface.blit(f.render(self.placeholder, True, T.TEXT_DIM),
                         (inner.x, ty))
        if self.focused(ui) and (pygame.time.get_ticks() // 500) % 2 == 0:
            x = inner.x + cx - self.scroll
            pygame.draw.line(surface, T.TEXT, (x, ty), (x, ty + f.get_height()))
        surface.set_clip(clip)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            if self.rect.collidepoint(ev.pos):
                ui.set_focus(self)
                f = T.font(13, self.mono)
                rel = ev.pos[0] - (self.rect.x + 4) + self.scroll
                self.cursor = len(self.value)
                for i in range(len(self.value) + 1):
                    if f.size(self.value[:i])[0] >= rel:
                        self.cursor = i
                        break
                return True
            return False
        if not self.focused(ui):
            return False
        if ev.type == pygame.TEXTINPUT:
            self.value = (self.value[:self.cursor] + ev.text
                          + self.value[self.cursor:])
            self.cursor += len(ev.text)
            self.error = False
            return True
        if ev.type == pygame.KEYDOWN:
            k = ev.key
            if k in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.submit(ui)
                if not self.error:
                    ui.set_focus(None, blur=False)
            elif k == pygame.K_ESCAPE:
                self.error = False
                self._sync()
                ui.set_focus(None, blur=False)
            elif k == pygame.K_TAB:
                self.submit(ui)
                ui.focus_next(self, backwards=bool(ev.mod & pygame.KMOD_SHIFT))
            elif k == pygame.K_BACKSPACE:
                if cmd_down(ev.mod):
                    self.value = self.value[self.cursor:]
                    self.cursor = 0
                elif self.cursor > 0:
                    self.value = (self.value[:self.cursor - 1]
                                  + self.value[self.cursor:])
                    self.cursor -= 1
            elif k == pygame.K_DELETE:
                self.value = (self.value[:self.cursor]
                              + self.value[self.cursor + 1:])
            elif k == pygame.K_LEFT:
                self.cursor = max(0, self.cursor - 1)
            elif k == pygame.K_RIGHT:
                self.cursor = min(len(self.value), self.cursor + 1)
            elif k == pygame.K_HOME or (k == pygame.K_a and cmd_down(ev.mod)):
                self.cursor = 0 if k == pygame.K_HOME else len(self.value)
            elif k == pygame.K_END:
                self.cursor = len(self.value)
            elif k == pygame.K_v and cmd_down(ev.mod):
                clip = ui.clipboard_get().replace("\n", " ")
                self.value = (self.value[:self.cursor] + clip
                              + self.value[self.cursor:])
                self.cursor += len(clip)
            elif k == pygame.K_c and cmd_down(ev.mod):
                ui.clipboard_set(self.value)
            return True  # focused fields swallow all keys
        return False


class TextArea(Widget):
    """Multi-line plain-text editor (raw JSON editing): arrows, Home/End,
    PgUp/PgDn, Enter keeps indentation, Tab = 2 spaces, Cmd+V/C (whole
    text when nothing else), mouse placement, wheel scrolling."""

    def __init__(self, rect=None, on_change: Optional[Callable] = None) -> None:
        super().__init__(rect)
        self.lines: List[str] = [""]
        self.row = 0
        self.col = 0
        self.top = 0
        self.left = 0
        self.on_change = on_change
        self.error_line: Optional[int] = None
        self.dirty = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def set_text(self, s: str) -> None:
        self.lines = s.split("\n") or [""]
        self.row = self.col = self.top = self.left = 0
        self.error_line = None
        self.dirty = False

    def _line_h(self) -> int:
        return T.font(12, True).get_linesize()

    def _visible_rows(self) -> int:
        return max(1, (self.rect.h - 8) // self._line_h())

    def _changed(self) -> None:
        self.dirty = True
        self.error_line = None
        if self.on_change:
            self.on_change()

    def _ensure_visible(self) -> None:
        vr = self._visible_rows()
        if self.row < self.top:
            self.top = self.row
        elif self.row >= self.top + vr:
            self.top = self.row - vr + 1

    def insert(self, s: str) -> None:
        line = self.lines[self.row]
        before, after = line[:self.col], line[self.col:]
        parts = s.split("\n")
        if len(parts) == 1:
            self.lines[self.row] = before + s + after
            self.col += len(s)
        else:
            self.lines[self.row] = before + parts[0]
            mid = parts[1:-1]
            last = parts[-1]
            self.lines[self.row + 1:self.row + 1] = mid + [last + after]
            self.row += len(parts) - 1
            self.col = len(last)
        self._changed()
        self._ensure_visible()

    def draw(self, surface, ui) -> None:
        r = self.rect
        pygame.draw.rect(surface, T.INPUT_BG, r)
        pygame.draw.rect(surface, T.FOCUS if ui.focus is self else T.BORDER,
                         r, 1)
        f = T.font(12, True)
        lh = self._line_h()
        gutter = f.size("0000")[0] + 8
        clip = surface.get_clip()
        surface.set_clip(r.inflate(-2, -2))
        cw = f.size("M")[0]
        vr = self._visible_rows()
        # horizontal scroll follows the cursor
        text_w = r.w - gutter - 10
        if (self.col - self.left) * cw > text_w:
            self.left = self.col - text_w // cw
        if self.col < self.left:
            self.left = self.col
        for i in range(self.top, min(len(self.lines), self.top + vr)):
            y = r.y + 4 + (i - self.top) * lh
            if i == self.error_line:
                pygame.draw.rect(surface, (90, 30, 30),
                                 (r.x + 1, y, r.w - 2, lh))
            surface.blit(f.render(str(i + 1).rjust(4), True, T.TEXT_DIM),
                         (r.x + 2, y))
            line = self.lines[i][self.left:]
            if line:
                surface.blit(f.render(line[:400], True, T.TEXT),
                             (r.x + gutter, y))
        if ui.focus is self and (pygame.time.get_ticks() // 500) % 2 == 0:
            if self.top <= self.row < self.top + vr:
                x = r.x + gutter + (self.col - self.left) * cw
                y = r.y + 4 + (self.row - self.top) * lh
                pygame.draw.line(surface, T.TEXT, (x, y), (x, y + lh))
        surface.set_clip(clip)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and self.rect.collidepoint(ev.pos):
            if ev.button == 1:
                ui.set_focus(self)
                f = T.font(12, True)
                gutter = f.size("0000")[0] + 8
                lh = self._line_h()
                self.row = min(len(self.lines) - 1, max(
                    0, self.top + (ev.pos[1] - self.rect.y - 4) // lh))
                cw = f.size("M")[0]
                col = self.left + max(0, (ev.pos[0] - self.rect.x - gutter
                                          + cw // 2) // cw)
                self.col = min(len(self.lines[self.row]), col)
            return True
        if ev.type == pygame.MOUSEWHEEL and self.rect.collidepoint(ui.mouse):
            self.top = max(0, min(len(self.lines) - 1, self.top - ev.y * 3))
            return True
        if ui.focus is not self:
            return False
        if ev.type == pygame.TEXTINPUT:
            self.insert(ev.text)
            return True
        if ev.type != pygame.KEYDOWN:
            return False
        k, mods = ev.key, ev.mod
        line = self.lines[self.row]
        if k == pygame.K_ESCAPE:
            ui.set_focus(None)
        elif k in (pygame.K_RETURN, pygame.K_KP_ENTER):
            indent = len(line) - len(line.lstrip(" "))
            extra = 2 if line[:self.col].rstrip().endswith(("{", "[")) else 0
            self.insert("\n" + " " * (indent + extra))
        elif k == pygame.K_TAB:
            self.insert("  ")
        elif k == pygame.K_BACKSPACE:
            if self.col > 0:
                self.lines[self.row] = line[:self.col - 1] + line[self.col:]
                self.col -= 1
                self._changed()
            elif self.row > 0:
                prev = self.lines[self.row - 1]
                self.lines[self.row - 1] = prev + line
                del self.lines[self.row]
                self.row -= 1
                self.col = len(prev)
                self._changed()
        elif k == pygame.K_DELETE:
            if self.col < len(line):
                self.lines[self.row] = line[:self.col] + line[self.col + 1:]
                self._changed()
            elif self.row < len(self.lines) - 1:
                self.lines[self.row] = line + self.lines[self.row + 1]
                del self.lines[self.row + 1]
                self._changed()
        elif k == pygame.K_LEFT:
            if self.col > 0:
                self.col -= 1
            elif self.row > 0:
                self.row -= 1
                self.col = len(self.lines[self.row])
        elif k == pygame.K_RIGHT:
            if self.col < len(line):
                self.col += 1
            elif self.row < len(self.lines) - 1:
                self.row += 1
                self.col = 0
        elif k == pygame.K_UP:
            self.row = max(0, self.row - 1)
        elif k == pygame.K_DOWN:
            self.row = min(len(self.lines) - 1, self.row + 1)
        elif k == pygame.K_PAGEUP:
            self.row = max(0, self.row - self._visible_rows())
        elif k == pygame.K_PAGEDOWN:
            self.row = min(len(self.lines) - 1,
                           self.row + self._visible_rows())
        elif k == pygame.K_HOME:
            self.col = 0
        elif k == pygame.K_END:
            self.col = len(line)
        elif k == pygame.K_v and cmd_down(mods):
            self.insert(ui.clipboard_get().replace("\r\n", "\n"))
        elif k == pygame.K_c and cmd_down(mods):
            ui.clipboard_set(self.text)
            ui.status("copied the whole text")
        else:
            return True
        self.col = min(self.col, len(self.lines[self.row]))
        self._ensure_visible()
        return True


class ListView(Widget):
    """Scrollable list of items (label, value[, colour]). on_select(value)
    on click, on_activate(value) on double-click / Enter."""

    def __init__(self, rect=None, on_select=None, on_activate=None,
                 row_h: int = 20, mono=False) -> None:
        super().__init__(rect)
        self.items: list = []
        self.selected_value = None
        self.on_select = on_select
        self.on_activate = on_activate
        self.row_h = row_h
        self.top = 0
        self.mono = mono
        self._last_click = (0, None)

    def set_items(self, items: Sequence) -> None:
        self.items = [tuple(i) for i in items]
        self.top = max(0, min(self.top, len(self.items) - 1))

    def _visible(self) -> int:
        return max(1, self.rect.h // self.row_h)

    def scroll_to_selected(self) -> None:
        for i, item in enumerate(self.items):
            if item[1] == self.selected_value:
                vis = self._visible()
                if i < self.top or i >= self.top + vis:
                    self.top = max(0, i - vis // 2)
                return

    def draw(self, surface, ui) -> None:
        r = self.rect
        pygame.draw.rect(surface, T.INPUT_BG, r)
        clip = surface.get_clip()
        surface.set_clip(r.clip(clip) if clip else r)
        for i in range(self.top, min(len(self.items), self.top + self._visible() + 1)):
            item = self.items[i]
            y = r.y + (i - self.top) * self.row_h
            row = pygame.Rect(r.x, y, r.w, self.row_h)
            if item[1] == self.selected_value and item[1] is not None:
                pygame.draw.rect(surface, T.SELECT, row)
            elif row.collidepoint(ui.mouse) and ui.modal_ok(self):
                pygame.draw.rect(surface, T.HOVER, row)
            color = item[2] if len(item) > 2 and item[2] else T.TEXT
            T.text(surface, item[0], (r.x + 6, y + 3), color, 12, self.mono,
                   max_w=r.w - 14)
        if len(self.items) > self._visible():
            frac = self._visible() / len(self.items)
            h = max(16, int(r.h * frac))
            y = r.y + int((r.h - h) * self.top
                          / max(1, len(self.items) - self._visible()))
            pygame.draw.rect(surface, T.BORDER, (r.right - 5, y, 4, h))
        surface.set_clip(clip)
        pygame.draw.rect(surface, T.BORDER, r, 1)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEWHEEL and self.rect.collidepoint(ui.mouse):
            maxtop = max(0, len(self.items) - self._visible())
            self.top = max(0, min(maxtop, self.top - ev.y * 2))
            return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 \
                and self.rect.collidepoint(ev.pos):
            i = self.top + (ev.pos[1] - self.rect.y) // self.row_h
            if 0 <= i < len(self.items):
                value = self.items[i][1]
                now = pygame.time.get_ticks()
                double = (self._last_click[1] == value
                          and now - self._last_click[0] < 400)
                self._last_click = (now, value)
                if value is not None:
                    self.selected_value = value
                    if self.on_select:
                        self.on_select(value)
                    if double and self.on_activate:
                        self.on_activate(value)
            return True
        return False


class TabBar(Widget):
    def __init__(self, tabs: List[str], on_change=None, rect=None) -> None:
        super().__init__(rect)
        self.tabs = tabs
        self.current = tabs[0]
        self.on_change = on_change

    def _rects(self):
        out = []
        x = self.rect.x
        f = T.font(12)
        for t in self.tabs:
            w = f.size(t)[0] + 16
            out.append((t, pygame.Rect(x, self.rect.y, w, self.rect.h)))
            x += w + 2
        return out

    def draw(self, surface, ui) -> None:
        pygame.draw.line(surface, T.BORDER, self.rect.bottomleft,
                         self.rect.bottomright)
        for t, r in self._rects():
            sel = t == self.current
            pygame.draw.rect(surface, T.PANEL_3 if sel else T.PANEL_2, r,
                             border_top_left_radius=4,
                             border_top_right_radius=4)
            T.text(surface, t, (r.x + 8, r.y + 5),
                   T.ACCENT if sel else T.TEXT_DIM, 12, bold=sel)

    def select(self, tab: str) -> None:
        if tab in self.tabs and tab != self.current:
            self.current = tab
            if self.on_change:
                self.on_change(tab)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            for t, r in self._rects():
                if r.collidepoint(ev.pos):
                    self.select(t)
                    return True
        return False


class Slider(Widget):
    """Horizontal integer slider; on_change(v) while dragging, on_commit(v)
    on release."""

    def __init__(self, lo, hi, get_value, on_change=None, on_commit=None,
                 color=(200, 200, 200), rect=None) -> None:
        super().__init__(rect)
        self.lo, self.hi = lo, hi
        self.get_value = get_value
        self.on_change = on_change
        self.on_commit = on_commit
        self.color = color
        self.dragging = False
        self.value = get_value()

    def _from_x(self, x) -> int:
        t = (x - self.rect.x) / max(1, self.rect.w)
        return int(round(self.lo + max(0.0, min(1.0, t)) * (self.hi - self.lo)))

    def draw(self, surface, ui) -> None:
        if not self.dragging:
            self.value = self.get_value()
        r = self.rect
        bar = pygame.Rect(r.x, r.centery - 3, r.w, 6)
        pygame.draw.rect(surface, T.INPUT_BG, bar, border_radius=3)
        t = (self.value - self.lo) / max(1, self.hi - self.lo)
        fill = bar.copy()
        fill.w = int(bar.w * t)
        pygame.draw.rect(surface, self.color, fill, border_radius=3)
        x = r.x + int(r.w * t)
        pygame.draw.circle(surface, T.TEXT, (x, r.centery), 6)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 \
                and self.rect.inflate(0, 8).collidepoint(ev.pos):
            self.dragging = True
            self.value = self._from_x(ev.pos[0])
            if self.on_change:
                self.on_change(self.value)
            return True
        if ev.type == pygame.MOUSEMOTION and self.dragging:
            v = self._from_x(ev.pos[0])
            if v != self.value:
                self.value = v
                if self.on_change:
                    self.on_change(v)
            return True
        if ev.type == pygame.MOUSEBUTTONUP and ev.button == 1 and self.dragging:
            self.dragging = False
            if self.on_commit:
                self.on_commit(self.value)
            return True
        return False


class Swatch(Widget):
    def __init__(self, get_color, on_click=None, selected=False, rect=None,
                 tooltip="") -> None:
        super().__init__(rect, tooltip)
        self.get_color = get_color
        self.on_click = on_click
        self.selected = selected

    def draw(self, surface, ui) -> None:
        pygame.draw.rect(surface, tuple(self.get_color()), self.rect)
        sel = self.selected() if callable(self.selected) else self.selected
        pygame.draw.rect(surface, T.ACCENT if sel else T.BORDER, self.rect,
                         2 if sel else 1)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 \
                and self.rect.collidepoint(ev.pos):
            if self.on_click:
                self.on_click()
            return True
        return False


class Image(Widget):
    def __init__(self, get_surface, rect=None, border=True) -> None:
        super().__init__(rect)
        self.get_surface = get_surface
        self.border = border

    def draw(self, surface, ui) -> None:
        img = self.get_surface()
        if img is not None:
            surface.blit(img, self.rect.topleft)
        if self.border:
            pygame.draw.rect(surface, T.BORDER, self.rect, 1)


class Form(Container):
    """Vertical, scrollable stack of rows. add_row(widgets, height,
    widths) lays widgets out horizontally (widths: px, or fractions of
    the remaining width, None = share equally)."""

    def __init__(self, rect=None) -> None:
        super().__init__(rect)
        self.rows: list = []  # (height, [(widget, width_spec)])
        self.scroll = 0
        self.content_h = 0

    def clear(self) -> None:
        super().clear()
        self.rows = []

    def add_row(self, widgets, height=T.ROW_H, widths=None) -> None:
        widths = widths or [None] * len(widgets)
        self.rows.append((height, list(zip(widgets, widths))))
        for w in widgets:
            self.add(w)

    def heading(self, text) -> None:
        self.add_row([Label(text, T.ACCENT, 13, bold=True)], 26)

    def spacer(self, h=6) -> None:
        self.rows.append((h, []))

    def field(self, label, widget, label_w=110, height=T.ROW_H) -> None:
        self.add_row([Label(label, T.TEXT_DIM, 12), widget], height,
                     [label_w, None])

    def layout(self, rect) -> None:
        self.rect = pygame.Rect(rect)
        self._place()

    def _place(self) -> None:
        y = self.rect.y + T.PAD - self.scroll
        inner_w = self.rect.w - 2 * T.PAD - 6
        for h, items in self.rows:
            fixed = sum(w for _, w in items if isinstance(w, int))
            gaps = 4 * max(0, len(items) - 1)
            free = max(0, inner_w - fixed - gaps)
            flex = [w for _, w in items if not isinstance(w, int)]
            n_eq = sum(1 for w in flex if w is None)
            frac_sum = sum(w for w in flex if isinstance(w, float))
            x = self.rect.x + T.PAD
            for widget, spec in items:
                if isinstance(spec, int):
                    w = spec
                elif isinstance(spec, float):
                    w = int(free * spec)
                else:
                    w = int(free * (1 - frac_sum) / max(1, n_eq))
                widget.layout((x, y, w, h))
                x += w + 4
            y += h + 4
        self.content_h = y + self.scroll - self.rect.y

    def draw(self, surface, ui) -> None:
        self._place()
        clip = surface.get_clip()
        surface.set_clip(self.rect.clip(clip) if clip else self.rect)
        for c in self.children:
            if c.visible and c.rect.bottom >= self.rect.y \
                    and c.rect.y <= self.rect.bottom:
                c.draw(surface, ui)
        surface.set_clip(clip)
        if self.content_h > self.rect.h:
            frac = self.rect.h / self.content_h
            h = max(20, int(self.rect.h * frac))
            y = self.rect.y + int((self.rect.h - h) * self.scroll
                                  / max(1, self.content_h - self.rect.h))
            pygame.draw.rect(surface, T.BORDER, (self.rect.right - 5, y, 4, h))

    def handle(self, ev, ui) -> bool:
        pos = getattr(ev, "pos", None)
        if pos is not None and not self.rect.collidepoint(pos) and \
                ev.type == pygame.MOUSEBUTTONDOWN:
            return False
        if super().handle(ev, ui):
            return True
        if ev.type == pygame.MOUSEWHEEL and self.rect.collidepoint(ui.mouse):
            maxs = max(0, self.content_h - self.rect.h)
            self.scroll = max(0, min(maxs, self.scroll - ev.y * 24))
            return True
        return False


class PopupMenu(Widget):
    """Context menu: items are (label, callback) or None (separator)."""

    def __init__(self, items, pos) -> None:
        f = T.font(13)
        w = max([f.size(i[0])[0] for i in items if i] + [80]) + 24
        h = sum(22 if i else 7 for i in items) + 6
        super().__init__((pos[0], pos[1], w, h))
        self.items = items

    def clamp(self, bounds) -> None:
        self.rect.clamp_ip(bounds)

    def _rows(self):
        y = self.rect.y + 3
        for item in self.items:
            h = 22 if item else 7
            yield item, pygame.Rect(self.rect.x + 2, y, self.rect.w - 4, h)
            y += h

    def draw(self, surface, ui) -> None:
        shadow = self.rect.move(3, 3)
        pygame.draw.rect(surface, (0, 0, 0), shadow, border_radius=4)
        pygame.draw.rect(surface, T.PANEL_2, self.rect, border_radius=4)
        pygame.draw.rect(surface, T.BORDER, self.rect, 1, border_radius=4)
        for item, r in self._rows():
            if item is None:
                pygame.draw.line(surface, T.BORDER, (r.x + 4, r.centery),
                                 (r.right - 4, r.centery))
                continue
            enabled = len(item) < 3 or item[2]
            if r.collidepoint(ui.mouse) and enabled:
                pygame.draw.rect(surface, T.SELECT, r, border_radius=3)
            T.text(surface, item[0], (r.x + 10, r.y + 3),
                   T.TEXT if enabled else T.TEXT_DIM, 13)

    def handle(self, ev, ui) -> bool:
        if ev.type == pygame.MOUSEBUTTONDOWN:
            if not self.rect.collidepoint(ev.pos):
                ui.close_menu()
                return True
            return True
        if ev.type == pygame.MOUSEBUTTONUP and self.rect.collidepoint(ev.pos):
            for item, r in self._rows():
                if item and r.collidepoint(ev.pos) and (len(item) < 3 or item[2]):
                    ui.close_menu()
                    item[1]()
                    return True
            return True
        if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
            ui.close_menu()
            return True
        return ev.type in (pygame.KEYDOWN, pygame.TEXTINPUT)


class Dialog(Container):
    """Modal dialog: a message plus optional text fields and buttons.

    fields: [(label, default)], buttons: [(label, callback(values))];
    a callback returning False keeps the dialog open (validation)."""

    def __init__(self, title, message="", fields=(), buttons=(("OK", None),),
                 width=520) -> None:
        super().__init__()
        self.title = title
        self.message = message
        self.width = width
        self.inputs: List[TextInput] = []
        self.field_labels = []
        self.error = ""
        for label, default in fields:
            ti = TextInput(placeholder="")
            ti.value = str(default)
            ti.cursor = len(ti.value)
            ti.on_submit = None
            self.inputs.append(ti)
            self.field_labels.append(label)
            self.add(ti)
        self.buttons = []
        for label, cb in buttons:
            b = Button(label, None)
            b.on_click = (lambda cb=cb: self._press(cb))
            self.buttons.append(b)
            self.add(b)
        self.ui = None

    def values(self) -> List[str]:
        return [ti.value for ti in self.inputs]

    def _press(self, cb) -> None:
        keep = False
        if cb is not None:
            try:
                keep = cb(self.values()) is False
            except Exception as exc:  # show op errors inside the dialog
                self.error = str(exc)
                keep = True
        if not keep and self.ui is not None:
            self.ui.close_dialog(self)

    def _msg_lines(self):
        f = T.font(13)
        out = []
        for para in str(self.message).split("\n"):
            words = para.split(" ")
            line = ""
            for w in words:
                trial = (line + " " + w).strip()
                if f.size(trial)[0] > self.width - 32 and line:
                    out.append(line)
                    line = w
                else:
                    line = trial
            out.append(line)
        return out

    def layout_in(self, screen_rect) -> None:
        msg = self._msg_lines() if self.message else []
        h = 44 + 18 * len(msg) + 34 * len(self.inputs) + 50 + (
            20 if self.error else 0)
        self.rect = pygame.Rect(0, 0, self.width, h)
        self.rect.center = screen_rect.center
        y = self.rect.y + 40 + 18 * len(msg) + 6
        for ti in self.inputs:
            ti.layout((self.rect.x + 150, y, self.rect.w - 166, 24))
            y += 34
        bx = self.rect.right - 16
        for b in reversed(self.buttons):
            w = max(80, T.font(13).size(b.text)[0] + 24)
            bx -= w
            b.layout((bx, self.rect.bottom - 42, w, 28))
            bx -= 8

    def draw(self, surface, ui) -> None:
        self.layout_in(surface.get_rect())
        shade = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        shade.fill((0, 0, 0, 140))
        surface.blit(shade, (0, 0))
        pygame.draw.rect(surface, T.PANEL, self.rect, border_radius=6)
        pygame.draw.rect(surface, T.BORDER, self.rect, 1, border_radius=6)
        T.text(surface, self.title, (self.rect.x + 16, self.rect.y + 12),
               T.ACCENT, 15, bold=True)
        y = self.rect.y + 40
        for line in (self._msg_lines() if self.message else []):
            T.text(surface, line, (self.rect.x + 16, y), T.TEXT, 13)
            y += 18
        for label, ti in zip(self.field_labels, self.inputs):
            T.text(surface, label, (self.rect.x + 16, ti.rect.y + 4),
                   T.TEXT_DIM, 13, max_w=130)
        if self.error:
            T.text(surface, self.error, (self.rect.x + 16,
                                         self.rect.bottom - 64), T.ERROR, 12,
                   max_w=self.rect.w - 32)
        super().draw(surface, ui)

    def handle(self, ev, ui) -> bool:
        self.ui = ui
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER) and self.buttons:
                self.buttons[-1].on_click()
                return True
            if ev.key == pygame.K_ESCAPE:
                ui.close_dialog(self)
                return True
        super().handle(ev, ui)
        return True  # modal: swallow everything
