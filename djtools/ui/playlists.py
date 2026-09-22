"""Playlists: the window that builds them, and the picker for adding tracks to one.

Unlike links, playlists are rekordbox's own: what you build here is pushed into master.db at the next
sync and is what rekordbox plays from. Deleting puts a playlist in the trash -- restorable until you
purge it -- because a delete here reaches a playlist you may have made in rekordbox.
"""
import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import cache, keys
from . import icons, theme
from .links import track_label

ID_ROLE = Qt.UserRole + 1
ENTRY_ROLE = Qt.UserRole + 2


def _line(row, path, content_id):
    """One member, the way LinkPicker writes a track, plus why it can't be played when it can't."""
    if row is None:
        if path is None:
            return f"(not in the DJTools library)  ·  rekordbox track {content_id}"
        return f"{os.path.basename(path)}  ·  (missing)"
    bits = [track_label(row)]
    if row.key:
        bits.append(keys.display(row.key))
    if row.bpm:
        bits.append(f"{row.bpm:.0f} BPM")
    if row.folder:
        bits.append(row.folder)
    return "  ·  ".join(bits)


class TrackPicker(QDialog):
    """Tick the tracks to put in a playlist. Ticked on open = already in it."""

    def __init__(self, name, rows, members, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Add tracks to {name}")
        self.resize(620, 560)
        self.members = set(members)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, key, BPM…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.in_playlist = QCheckBox("Only what's already in it")
        self.in_playlist.toggled.connect(self._apply_filter)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(False)
        for r in sorted(rows, key=lambda r: (r.path not in self.members, track_label(r).lower())):
            item = QListWidgetItem(_line(r, r.path, None))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if r.path in self.members else Qt.Unchecked)
            item.setData(Qt.UserRole, r)
            item.setToolTip(r.path)
            self.list.addItem(item)
        self.list.itemDoubleClicked.connect(
            lambda item: item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        )
        self.list.itemChanged.connect(self._update_count)
        self.count = QLabel()

        hint = QLabel("Space or double-click ticks a track. New tracks go to the end; reorder them in the window.")
        hint.setProperty("muted", True)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        filters = QHBoxLayout()
        filters.addWidget(self.search, 1)
        filters.addWidget(self.in_playlist)
        layout = QVBoxLayout(self)
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
            show = all(w in item.text().lower() for w in words)
            if show and self.in_playlist.isChecked():
                show = item.checkState() == Qt.Checked
            item.setHidden(not show)

    def ticked(self):
        return [
            self.list.item(i).data(Qt.UserRole).path
            for i in range(self.list.count())
            if self.list.item(i).checkState() == Qt.Checked
        ]

    def _update_count(self, *_):
        n = len(self.ticked())
        self.count.setText(f"{n} track{'s' * (n != 1)}" if n else "Nothing ticked")

    def changes(self):
        """(paths to add, paths to remove)."""
        ticked = self.ticked()
        return [p for p in ticked if p not in self.members], self.members - set(ticked)


class PlaylistsWindow(QDialog):
    """rekordbox's playlist tree, editable. Drag or ⌥↑/⌥↓ to order a set; ⌘Z undoes any of it."""

    jump_requested = Signal(str)  # select this track in the main list
    play_requested = Signal(str)
    show_playlist = Signal(int)  # playlist id, to narrow the main list to
    edited = Signal()
    undo_requested = Signal()
    redo_requested = Signal()

    def __init__(self, library, model, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Playlists")
        self.resize(940, 560)
        self.library, self.model = library, model
        self._filling = False

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Playlist", "Tracks"])
        self.tree.setColumnWidth(0, 240)
        self.tree.setRootIsDecorated(True)
        self.tree.setAlternatingRowColors(False)
        self.tree.currentItemChanged.connect(lambda *_: self._fill_members())

        self.members = QListWidget()
        self.members.setAlternatingRowColors(False)
        self.members.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.members.setDragDropMode(QAbstractItemView.InternalMove)
        self.members.setDefaultDropAction(Qt.MoveAction)
        self.members.model().rowsMoved.connect(lambda *_: self._order_changed())
        self.members.itemDoubleClicked.connect(self._double_clicked)
        self.members.itemSelectionChanged.connect(self._update_buttons)

        self.new_btn = QPushButton(icons.icon("plus"), " New playlist")
        self.new_btn.clicked.connect(self.new_playlist)
        self.rename_btn = QPushButton(icons.icon("pencil"), " Rename")
        self.rename_btn.clicked.connect(self._rename)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setToolTip("Puts it in the trash below: nothing is lost until you empty it")
        self.delete_btn.clicked.connect(self._delete)
        self.add_btn = QPushButton("Add tracks…")
        self.add_btn.clicked.connect(self.add_tracks)
        self.remove_btn = QPushButton("Remove")
        self.remove_btn.setToolTip("Take the selected tracks out of this playlist (⌘Z brings them back)")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.up_btn = QPushButton("↑")
        self.up_btn.setToolTip("Move the selected tracks up (⌥↑)")
        self.up_btn.clicked.connect(lambda: self._nudge(-1))
        self.down_btn = QPushButton("↓")
        self.down_btn.setToolTip("Move the selected tracks down (⌥↓)")
        self.down_btn.clicked.connect(lambda: self._nudge(1))
        self.show_btn = QPushButton("Show in list")
        self.show_btn.clicked.connect(self._show_playlist)
        self.play_btn = QPushButton(icons.icon("play"), " Play")
        self.play_btn.clicked.connect(self._play_current)

        self.trash = QListWidget()
        self.trash.setMaximumHeight(110)
        self.trash.setAlternatingRowColors(False)
        self.trash.itemSelectionChanged.connect(self._update_buttons)
        self.trash_label = QLabel("Trash — deleted from rekordbox at the next sync, kept here until you empty it")
        self.trash_label.setProperty("muted", True)
        self.restore_btn = QPushButton("Restore")
        self.restore_btn.clicked.connect(self._restore)
        self.purge_btn = QPushButton("Delete forever…")
        self.purge_btn.clicked.connect(self._purge)

        self.hint_text = "Drag or ⌥↑ / ⌥↓ to order a set. Double-click a track to find it in the list."
        self.hint = QLabel(self.hint_text)
        self.hint.setProperty("muted", True)
        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(lambda: self.hint.setText(self.hint_text))
        # This is its own window: the main window's ⌘Z doesn't reach it.
        for seq, signal in ((QKeySequence.Undo, self.undo_requested), (QKeySequence.Redo, self.redo_requested)):
            action = QAction(self)
            action.setShortcuts(seq)
            action.triggered.connect(lambda _checked=False, s=signal: s.emit())
            self.addAction(action)
        for seq, delta in (("Alt+Up", -1), ("Alt+Down", 1)):
            action = QAction(self)
            action.setShortcut(QKeySequence(seq))
            action.triggered.connect(lambda _checked=False, d=delta: self._nudge(d))
            self.addAction(action)

        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(self.tree, 1)
        tree_buttons = QHBoxLayout()
        for b in (self.new_btn, self.rename_btn, self.delete_btn):
            tree_buttons.addWidget(b)
        left_box.addLayout(tree_buttons)

        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(0, 0, 0, 0)
        self.title = QLabel()
        right_box.addWidget(self.title)
        right_box.addWidget(self.members, 1)
        member_buttons = QHBoxLayout()
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn, self.show_btn, self.play_btn):
            member_buttons.addWidget(b)
        member_buttons.addStretch(1)
        right_box.addLayout(member_buttons)

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 640])

        trash_buttons = QHBoxLayout()
        trash_buttons.addWidget(self.restore_btn)
        trash_buttons.addWidget(self.purge_btn)
        trash_buttons.addStretch(1)
        self.trash_box = QWidget()
        trash_layout = QVBoxLayout(self.trash_box)
        trash_layout.setContentsMargins(0, 0, 0, 0)
        trash_layout.addWidget(self.trash_label)
        trash_layout.addWidget(self.trash)
        trash_layout.addLayout(trash_buttons)

        layout = QVBoxLayout(self)
        layout.addWidget(split, 1)
        layout.addWidget(self.trash_box)
        layout.addWidget(self.hint)
        self.refresh()

    # --- filling ------------------------------------------------------------------------------------

    def refresh(self):
        current = self.current_id()
        synced = self.library.rekordbox_enabled  # off: nothing is waiting to go anywhere, so no • and no promises
        self.trash_label.setText(
            "Trash — deleted from rekordbox at the next sync, kept here until you empty it" if synced
            else "Trash — kept here until you empty it"
        )
        self._filling = True
        self.tree.clear()
        rows = self.library.playlists()
        counts = {}
        for r in rows:
            counts[r["id"]] = len(self.library.playlist_entries(r["id"]))
        by_parent = {}
        for r in rows:
            by_parent.setdefault(r["parent_id"], []).append(r)
        found = []

        def add(parent_item, parent_id):
            for r in by_parent.get(parent_id, []):
                item = QTreeWidgetItem([r["name"], "" if r["kind"] == cache.FOLDER else str(counts[r["id"]])])
                item.setData(0, ID_ROLE, r["id"])
                if r["kind"] == cache.FOLDER:
                    item.setIcon(0, icons.icon("folder"))
                elif r["kind"] == cache.SMART:
                    item.setForeground(0, theme.WARN)
                    item.setToolTip(0, "Smart playlist: rekordbox builds it from rules, so DJTools leaves it alone")
                if r["rb_id"] is None and synced:  # • marks what rekordbox hasn't been told about yet
                    item.setToolTip(1, "Not in rekordbox yet — sync to push it")
                    item.setText(1, f"{item.text(1)} •".strip())
                (parent_item.addChild(item) if parent_item is not None else self.tree.addTopLevelItem(item))
                if r["id"] == current:
                    found.append(item)
                add(item, r["id"])

        add(None, None)
        self.tree.expandAll()
        self._filling = False
        if found:
            self.tree.setCurrentItem(found[0])
        elif self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        else:
            self._fill_members()
        self._fill_trash()

    def _fill_trash(self):
        self.trash.clear()
        rows = self.library.trashed_playlists()
        for r in rows:
            n = len(self.library.playlist_entries(r["id"]))
            item = QListWidgetItem(f"{r['name']}  ·  {n} track{'s' * (n != 1)}")
            item.setData(ID_ROLE, r["id"])
            blocker = self.library.restore_blocker(r["id"])
            if blocker:
                item.setText(f"{r['name']}  ·  can't be restored here")
                item.setToolTip(blocker)
                item.setForeground(theme.WARN)
            self.trash.addItem(item)
        self.trash_box.setVisible(bool(rows))
        self._update_buttons()

    def _fill_members(self):
        self._filling = True
        self.members.clear()
        row = self.current_playlist()
        if row is None:
            self.title.setText("No playlist selected.")
        elif row["kind"] == cache.FOLDER:
            self.title.setText(f"<b>{row['name']}</b> is a folder.")
        else:
            for path, content_id in self.library.playlist_entries(row["id"]):
                track = self.model.row_for(path) if path else None
                item = QListWidgetItem(_line(track, path, content_id))
                item.setData(ENTRY_ROLE, (path, content_id))
                item.setToolTip(path or f"rekordbox content {content_id}")
                if track is None:
                    item.setForeground(theme.WARN)
                self.members.addItem(item)
            n = self.members.count()
            smart = " — a smart playlist, so rekordbox owns its contents" if row["kind"] == cache.SMART else ""
            self.title.setText(f"<b>{row['name']}</b> · {n} track{'s' * (n != 1)}{smart}")
        self._filling = False
        self._update_buttons()

    # --- what's selected ----------------------------------------------------------------------------

    def current_id(self):
        item = self.tree.currentItem()
        return item.data(0, ID_ROLE) if item else None

    def current_playlist(self):
        playlist_id = self.current_id()
        return self.library.playlist(playlist_id) if playlist_id is not None else None

    def _editable(self):
        """The selected playlist, when it is one DJTools may change."""
        row = self.current_playlist()
        return row if row is not None and row["kind"] == cache.PLAYLIST else None

    def _entries(self):
        return [self.members.item(i).data(ENTRY_ROLE) for i in range(self.members.count())]

    def _selected_paths(self):
        return [i.data(ENTRY_ROLE)[0] for i in self.members.selectedItems() if i.data(ENTRY_ROLE)[0]]

    def _update_buttons(self):
        row = self.current_playlist()
        editable = self._editable() is not None
        has_tracks = bool(self.members.selectedItems())
        self.rename_btn.setEnabled(row is not None and row["kind"] != cache.SMART)
        self.delete_btn.setEnabled(row is not None)
        self.add_btn.setEnabled(editable)
        for b in (self.remove_btn, self.up_btn, self.down_btn):
            b.setEnabled(editable and has_tracks)
        self.show_btn.setEnabled(self.members.count() > 0)
        self.play_btn.setEnabled(has_tracks)
        trashed = bool(self.trash.selectedItems())
        self.restore_btn.setEnabled(trashed)
        self.purge_btn.setEnabled(trashed)

    def show_message(self, text):
        self.hint.setText(text)
        self._hint_timer.start(6000)

    # --- editing ------------------------------------------------------------------------------------

    def new_playlist(self, paths=()):
        parent = self.current_playlist()
        parent_id = parent["id"] if parent is not None and parent["kind"] == cache.FOLDER else None
        where = f" in {parent['name']}" if parent_id is not None else ""
        name, ok = QInputDialog.getText(self, "New playlist", f"Name of the new playlist{where}:")
        name = name.strip()
        if not ok or not name:
            return None
        playlist_id = self.library.create_playlist(name, parent_id, paths)
        self.edited.emit()
        self.refresh()
        self._select(playlist_id)
        self.show_message(
            f"Created {name}. It reaches rekordbox at the next sync." if self.library.rekordbox_enabled
            else f"Created {name}."
        )
        return playlist_id

    def _select(self, playlist_id):
        for item in self.tree.findItems("", Qt.MatchContains | Qt.MatchRecursive):
            if item.data(0, ID_ROLE) == playlist_id:
                self.tree.setCurrentItem(item)
                return

    def _rename(self):
        row = self.current_playlist()
        if row is None or row["kind"] == cache.SMART:
            return
        name, ok = QInputDialog.getText(self, "Rename playlist", "New name:", text=row["name"])
        if ok and name.strip() and self.library.rename_playlist(row["id"], name.strip()):
            self.edited.emit()
            self.refresh()

    def _delete(self):
        row = self.current_playlist()
        if row is None:
            return
        blocker = self.library.playlist_blockers(row["id"])
        if blocker:
            QMessageBox.information(self, "Playlists", blocker)
            return
        n = len(self.library.playlist_entries(row["id"]))
        if not self.library.rekordbox_enabled:
            where = "Syncing with rekordbox is off, so this only changes DJTools"
        elif row["rb_id"] is None:
            where = "It is only here so far"
        else:
            where = "It will be deleted from rekordbox at the next sync"
        text = (
            f"Delete “{row['name']}” ({n} track{'s' * (n != 1)})?\n\n"
            f"{where}. It goes to the trash, so you can put it back — and ⌘Z undoes this straight away. "
            "The tracks themselves are not touched."
        )
        if QMessageBox.question(self, "Playlists", text) != QMessageBox.Yes:
            return
        self.library.trash_playlist(row["id"])
        self.edited.emit()
        self.refresh()

    def _restore(self):
        blocked = []
        for item in self.trash.selectedItems():
            blocker = self.library.restore_blocker(item.data(ID_ROLE))
            if blocker:
                blocked.append(blocker)
            else:
                self.library.restore_playlist(item.data(ID_ROLE))
        self.edited.emit()
        self.refresh()
        if blocked:
            QMessageBox.information(self, "Playlists", "\n\n".join(blocked))

    def _purge(self):
        items = self.trash.selectedItems()
        if not items:
            return
        names = ", ".join(i.text().split("  ·  ")[0] for i in items)
        text = (
            f"Delete {names} forever?\n\nThis is the one thing ⌘Z cannot undo: the playlist and its order "
            "are gone from DJTools for good. rekordbox is not touched — it lost them at the sync that "
            "followed the delete."
        )
        if QMessageBox.question(self, "Playlists", text) != QMessageBox.Yes:
            return
        for item in items:
            self.library.purge_playlist(item.data(ID_ROLE))
        self.edited.emit()
        self.refresh()
        self.show_message(f"Deleted {names} for good.")

    def add_tracks(self, paths=None):
        row = self._editable()
        if row is None:
            return
        if paths is None:
            members = [p for p, _c in self.library.playlist_entries(row["id"]) if p]
            dialog = TrackPicker(row["name"], self.model.rows, members, self)
            if dialog.exec() != QDialog.Accepted:
                return
            add, remove = dialog.changes()
            added = self.library.add_to_playlist(row["id"], add)
            removed = self.library.remove_from_playlist(row["id"], remove)
        else:
            added, removed = self.library.add_to_playlist(row["id"], paths), 0
        self.edited.emit()
        self.refresh()
        if added or removed:
            parts = [f"added {added}"] * bool(added) + [f"removed {removed}"] * bool(removed)
            self.show_message(f"{row['name']}: {', '.join(parts)}. ⌘Z undoes it.")

    def _remove_selected(self):
        row = self._editable()
        rows = sorted(self.members.row(i) for i in self.members.selectedItems())
        if row is None or not rows:
            return
        keep = [e for i, e in enumerate(self._entries()) if i not in set(rows)]
        self.library.set_playlist_members(row["id"], keep)
        self.edited.emit()
        self.refresh()
        self.show_message(f"Removed {len(rows)} track{'s' * (len(rows) != 1)}. ⌘Z brings them back.")

    def _nudge(self, delta):
        """Move the selected tracks one place up or down, keeping them selected."""
        row = self._editable()
        picked = sorted(self.members.row(i) for i in self.members.selectedItems())
        if row is None or not picked:
            return
        entries = self._entries()
        order = list(range(len(entries)))
        picked_set = set(picked)
        for i in picked if delta < 0 else reversed(picked):
            target = i + delta
            if target < 0 or target >= len(order) or order[target] in picked_set:
                continue  # already against the edge, or swapping with another selected track
            order[i], order[target] = order[target], order[i]
            picked_set.discard(i)
            picked_set.add(target)
        moved = [entries[i] for i in order]
        if moved == entries:
            return
        self.library.set_playlist_members(row["id"], moved)
        self.edited.emit()
        self.refresh()
        for i in range(self.members.count()):
            self.members.item(i).setSelected(i in picked_set)

    def _order_changed(self):
        """A drag finished: the list widget already holds the new order."""
        row = self._editable()
        if self._filling or row is None:
            return
        if self.library.set_playlist_members(row["id"], self._entries()):
            self.edited.emit()
            self.show_message("Reordered. ⌘Z puts it back.")

    # --- getting out to the main window --------------------------------------------------------------

    def _double_clicked(self, item):
        path = item.data(ENTRY_ROLE)[0]
        if path:
            self.jump_requested.emit(path)

    def _show_playlist(self):
        row = self.current_playlist()
        if row is None:
            return
        if not any(p and self.model.row_for(p) for p, _c in self.library.playlist_entries(row["id"])):
            self.show_message("None of those tracks are in the library right now.")
            return
        self.show_playlist.emit(row["id"])

    def _play_current(self):
        paths = self._selected_paths()
        if paths:
            self.play_requested.emit(paths[0])
