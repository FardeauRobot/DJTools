"""Track links ("goes well with"): the picker for one track, and the window listing every link."""
import datetime
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .. import keys
from . import theme

PATHS_ROLE = Qt.UserRole + 1


def track_label(row):
    return f"{row.artist} – {row.title}" if row.artist else row.title


def mixes_with(source, row, percent):
    """Same rule as "Mix with this": compatible Camelot key, BPM within percent (half and double tempo count)."""
    if source.key and row.key not in keys.neighbours(source.key):
        return False
    if source.bpm:
        if not row.bpm:
            return False
        low, high = source.bpm * (1 - percent / 100), source.bpm * (1 + percent / 100)
        if not any(low <= b <= high for b in (row.bpm, row.bpm * 2, row.bpm / 2)):
            return False
    return True


class LinkPicker(QDialog):
    """Tick the tracks one track goes well with. Ticked on open = already linked."""

    def __init__(self, source, rows, linked, percent, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Goes well with…")
        self.resize(620, 560)
        self.source, self.linked, self.percent = source, set(linked), percent

        title = QLabel(f"Tracks that go well with <b>{track_label(source)}</b>")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, key, BPM…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.compatible = QCheckBox("Only compatible key / BPM")
        self.compatible.setToolTip(f"Camelot neighbours and BPM ±{percent}%, like ⌘K")
        self.compatible.toggled.connect(self._apply_filter)
        self.linked_only = QCheckBox("Only linked")
        self.linked_only.toggled.connect(self._apply_filter)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        others = [r for r in rows if r.path != source.path]
        others.sort(key=lambda r: (r.path not in self.linked, track_label(r).lower()))
        for r in others:
            bits = [track_label(r)]
            if r.key:
                bits.append(keys.display(r.key))
            if r.bpm:
                bits.append(f"{r.bpm:.0f} BPM")
            if r.folder:
                bits.append(r.folder)  # tells copies of the same track apart
            item = QListWidgetItem("  ·  ".join(bits))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if r.path in self.linked else Qt.Unchecked)
            item.setData(Qt.UserRole, r)
            item.setToolTip(r.path)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(
            lambda item: item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        )
        self.list.itemChanged.connect(self._update_count)
        self.count = QLabel()

        hint = QLabel("Space or double-click ticks a track. Links stay in DJTools (rekordbox doesn't get them).")
        hint.setStyleSheet("color: palette(placeholder-text);")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.compatible)
        filters.addWidget(self.linked_only)
        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addLayout(filters)
        layout.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.count)
        bottom.addStretch(1)
        bottom.addWidget(buttons)
        layout.addWidget(hint)
        layout.addLayout(bottom)
        self.search.installEventFilter(self)
        self._update_count()
        self.search.setFocus()

    def eventFilter(self, obj, event):
        # ↓ from the search box goes to the list, so it's all keyboard.
        if obj is self.search and event.type() == event.Type.KeyPress and event.key() == Qt.Key_Down:
            for i in range(self.list.count()):
                if not self.list.item(i).isHidden():
                    self.list.setCurrentRow(i)
                    break
            self.list.setFocus()
            return True
        return super().eventFilter(obj, event)

    def _apply_filter(self, *_):
        words = self.search.text().lower().split()
        for i in range(self.list.count()):
            item = self.list.item(i)
            r = item.data(Qt.UserRole)
            show = all(w in item.text().lower() for w in words)
            if show and self.compatible.isChecked():
                show = mixes_with(self.source, r, self.percent)
            if show and self.linked_only.isChecked():
                show = item.checkState() == Qt.Checked
            item.setHidden(not show)

    def ticked(self):
        return {
            self.list.item(i).data(Qt.UserRole).path
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        }

    def _update_count(self, *_):
        n = len(self.ticked())
        self.count.setText(f"{n} linked" if n else "Nothing linked yet")

    def changes(self):
        """(paths to link, paths to unlink)."""
        ticked = self.ticked()
        return ticked - self.linked, self.linked - ticked


class LinksWindow(QDialog):
    """Every link, searchable. Notes are editable; tracks can be found in the list, played or unlinked."""

    jump_requested = Signal(str)  # select this track in the main list
    play_requested = Signal(str)
    show_pair = Signal(object)  # set of paths to show in the main list
    unlink_requested = Signal(object)  # [(path, path)]
    note_edited = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    TRACK_A, TRACK_B, NOTE, ADDED = range(4)

    def __init__(self, library, model, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Links")
        self.resize(900, 520)
        self.library, self.model = library, model
        self._filling = False

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tracks or notes…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.empty = QLabel(
            "No links yet. Select a track and press ⌘L (or <code>w</code>) to say what it goes well with, "
            "or select several tracks and press ⌘L to link them together."
        )
        self.empty.setWordWrap(True)
        self.empty.setStyleSheet("color: palette(placeholder-text);")

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Track", "Goes well with", "Note", "Added"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setSortingEnabled(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for col, width in ((self.TRACK_A, 280), (self.TRACK_B, 280), (self.ADDED, 90)):
            self.table.setColumnWidth(col, width)
        header.setSectionResizeMode(self.NOTE, QHeaderView.Stretch)
        header.setSortIndicator(self.ADDED, Qt.DescendingOrder)  # newest links first
        self.table.itemChanged.connect(self._note_edited)
        self.table.cellDoubleClicked.connect(self._double_clicked)
        self.table.itemSelectionChanged.connect(self._update_buttons)

        self.show_btn = QPushButton("Show in list")
        self.show_btn.setToolTip("Show the selected links' tracks in the main list")
        self.show_btn.clicked.connect(self._show_selected)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setToolTip("Play the track in the column you clicked")
        self.play_btn.clicked.connect(self._play_current)
        self.unlink_btn = QPushButton("Unlink")
        self.unlink_btn.setToolTip("Remove the selected links (⌘Z brings them back)")
        self.unlink_btn.clicked.connect(self._unlink_selected)
        self.missing_btn = QPushButton("Remove missing…")
        self.missing_btn.setToolTip("Links to files that aren't in the library any more")
        self.missing_btn.clicked.connect(self._unlink_missing)
        self.hint_text = "Double-click a track to find it, or a note to edit it."
        self.hint = hint = QLabel(self.hint_text)
        hint.setStyleSheet("color: palette(placeholder-text);")
        self._hint_timer = QTimer(self, singleShot=True, timeout=lambda: self.hint.setText(self.hint_text))
        # This is its own window: the main window's ⌘Z doesn't reach it.
        for seq, signal in ((QKeySequence.Undo, self.undo_requested), (QKeySequence.Redo, self.redo_requested)):
            action = QAction(self)
            action.setShortcuts(seq)
            action.triggered.connect(lambda _checked=False, s=signal: s.emit())
            self.addAction(action)

        buttons = QHBoxLayout()
        for b in (self.show_btn, self.play_btn, self.unlink_btn):
            buttons.addWidget(b)
        buttons.addWidget(hint, 1)
        buttons.addWidget(self.missing_btn)
        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self._filling = True
        sort_col = self.table.horizontalHeader().sortIndicatorSection()
        sort_order = self.table.horizontalHeader().sortIndicatorOrder()
        self.table.setSortingEnabled(False)
        links = self.library.cache.links()
        self.table.setRowCount(len(links))
        self.missing = []
        for i, link in enumerate(links):
            pair = (link["a"], link["b"])
            for col, path in ((self.TRACK_A, link["a"]), (self.TRACK_B, link["b"])):
                row = self.model.row_for(path)
                item = QTableWidgetItem(track_label(row) if row else f"{os.path.basename(path)} (missing)")
                item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                item.setToolTip(path)
                item.setData(Qt.UserRole, path)
                if row is None:
                    item.setForeground(theme.WARN)
                    if pair not in self.missing:
                        self.missing.append(pair)
                self.table.setItem(i, col, item)
            note = QTableWidgetItem(link["note"])
            note.setData(PATHS_ROLE, pair)
            note.setToolTip("Double-click to write a note (a cue, where to drop it…)")
            self.table.setItem(i, self.NOTE, note)
            created = link["created"]
            added = QTableWidgetItem(datetime.date.fromtimestamp(created).isoformat() if created else "")
            added.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(i, self.ADDED, added)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(sort_col, sort_order)
        self._filling = False
        self.empty.setVisible(not links)
        self.missing_btn.setVisible(bool(self.missing))
        self._apply_filter()
        self._update_buttons()

    def _pair(self, row):
        return self.table.item(row, self.NOTE).data(PATHS_ROLE)

    def _selected_pairs(self):
        return [self._pair(i.row()) for i in self.table.selectionModel().selectedRows()]

    def _apply_filter(self, *_):
        words = self.search.text().lower().split()
        for row in range(self.table.rowCount()):
            text = " ".join(self.table.item(row, c).text() for c in (self.TRACK_A, self.TRACK_B, self.NOTE)).lower()
            self.table.setRowHidden(row, not all(w in text for w in words))

    def _update_buttons(self):
        has = bool(self.table.selectionModel().selectedRows())
        for b in (self.show_btn, self.play_btn, self.unlink_btn):
            b.setEnabled(has)

    def _note_edited(self, item):
        if self._filling or item.column() != self.NOTE:
            return
        a, b = item.data(PATHS_ROLE)
        if self.library.set_link_note(a, b, item.text().strip()):
            self.note_edited.emit()

    def show_message(self, text):
        self.hint.setText(text)
        self._hint_timer.start(6000)

    def _double_clicked(self, row, col):
        if col in (self.TRACK_A, self.TRACK_B):
            self.jump_requested.emit(self.table.item(row, col).data(Qt.UserRole))

    def _show_selected(self):
        paths = {p for pair in self._selected_pairs() for p in pair}
        if paths:
            self.show_pair.emit(paths)

    def _play_current(self):
        index = self.table.currentIndex()
        if not index.isValid():
            return
        col = self.TRACK_B if index.column() == self.TRACK_B else self.TRACK_A
        self.play_requested.emit(self.table.item(index.row(), col).data(Qt.UserRole))

    def _unlink_selected(self):
        pairs = self._selected_pairs()
        if pairs:
            self.unlink_requested.emit(pairs)

    def _unlink_missing(self):
        n = len(self.missing)
        text = (
            f"Remove {n} link{'s' * (n != 1)} to files that aren't in the library?\n\n"
            "If the stick isn't plugged in or a folder is still scanning, cancel: the links come back by themselves."
        )
        if QMessageBox.question(self, "Links", text) == QMessageBox.Yes:
            self.unlink_requested.emit(list(self.missing))
