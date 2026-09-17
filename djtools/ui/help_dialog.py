"""The keyboard cheat sheet, grouped by what you are trying to do. Click a key to rebind it; right-click restores it."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from . import keymap as km
from . import theme

# (section, [(keys, description)]). In keys, "@id" is a rebindable track-list key and "#id" a rebindable menu
# shortcut; any other word is a fixed key. "/" between keys reads as "or", "·" leads to the ⌘ equivalent.
# Descriptions can name a track key's current binding as {id}.
COLUMNS = [
    [
        ("Move", [
            ("@down / @up", "down / up  —  5{down} moves 5"),
            ("gg / @last", "first / last  —  12{last} goes to 12"),
            ("Ctrl-d / Ctrl-u", "half a page"),
            ("@extend_down / @extend_up", "extend the selection"),
            ("@visual / @visual_line", "visual select, Esc leaves"),
            ("@search · #m_search", "search, Enter or Esc returns"),
        ]),
        ("Panes", [
            ("Ctrl-h / Ctrl-l", "folders ← tracks → tags"),
            ("@tags", "jump to the tag panel"),
        ]),
        ("In the tag panel & folders", [
            ("j / k", "next / previous"),
            ("Space / x / Enter", "tick / untick a tag"),
            ("h / l", "collapse / expand a folder"),
            ("Esc", "back to the tracks"),
        ]),
    ],
    [
        ("Play", [
            ("@play / Enter", "play"),
            ("Space", "play / pause"),
            ("@seek_back / @seek_forward", "seek 5 s"),
            ("@seek_back_long / @seek_forward_long", "seek 30 s"),
            ("← / →", "seek 10 s"),
            ("@volume_down / @volume_up", "volume"),
        ]),
        ("Mix", [
            ("@compatible · #m_compatible", "tracks that mix with this one"),
            ("@link · #m_link", "link: goes well with…"),
            ("@show_linked · #m_show_linked", "show the linked tracks"),
            ("#m_all_links", "all links"),
            ("#m_clear_filters", "clear filters"),
        ]),
    ],
    [
        ("Edit", [
            ("@favorite", "favorite"),
            ("@copy_tags / @paste_tags · #m_copy_tags / #m_paste_tags", "copy / paste tags"),
            ("@undo / Ctrl-r · #m_undo / #m_redo", "undo / redo"),
            ("@edit_title / @edit_artist · F2", "rename title / artist"),
            ("@move", "move to folder…"),
            ("dd / @trash", "move to Trash, asks first"),
        ]),
        ("Library", [
            ("#m_rescan", "rescan"),
            ("#m_import", "import tracks"),
            ("#m_new_folder", "new folder"),
            ("? · #m_help", "this sheet"),
        ]),
    ],
]

STYLE = f"""
#Section {{ background: {theme.BASE}; border: 1px solid {theme.BORDER}; border-radius: 8px; }}
#SectionTitle {{ color: {theme.ACCENT}; font-weight: 600; letter-spacing: 1px; }}
QLabel[chip="true"] {{ background: {theme.RAISED}; border: 1px solid {theme.BORDER}; border-bottom-width: 2px;
    border-radius: 4px; padding: 1px 6px; font-family: Menlo, monospace; }}
QLabel[chip="true"][fixed="true"] {{ background: transparent; color: {theme.MUTED}; }}
QLabel[chip="true"][fixed="false"]:hover {{ border-color: {theme.ACCENT}; }}
QLabel[chip="true"][custom="true"] {{ color: {theme.ACCENT}; }}
QLabel[chip="true"][capturing="true"] {{ border-color: {theme.ACCENT}; color: {theme.ACCENT}; background: #3a3222; }}
#Sep, #Status {{ color: {theme.MUTED}; }}
#Status[error="true"] {{ color: {theme.TODO.name()}; }}
"""

MODIFIER_KEYS = {Qt.Key_Shift, Qt.Key_Control, Qt.Key_Meta, Qt.Key_Alt, Qt.Key_AltGr, Qt.Key_CapsLock}


def _restyle(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class Chip(QLabel):
    """One key. Fixed ones are dimmed; bound ones show the keymap's current key and start capture on click."""

    def __init__(self, dialog, token):
        super().__init__()
        self.dialog = dialog
        self.action = token[1:] if token[:1] in "@#" and len(token) > 1 else None
        self.setProperty("chip", True)
        self.setProperty("fixed", self.action is None)
        if self.action is None:
            self.setText(token)
            self.setToolTip("Fixed key")
        else:
            self.setCursor(Qt.PointingHandCursor)

    def refresh(self, capturing):
        if self.action is None:
            return
        keymap = self.dialog.keymap
        self.setText("press a key…" if capturing else km.display(self.action, keymap[self.action]))
        default = km.display(self.action, km.default(self.action))
        self.setToolTip(f"{km.label(self.action)}: click to change, right-click for the default ({default})")
        self.setProperty("custom", keymap.is_custom(self.action))
        self.setProperty("capturing", capturing)
        _restyle(self)

    def mousePressEvent(self, event):
        if self.action is None:
            return
        if event.button() == Qt.RightButton:
            self.dialog.reset(self.action)
        else:
            self.dialog.start_capture(self)


class ShortcutsDialog(QDialog):
    HINT = "Click a key to change it, right-click to restore it  ·  ? or Esc closes"

    def __init__(self, keymap, parent=None):
        super().__init__(parent)
        self.keymap = keymap
        self.chips = []
        self.descriptions = []  # (label, template); templates can mention a binding
        self.captured = None  # the chip waiting for a key
        self.setWindowTitle("Keyboard shortcuts")
        self.setStyleSheet(STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 12)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        for sections in COLUMNS:
            col = QVBoxLayout()
            col.setSpacing(12)
            for title, rows in sections:
                col.addWidget(self._section(title, rows))
            col.addStretch()
            columns.addLayout(col)
        outer.addLayout(columns)

        footer = QHBoxLayout()
        self.status = QLabel()
        self.status.setObjectName("Status")
        footer.addWidget(self.status, 1)
        reset_all = QPushButton("Restore all defaults")
        reset_all.setFocusPolicy(Qt.NoFocus)
        reset_all.setAutoDefault(False)
        reset_all.clicked.connect(lambda: self.reset(None))
        footer.addWidget(reset_all)
        outer.addLayout(footer)
        self._say(self.HINT)
        self._refresh()

    def _section(self, title, rows):
        frame = QFrame()
        frame.setObjectName("Section")
        grid = QGridLayout(frame)
        grid.setContentsMargins(12, 10, 12, 12)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        heading = QLabel(title.upper())
        heading.setObjectName("SectionTitle")
        grid.addWidget(heading, 0, 0, 1, 2)
        for i, (keys, text) in enumerate(rows, start=1):
            grid.addWidget(self._keys(keys), i, 0)
            label = QLabel()
            self.descriptions.append((label, text))
            grid.addWidget(label, i, 1)
        grid.setColumnStretch(1, 1)
        return frame

    def _keys(self, spec):
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for i, token in enumerate(spec.split()):
            if i and token in ("/", "·"):  # a leading "/" is the search key itself
                sep = QLabel(token)
                sep.setObjectName("Sep")
                row.addWidget(sep)
            else:
                chip = Chip(self, token)
                self.chips.append(chip)
                row.addWidget(chip)
        row.addStretch()
        return box

    # --- rebinding -------------------------------------------------------------------------------

    def _refresh(self):
        for chip in self.chips:
            chip.refresh(capturing=chip is self.captured)
        keys = {a: self.keymap[a] for a in km.TRACK_KEYS}
        for label, template in self.descriptions:
            label.setText(template.format(**keys))

    def _say(self, text, error=False):
        self.status.setText(text)
        self.status.setProperty("error", error)
        _restyle(self.status)

    def start_capture(self, chip):
        self.captured = None if self.captured is chip else chip
        self.setFocus()
        self._say(f"Press the new key for “{km.label(chip.action)}”  ·  Esc cancels" if self.captured else self.HINT)
        self._refresh()

    def _finish(self, key):
        action, self.captured = self.captured.action, None
        error = self.keymap.check(action, key)
        if error:
            self._say(error, error=True)
        else:
            old = km.display(action, self.keymap[action])
            other = self.keymap.set(action, key)
            new = km.display(action, key)
            moved = f"; “{km.label(other)}” moved to {old}" if other else ""
            self._say(f"{new} is now “{km.label(action)}”{moved}.")
        self._refresh()

    def reset(self, action):
        self.captured = None
        other = self.keymap.reset(action)
        if action is None:
            self._say("Every shortcut is back to its default.")
        else:
            key = km.display(action, self.keymap[action])
            moved = f"; “{km.label(other)}” moved to {km.display(other, self.keymap[other])}" if other else ""
            self._say(f"“{km.label(action)}” is back to {key}{moved}.")
        self._refresh()

    def event(self, event):
        # While capturing, a key that is also a menu shortcut must reach keyPressEvent instead of firing.
        if event.type() == QEvent.ShortcutOverride and self.captured:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if not self.captured:
            if event.text() in ("?", "q"):
                self.accept()
            else:
                super().keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self.captured = None
            self._say(self.HINT)
            self._refresh()
        elif event.key() in MODIFIER_KEYS:
            return
        elif self.captured.action in km.TRACK_KEYS:
            self._finish(event.text())
        else:
            self._finish(QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText))


def show_help(window):
    ShortcutsDialog(window.keymap, window).exec()
