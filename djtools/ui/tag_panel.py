"""Right-hand panel: the four tag columns with their values as checkboxes.

They are rekordbox's My Tag columns when rekordbox is on, and local ones (config.LOCAL_COLUMN_PREFIX)
when it is off — same panel either way, so only the wording about syncing changes.

Tag mode: ticking a value applies it to every selected track (a partial tick means some of them have it).
Filter mode: clicking a value cycles must have → exclude → off, and the list is filtered instead.
The number after each tag counts the tracks carrying it in the current list.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import FAVORITE
from . import theme


class TagPanel(QWidget):
    tags_edited = Signal()  # assignments changed: refresh the table
    definitions_edited = Signal()  # columns/values changed: rebuild everything
    filter_changed = Signal(object)  # {"value_ids", "not_ids", "match_any"}

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.selected = []  # paths
        self.boxes = {}  # value id -> QCheckBox
        self.names = {}  # value id -> label without the count
        self.counts = {}
        self.filter_ids = set()  # must have
        self.exclude_ids = set()

        self.mode_btn = QPushButton("Filter by tags")
        self.mode_btn.setCheckable(True)
        self.mode_btn.toggled.connect(self._mode_changed)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        self.match_btn = QPushButton("Match: all ticked tags")
        self.match_btn.setCheckable(True)
        self.match_btn.setToolTip("All: a track needs every ticked tag. Any: one of them is enough.")
        self.match_btn.toggled.connect(self._match_changed)
        self.match_btn.hide()

        self.inner = QWidget()
        self.inner_layout = QVBoxLayout(self.inner)
        self.inner_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.inner)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.mode_btn)
        layout.addWidget(self.hint)
        layout.addWidget(self.match_btn)
        layout.addWidget(scroll, 1)
        self.setMinimumWidth(230)

    @property
    def filtering(self):
        return self.mode_btn.isChecked()

    # --- building --------------------------------------------------------------------------------

    def rebuild(self):
        while self.inner_layout.count():
            item = self.inner_layout.takeAt(0)
            if item.widget():
                item.widget().hide()  # deleteLater waits for the event loop; don't leave it drawn meanwhile
                item.widget().deleteLater()
        self.boxes, self.names = {}, {}
        columns = self.library.cache.columns()
        local = not self.library.rekordbox_enabled
        if not columns:
            msg = self.library.rb_error or "No rekordbox My Tag columns found."
            label = QLabel(f"Tags unavailable.\n\n{msg}")
            label.setWordWrap(True)
            self.inner_layout.addWidget(label)
            self.inner_layout.addStretch(1)
            return
        values = self.library.cache.values()
        live_ids = {v["id"] for v in values}
        self.filter_ids &= live_ids
        self.exclude_ids &= live_ids
        for column in columns:
            box = QGroupBox()
            v_layout = QVBoxLayout(box)
            header = QHBoxLayout()
            title = QLabel(f"<b>{column['name']}</b>")
            rename = QToolButton(text="✎")
            rename.setToolTip("Rename this column" if local else "Rename this My Tag column")
            rename.clicked.connect(lambda _=False, c=column: self._rename_column(c))
            add = QToolButton(text="+")
            add.setToolTip(f"New tag in {column['name']}")
            add.clicked.connect(lambda _=False, c=column: self._add_value(c))
            header.addWidget(title, 1)
            header.addWidget(rename)
            header.addWidget(add)
            v_layout.addLayout(header)
            for value in (v for v in values if v["column_rb_id"] == column["rb_id"]):
                # The • means "rekordbox hasn't got this yet". With rekordbox off nothing is waiting on it.
                unsynced = not local and (value["rb_id"] is None or value["dirty"])
                self.names[value["id"]] = value["name"] + ("  •" if unsynced else "")
                cb = QCheckBox()
                cb.setToolTip("• = not synced to rekordbox yet" if unsynced else "")
                cb.setContextMenuPolicy(Qt.CustomContextMenu)
                cb.customContextMenuRequested.connect(lambda _pos, v=value, w=cb: self._value_menu(v, w))
                cb.clicked.connect(lambda _checked, vid=value["id"]: self._clicked(vid))
                v_layout.addWidget(cb)
                self.boxes[value["id"]] = cb
            self.inner_layout.addWidget(box)
        self.inner_layout.addStretch(1)
        self.refresh_states()

    def set_selection(self, paths):
        self.selected = paths
        self.refresh_states()

    def set_counts(self, counts):
        self.counts = counts
        for vid, cb in self.boxes.items():
            n = counts.get(vid, 0)
            cb.setText(f"{self.names[vid]}  ({n})" if n else self.names[vid])

    def _style(self, cb, excluded):
        font = cb.font()
        font.setStrikeOut(excluded)
        cb.setFont(font)
        cb.setStyleSheet(f"color: {theme.TODO.name()};" if excluded else "")

    def refresh_states(self):
        self.match_btn.setVisible(self.filtering)
        if self.filtering:
            self.hint.setText("Click a tag: ✓ must have → ▬ exclude (struck through) → off.")
            for vid, cb in self.boxes.items():
                cb.setEnabled(True)
                cb.setTristate(True)
                excluded = vid in self.exclude_ids
                cb.setCheckState(Qt.Checked if vid in self.filter_ids else Qt.PartiallyChecked if excluded else Qt.Unchecked)
                self._style(cb, excluded)
            self.set_counts(self.counts)
            return
        n = len(self.selected)
        self.hint.setText(
            "Select tracks, then tick tags to apply them." if n == 0 else f"Tagging {n} track{'s' * (n > 1)}."
        )
        tag_map = self.library.cache.track_tag_map()
        for vid, cb in self.boxes.items():
            self._style(cb, False)
            cb.setEnabled(n > 0)
            have = sum(1 for p in self.selected if vid in tag_map.get(p, ()))
            cb.setTristate(0 < have < n)
            cb.setCheckState(Qt.Unchecked if have == 0 else Qt.Checked if have == n else Qt.PartiallyChecked)
        self.set_counts(self.counts)

    # --- actions ---------------------------------------------------------------------------------

    def filter_state(self):
        if not self.filtering:
            return {"value_ids": set(), "not_ids": set(), "match_any": False}
        return {"value_ids": set(self.filter_ids), "not_ids": set(self.exclude_ids), "match_any": self.match_btn.isChecked()}

    def _mode_changed(self, filtering):
        self.mode_btn.setText("Back to tagging" if filtering else "Filter by tags")
        self.refresh_states()
        self.filter_changed.emit(self.filter_state())

    def _match_changed(self, any_):
        self.match_btn.setText("Match: any ticked tag" if any_ else "Match: all ticked tags")
        self.filter_changed.emit(self.filter_state())

    def clear_filter(self):
        self.filter_ids.clear()
        self.exclude_ids.clear()
        if self.filtering:
            self.refresh_states()
            self.filter_changed.emit(self.filter_state())

    def _clicked(self, value_id):
        if self.filtering:
            if value_id in self.filter_ids:
                self.filter_ids.discard(value_id)
                self.exclude_ids.add(value_id)
            elif value_id in self.exclude_ids:
                self.exclude_ids.discard(value_id)
            else:
                self.filter_ids.add(value_id)
            self.refresh_states()
            self.filter_changed.emit(self.filter_state())
            return
        if not self.selected:
            return
        tag_map = self.library.cache.track_tag_map()
        all_have = all(value_id in tag_map.get(p, ()) for p in self.selected)
        self.library.set_tags([(self.selected, value_id, not all_have)])
        self.refresh_states()
        self.tags_edited.emit()

    def _ask(self, title, label, text=""):
        name, ok = QInputDialog.getText(self, title, label, text=text)
        name = name.strip()
        return name if ok and name else None

    def _rename_column(self, column):
        label = "Column name:" if not self.library.rekordbox_enabled else "Column name (as shown in rekordbox My Tag):"
        name = self._ask("Rename column", label, column["name"])
        if name and name != column["name"]:
            self.library.cache.rename_column(column["rb_id"], name)
            self.definitions_edited.emit()

    def _add_value(self, column):
        name = self._ask("New tag", f"New tag in “{column['name']}”:")
        if not name:
            return
        existing = {v["name"].lower() for v in self.library.cache.values() if v["column_rb_id"] == column["rb_id"]}
        if name.lower() in existing:
            QMessageBox.information(self, "New tag", f"“{name}” already exists in {column['name']}.")
            return
        self.library.cache.add_value(column["rb_id"], name)
        self.definitions_edited.emit()

    def _value_menu(self, value, widget):
        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        delete = menu.addAction("Delete tag…")
        chosen = menu.exec(widget.mapToGlobal(widget.rect().bottomLeft()))
        if chosen == rename:
            name = self._ask("Rename tag", "Tag name:", value["name"])
            if name and name != value["name"]:
                self.library.cache.rename_value(value["id"], name)
                self.definitions_edited.emit()
        elif chosen == delete:
            if value["name"].lower() == FAVORITE.lower():
                note = "\n\nThe ★ column uses this tag; it will be recreated the next time you mark a favorite."
            else:
                note = ""
            answer = QMessageBox.question(
                self,
                "Delete tag",
                f"Delete “{value['name']}” and remove it from every track?"
                + (f"\nrekordbox is updated at the next sync." if self.library.rekordbox_enabled else "")
                + note,
            )
            if answer == QMessageBox.Yes:
                self.library.cache.delete_value(value["id"])
                self.definitions_edited.emit()
