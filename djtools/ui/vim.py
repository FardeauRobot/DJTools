"""Vim-style keyboard control for the main window.

One event filter handles the track table, the folder tree, the tag panel and the search box. Keys Qt already
handles through shortcuts on the table (Space, Enter, Delete, F2, arrows) keep working unchanged. Single-key
track-list commands are looked up in the rebindable keymap (keymap.py).
"""
import sys

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from .help_dialog import show_help
from .track_model import ARTIST, TITLE

# On macOS Qt reports the physical Control key as Meta (Control is Command).
CTRL = Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier



class VimKeys(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.count = ""
        self.pending = ""  # "g" or "d" waiting for its second key
        self.visual = False
        self.anchor = None  # row the extended selection grows from
        w, player = window, window.player
        # What each rebindable track-list key does (keymap.TRACK_KEYS). Counts apply to the moves.
        self.commands = {
            "down": lambda: self._move(self._take_count(), extend=self.visual),
            "up": lambda: self._move(-self._take_count(), extend=self.visual),
            "extend_down": lambda: self._move(self._take_count(), extend=True),
            "extend_up": lambda: self._move(-self._take_count(), extend=True),
            "last": lambda: self._goto(
                self._take_count() - 1 if self.count else w.proxy.rowCount() - 1, extend=self.visual
            ),
            "visual": self._toggle_visual,
            "visual_line": self._toggle_visual,
            "search": lambda: (w.search.setFocus(), w.search.selectAll()),
            "tags": self._focus_tags,
            "play": lambda: w._play_index(w.table.currentIndex()),
            "seek_back": lambda: player.seek_by(-5000),
            "seek_forward": lambda: player.seek_by(5000),
            "seek_back_long": lambda: player.seek_by(-30000),
            "seek_forward_long": lambda: player.seek_by(30000),
            "volume_down": lambda: player.volume_by(-5),
            "volume_up": lambda: player.volume_by(5),
            "compatible": w.show_compatible,
            "link": lambda: (self._leave_visual(collapse=False), w.link_tracks()),
            "show_linked": w.show_linked,
            "favorite": w.toggle_favorite,
            "copy_tags": w.copy_tags,
            "paste_tags": w.paste_tags,
            "undo": w.undo,
            "edit_title": lambda: (self._leave_visual(collapse=False), w.edit_cell(TITLE)),
            "edit_artist": lambda: (self._leave_visual(collapse=False), w.edit_cell(ARTIST)),
            "trash": self._trash,
            "move": w.move_selected_dialog,
        }
        for widget in (window.table, window.table.viewport(), window.tree, window.tags, window.search):
            widget.installEventFilter(self)

    # --- dispatch --------------------------------------------------------------------------------

    def eventFilter(self, obj, event):
        w = self.w
        if event.type() == QEvent.MouseButtonPress and obj is w.table.viewport():
            self._leave_visual(collapse=False)
            return False
        if event.type() != QEvent.KeyPress:
            return False
        if obj is w.search:
            return self._search_key(event)
        if obj is w.table:
            return self._table_key(event)
        if obj is w.tree:
            return self._tree_key(event)
        if obj is w.tags:
            return self._tags_key(event)
        return False

    def _message(self, text=""):
        if self.visual:
            text = f"-- VISUAL --  {text}".rstrip()
        if text:
            self.w.statusBar().showMessage(text)
        else:
            self.w.statusBar().clearMessage()

    def _reset(self):
        self.count, self.pending = "", ""
        self._message()

    def _take_count(self, default=1):
        n = int(self.count) if self.count else default
        self.count = ""
        return n

    # --- track table -----------------------------------------------------------------------------

    def _table_key(self, event):
        key, mods, text = event.key(), event.modifiers(), event.text()
        if mods & CTRL:
            if key in (Qt.Key_D, Qt.Key_U):
                step = max(1, self._page_rows() // 2) * self._take_count()
                self._move(step if key == Qt.Key_D else -step, extend=self.visual)
            elif key == Qt.Key_H:
                self._focus_tree()
            elif key == Qt.Key_L:
                self._focus_tags()
            elif key == Qt.Key_R:
                self.w.redo()
            else:
                return False
            self._reset()
            return True

        if key == Qt.Key_Escape:
            self._leave_visual(collapse=True)
            self._reset()
            return True
        if not text or mods & (Qt.ControlModifier | Qt.AltModifier):
            return False

        if text.isdigit() and (self.count or text != "0"):
            self.count += text
            self._message(self.count)
            return True

        if self.pending:
            first, self.pending = self.pending, ""
            if first + text == "gg":
                self._goto(self._take_count() - 1 if self.count else 0, extend=self.visual)
            elif first + text == "dd":
                self._trash()
            self._reset()
            return True

        if text in "gd":
            self.pending = text
            self._message(self.count + text)
            return True
        if text == "?":
            show_help(self.w)
        elif (action := self.w.keymap.track_action(text) or ("volume_up" if text == "+" else None)):
            self.commands[action]()
        elif text.isprintable() and not text.isspace():
            pass  # swallow: don't let the table jump to rows by typed letters
        else:
            return False
        self._reset()
        return True

    def _current_row(self):
        index = self.w.table.currentIndex()
        return index.row() if index.isValid() else 0

    def _page_rows(self):
        table = self.w.table
        return max(1, table.viewport().height() // max(1, table.verticalHeader().defaultSectionSize()))

    def _move(self, delta, extend):
        self._goto(self._current_row() + delta if self.w.table.currentIndex().isValid() else 0, extend)

    def _goto(self, row, extend):
        table, model = self.w.table, self.w.proxy
        if model.rowCount() == 0:
            return
        row = max(0, min(row, model.rowCount() - 1))
        column = table.currentIndex().column() if table.currentIndex().isValid() else 1
        index = model.index(row, column)
        sm = table.selectionModel()
        if extend:
            if self.anchor is None:
                self.anchor = self._current_row() if table.currentIndex().isValid() else row
            top, bottom = sorted((self.anchor, row))
            selection = QItemSelection(model.index(top, 0), model.index(bottom, model.columnCount() - 1))
            sm.select(selection, QItemSelectionModel.ClearAndSelect)
            sm.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        else:
            self.anchor = None
            sm.setCurrentIndex(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        table.scrollTo(index)

    def _toggle_visual(self):
        if self.visual:
            self._leave_visual(collapse=True)
        else:
            self.visual = True
            self.anchor = self._current_row()
            self._goto(self.anchor, extend=True)

    def _leave_visual(self, collapse):
        was = self.visual
        self.visual, self.anchor = False, None
        if was and collapse and self.w.table.currentIndex().isValid():
            self._goto(self._current_row(), extend=False)

    def _trash(self):
        self._leave_visual(collapse=False)
        self.w.trash_selected()

    # --- panes -----------------------------------------------------------------------------------

    def _focus_table(self):
        self.w.table.setFocus()
        self._ensure_selection()

    def _ensure_selection(self):
        """Filtering can leave a current row with nothing selected; select it so tagging has a target."""
        table = self.w.table
        if self.w.proxy.rowCount() and not table.selectionModel().hasSelection():
            self._goto(self._current_row(), extend=False)

    def _focus_tree(self):
        self._leave_visual(collapse=False)
        tree = self.w.tree
        tree.setFocus()
        if not tree.currentIndex().isValid():
            first = tree.model().index(0, 0, tree.rootIndex())
            if first.isValid():
                tree.setCurrentIndex(first)

    def _focus_tags(self):
        self._ensure_selection()
        boxes = self._tag_boxes()
        if not boxes:
            self.w.statusBar().showMessage("Select tracks first to tag them (or switch the tag panel to filtering).", 5000)
            return
        self._leave_visual(collapse=False)
        self._focus_box(boxes[0])

    # --- folder tree -----------------------------------------------------------------------------

    def _tree_key(self, event):
        key, mods, text = event.key(), event.modifiers(), event.text()
        if mods & CTRL and key == Qt.Key_L:
            self._focus_table()
            return True
        if mods & (CTRL | Qt.ControlModifier | Qt.AltModifier):
            return False
        if self.pending == "g":
            self.pending = ""
            if text == "g":
                self._send(self.w.tree, Qt.Key_Home)
            return True
        arrows = {"j": Qt.Key_Down, "k": Qt.Key_Up, "h": Qt.Key_Left, "l": Qt.Key_Right, "G": Qt.Key_End}
        if text in arrows:
            self._send(self.w.tree, arrows[text])
            return True
        if text == "g":
            self.pending = "g"
            return True
        if key == Qt.Key_Escape:
            self._focus_table()
            return True
        if text == self.w.keymap["search"]:
            self.w.search.setFocus()
            return True
        if text == "?":
            show_help(self.w)
            return True
        return False

    @staticmethod
    def _send(widget, key):
        QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))

    # --- tag panel -------------------------------------------------------------------------------

    def _tag_boxes(self):
        return [cb for cb in self.w.tags.boxes.values() if cb.isEnabled()]

    def _focus_box(self, cb):
        cb.setFocus(Qt.ShortcutFocusReason)
        self.w.tags.scroll.ensureWidgetVisible(cb)

    def _tags_key(self, event):
        # Keys the checkboxes ignore bubble up to the panel, so this sees them wherever focus is inside it.
        key, mods, text = event.key(), event.modifiers(), event.text()
        if (mods & CTRL and key == Qt.Key_H) or key == Qt.Key_Escape:
            self._focus_table()
            return True
        boxes = self._tag_boxes()
        focused = QApplication.focusWidget()
        if text in ("j", "k") and boxes:
            i = boxes.index(focused) if focused in boxes else -1
            i = min(i + 1, len(boxes) - 1) if text == "j" else max(i - 1, 0)
            self._focus_box(boxes[i])
            return True
        if (text == "x" or key in (Qt.Key_Return, Qt.Key_Enter)) and focused in boxes:
            focused.click()
            return True
        if text == "?":
            show_help(self.w)
            return True
        return False

    # --- search box ------------------------------------------------------------------------------

    def _search_key(self, event):
        if event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self._focus_table()
            if event.key() != Qt.Key_Escape and self.w.proxy.rowCount():
                self._goto(0, extend=False)
            return True
        return False
