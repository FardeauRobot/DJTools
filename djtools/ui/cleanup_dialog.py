"""Preview of title / artist / filename fixes. Nothing changes on disk until "Apply"."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import naming
from . import icons, theme
from .theme import EDIT as EDIT_COLOR
from .theme import TODO as WARN_COLOR

APPLY, CURRENT, TITLE, ARTIST, NEW_FILE, NOTES = range(6)
HEADERS = ["", "Current file", "New title", "New artist", "New file", "Notes"]

INTRO = (
    "Proposed fixes, following the library's naming rules. <b>Blue</b> = will change. Double-click a title or "
    "artist to correct it; flagged lines need a look and aren't ticked. Nothing is changed until you apply."
    "<br><br>Only the title and artist are written inside the files; cues, key, BPM and artwork are kept. "
    "Renamed files follow in rekordbox at the next <b>Sync</b>. To refresh the titles shown in rekordbox, select "
    "the tracks there and right-click → <i>Reload Tag</i>."
)


class CleanupDialog(QDialog):
    def __init__(self, library, proposals, focus_paths=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clean up names")
        self.resize(1300, 700)
        self.library = library
        self.root = library.root
        self.tracks = library.cleanup_tracks()
        self.folders = naming.ArtistFolders(self.root)
        self.proposals = proposals
        self.errors = []
        self.applied = 0
        self._filling = False

        intro = QLabel(INTRO)
        intro.setWordWrap(True)
        self.table = QTableWidget(len(proposals), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in ((APPLY, 28), (CURRENT, 330), (TITLE, 230), (ARTIST, 170), (NEW_FILE, 330)):
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(True)
        self.table.itemChanged.connect(self._item_changed)

        all_btn = QPushButton("Tick all safe")
        all_btn.clicked.connect(lambda: self._tick_all(True))
        none_btn = QPushButton("Untick all")
        none_btn.clicked.connect(lambda: self._tick_all(False))
        self.apply_btn = QPushButton()
        self.apply_btn.setProperty("primary", True)  # the one button here that touches files
        self.apply_btn.setDefault(True)
        self.apply_btn.clicked.connect(self._apply)
        cancel = QPushButton("Close")
        cancel.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(all_btn)
        buttons.addWidget(none_btn)
        buttons.addStretch(1)
        buttons.addWidget(cancel)
        buttons.addWidget(self.apply_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)

        self.ticked = {p.path: p.safe for p in proposals}
        self._fill()
        if focus_paths:  # opened from a health check: show its tracks first
            first = next((i for i, p in enumerate(proposals) if p.path in focus_paths), None)
            if first is not None:
                self.table.scrollToItem(self.table.item(first, CURRENT), QAbstractItemView.PositionAtTop)

    def _rel(self, path):
        return os.path.relpath(path, self.root)

    def _fill(self):
        self._filling = True
        for row, p in enumerate(self.proposals):
            self._fill_row(row, p)
        self._filling = False
        self._update_button()

    def _fill_row(self, row, p):
        def cell(text, editable=False, changed=False, tip=None):
            item = QTableWidgetItem(text)
            flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
            item.setFlags(flags | Qt.ItemIsEditable if editable else flags)
            if changed:
                item.setForeground(EDIT_COLOR)
            item.setToolTip(tip or text)
            return item

        tick = QTableWidgetItem()
        if not p.safe:
            tick.setIcon(icons.icon("alert", WARN_COLOR.name(), 13))
        tick.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        tick.setCheckState(Qt.Checked if self.ticked.get(p.path) else Qt.Unchecked)
        if not p.safe:
            tick.setForeground(WARN_COLOR)
        self.table.setItem(row, APPLY, tick)
        self.table.setItem(row, CURRENT, cell(self._rel(p.path), tip=self._current_tip(p)))
        self.table.setItem(row, TITLE, cell(p.new_title, editable=True, changed=p.title_changed,
                                            tip=f"Was: {p.title or '(none)'}"))
        self.table.setItem(row, ARTIST, cell(p.new_artist, editable=True, changed=p.artist_changed,
                                             tip=f"Was: {p.artist or '(none)'}"))
        self.table.setItem(row, NEW_FILE, cell(self._rel(p.new_path) if p.path_changed else "(unchanged)",
                                               changed=p.path_changed))
        notes = cell("; ".join(p.notes))
        if not p.safe:
            notes.setForeground(WARN_COLOR)
        self.table.setItem(row, NOTES, notes)

    @staticmethod
    def _current_tip(p):
        return f"{p.path}\nTitle tag: {p.title or '(none)'}\nArtist tag: {p.artist or '(none)'}"

    def _item_changed(self, item):
        if self._filling:
            return
        row, col = item.row(), item.column()
        p = self.proposals[row]
        if col == APPLY:
            self.ticked[p.path] = item.checkState() == Qt.Checked
            self._update_button()
            return
        if col not in (TITLE, ARTIST):
            return
        title = self.table.item(row, TITLE).text().strip()
        artist = self.table.item(row, ARTIST).text().strip()
        new = naming.propose(self.tracks[p.path], self.root, self.folders, new_title=title, new_artist=artist)
        new.notes = [n for n in new.notes if "disagree" not in n]  # the user has checked the artist
        new.ok = bool(self.tracks[p.path]["readable"])
        self.proposals[row] = new
        was_safe = {q.path: q.safe for q in self.proposals}
        naming.mark_conflicts(self.proposals, edited=new)
        for q in self.proposals:  # untick lines that just became conflicts, re-tick the ones that stopped being
            if q.safe != was_safe[q.path] or q is new:
                self.ticked[q.path] = q.safe and q.changed
        self._fill()

    def _tick_all(self, on):
        self.ticked = {p.path: on and p.safe for p in self.proposals}
        self._fill()

    def _chosen(self):
        return [p for p in self.proposals if self.ticked.get(p.path) and p.changed]

    def _update_button(self):
        n = len(self._chosen())
        self.apply_btn.setText(f"Apply {n} change{'s' * (n != 1)}")
        self.apply_btn.setEnabled(bool(n))

    def _apply(self):
        chosen = self._chosen()
        renames = sum(1 for p in chosen if p.path_changed)
        text = f"Apply {len(chosen)} changes?"
        if renames:
            text += f"\n\n{renames} files will be renamed or moved."
        text += "\n\nA log of every change is saved in DJTools' logs folder."
        if QMessageBox.question(self, "Clean up names", text) != QMessageBox.Yes:
            return
        stop = getattr(self.parent(), "player", None)
        if stop is not None:
            stop.stop_if([p.path for p in chosen])
        self.errors = self.library.apply_cleanup(chosen)
        self.applied = len(chosen) - len(self.errors)
        if self.errors:
            QMessageBox.warning(self, "Clean up names", f"{len(self.errors)} couldn't be changed:\n\n"
                                + "\n".join(self.errors[:20]))
        self.accept()
