import base64
import os
import subprocess

from PySide6.QtCore import QByteArray, QDir, QItemSelectionModel, QMimeData, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFileIconProvider,
    QFileSystemModel,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QToolBar,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .. import analysis, cache, rekordbox
from ..health import INBOX
from ..library import Library, import_files, scan
from . import delegates, icons, theme
from .cleanup_dialog import CleanupDialog
from .filter_bar import FilterBar
from .health_panel import HealthPanel
from .links import LinkPicker, LinksWindow, track_label
from .player_bar import PlayerBar
from .playlists import PlaylistsWindow
from .tag_panel import TagPanel
from .track_model import (
    ARTIST,
    DATE,
    FAV,
    FOLDER,
    HEADERS,
    HIDDEN_BY_DEFAULT,
    KEY,
    LINKS,
    PATH_ROLE,
    PLAYLISTS,
    RB,
    TAGS,
    TITLE,
    TrackFilter,
    TrackModel,
)
from . import keymap as km
from .help_dialog import show_help
from .vim import VimKeys

MIME_TRACKS = "application/x-djtools-paths"


class LoadWorker(QThread):
    progress = Signal(int, int)
    done = Signal(object, object, int)

    def __init__(self, library):
        super().__init__()
        self.library = library

    def run(self):
        count = 0
        if self.library.root and os.path.isdir(self.library.root):
            count = scan(self.library.root, lambda i, n: self.progress.emit(i, n))
        state, error = self.library.load_rekordbox_state()
        self.done.emit(state, error, count)


class ImportWorker(QThread):
    progress = Signal(int, int)
    done = Signal(list, list)

    def __init__(self, sources, dest, root):
        super().__init__()
        self.sources, self.dest, self.root = sources, dest, root

    def run(self):
        imported, errors = import_files(self.sources, self.dest, self.root, lambda i, n: self.progress.emit(i, n))
        self.done.emit(imported, errors)


class AnalyzeWorker(QThread):
    progress = Signal(int, int)
    done = Signal(int, int, list, str)  # stored, no steady beat, per-file errors, fatal (model didn't load)

    def __init__(self, paths, bpm_range):
        super().__init__()
        self.paths, self.bpm_range = paths, bpm_range
        self.stop = False

    def run(self):
        try:
            stored, skipped, errors = analysis.analyze(
                self.paths, self.bpm_range, lambda i, n: self.progress.emit(i, n), lambda: self.stop
            )
        except Exception as exc:
            self.done.emit(0, 0, [], str(exc))
            return
        self.done.emit(stored, skipped, errors, "")


def _dropped_files(mime):
    """Local file paths from a Finder drag, or [] for anything else (including our own track drags)."""
    if mime.hasFormat(MIME_TRACKS) or not mime.hasUrls():
        return []
    return [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]


class DraggableTrackModel(TrackModel):
    def flags(self, index):
        return super().flags(index) | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return [MIME_TRACKS]

    def mimeData(self, indexes):
        paths = sorted({self.rows[i.row()].path for i in indexes if i.isValid()})
        mime = QMimeData()
        mime.setData(MIME_TRACKS, "\n".join(paths).encode())
        return mime


class FolderTree(QTreeView):
    """Folder browser. Dropping tracks on a folder moves them there; dropping files from Finder copies them in."""

    tracks_dropped = Signal(list, str)
    files_dropped = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DropOnly)

    def dragEnterEvent(self, event):
        mime = event.mimeData()
        event.setAccepted(mime.hasFormat(MIME_TRACKS) or bool(_dropped_files(mime)))

    def dragMoveEvent(self, event):
        event.setAccepted(self.indexAt(event.position().toPoint()).isValid())

    def dropEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            return
        mime = event.mimeData()
        dest = self.model().filePath(index)
        if mime.hasFormat(MIME_TRACKS):
            event.acceptProposedAction()
            self.tracks_dropped.emit(bytes(mime.data(MIME_TRACKS)).decode().splitlines(), dest)
        elif files := _dropped_files(mime):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self.files_dropped.emit(files, dest)


# Bumped whenever the default column widths change: a saved header state restores the old ones
# silently, and the new defaults would never be seen.
LAYOUT_VERSION = 4  # 4: the Date column (a restored 13-column header would never show it)


class _FolderIcons(QFileIconProvider):
    """One flat folder mark instead of the system's blue folders, which are the only saturated
    colour in the app that does not mean a key."""

    def icon(self, arg):
        if isinstance(arg, QFileIconProvider.IconType) or arg.isDir():
            return icons.icon("folder", theme.MUTED, 15)
        return super().icon(arg)


class TrackTable(QTableView):
    """The track list. Files dropped from Finder are copied into the library."""

    files_dropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_row = -1  # read by ui/delegates.py; Qt has no row-level :hover
        self.setMouseTracking(True)

    def _hover(self, row):
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()

    def mouseMoveEvent(self, event):
        super().mouseMoveEvent(event)
        self._hover(self.indexAt(event.position().toPoint()).row())

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hover(-1)

    def dragEnterEvent(self, event):
        if _dropped_files(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if files := _dropped_files(event.mimeData()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            self.files_dropped.emit(files)


class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.setWindowTitle("DJTools")
        self.resize(1400, 850)
        self.library = Library(settings)
        self.settings = settings
        self.keymap = km.Keymap(settings)
        self.menu_actions = {}  # keymap id -> QAction, re-bound when a shortcut changes
        self.keymap.listeners.append(self._apply_menu_keys)
        self.worker = None
        self.import_worker = None
        self.analyze_worker = None
        self.copied_tags = None  # value ids from "Copy tags"
        self.links_window = None
        self.playlists_window = None
        self.banner_kind = None  # what the banner's path filter is: "health", "links" or "playlist"
        self.linked_source = None  # the track whose links are shown, while banner_kind == "links"
        self.shown_playlist = None  # the playlist id shown, while banner_kind == "playlist"

        # Toolbar
        bar = QToolBar()
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        bar.setIconSize(QSize(18, 18))
        self.addToolBar(bar)
        choose = QAction(icons.icon("folder"), "Library folder…", self)
        choose.triggered.connect(self.choose_root)
        bar.addAction(choose)
        self.rescan_action = QAction(icons.icon("refresh"), "Rescan", self)
        self.rescan_action.setToolTip("Rescan files and re-read rekordbox (after importing or analyzing there)")
        self.rescan_action.triggered.connect(self.reload)
        bar.addAction(self.rescan_action)
        cleanup = QAction(icons.icon("sparkle"), "Clean up names…", self)
        cleanup.setToolTip("Preview and fix titles, artists and filenames that don't follow the library's rules")
        cleanup.triggered.connect(lambda: self.open_cleanup())
        bar.addAction(cleanup)
        bar.addSeparator()
        self.sync_action = QAction(icons.icon("upload", theme.ACCENT), "Sync to rekordbox", self)
        self.sync_action.triggered.connect(self.sync)
        bar.addAction(self.sync_action)
        # The one filled control in the app: syncing is the thing everything else leads to.
        if button := bar.widgetForAction(self.sync_action):
            button.setProperty("primary", True)
        self.rb_label = QLabel()
        self.rb_label.setFont(theme.font("caption"))
        self.rb_label.setProperty("muted", True)
        self.rb_label.setContentsMargins(theme.SPACE * 2, 0, 0, 0)
        bar.addWidget(self.rb_label)

        # Left: folders
        self.fs_model = QFileSystemModel(self)
        self.fs_model.setIconProvider(_FolderIcons())
        self.fs_model.setFilter(QDir.AllDirs | QDir.NoDotAndDotDot)
        self.fs_model.setReadOnly(True)  # all moves go through the library so rekordbox paths follow
        self.tree = FolderTree()
        self.tree.setModel(self.fs_model)
        for col in (1, 2, 3):
            self.tree.hideColumn(col)
        self.tree.setHeaderHidden(True)
        self.tree.selectionModel().currentChanged.connect(self._folder_selected)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._folder_menu)
        self.tree.tracks_dropped.connect(self.move_tracks)
        self.tree.files_dropped.connect(self.import_into)
        all_btn = QPushButton("All tracks")
        all_btn.clicked.connect(self._show_all)
        new_folder_btn = QPushButton("New folder…")
        new_folder_btn.clicked.connect(lambda: self.new_folder(self._current_folder()))
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(theme.SPACE, theme.SPACE, 0, theme.SPACE)
        left_layout.setSpacing(theme.SPACE)
        left_layout.addWidget(all_btn)
        left_layout.addWidget(self.tree, 1)
        left_layout.addWidget(new_folder_btn)
        self.health = HealthPanel()
        self.health.check_selected.connect(self._show_check)
        self.health.fix_requested.connect(self._fix_check)
        self.health_report = None
        self.left_split = left_split = QSplitter(Qt.Vertical)
        left_split.addWidget(left)
        left_split.addWidget(self.health)
        left_split.setSizes([420, 380])

        # Center: search + table
        self.model = DraggableTrackModel(self)
        self.model.editor = self._edit_cell
        self.proxy = TrackFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = TrackTable()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(TITLE, Qt.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setDragEnabled(True)
        self.table.setDragDropMode(QAbstractItemView.DragDrop)  # drops from Finder only, see TrackTable
        self.table.setAcceptDrops(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)  # F2 / menu; double-click plays
        self.table.files_dropped.connect(lambda files: self.import_into(files, self._import_destination()))
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        self.table.setAlternatingRowColors(False)  # the delegate draws the row; two treatments fight
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        delegates.install(self.table)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        header.setStretchLastSection(False)
        for col, width in ((FAV, 34), (TITLE, 320), (1 + TITLE, 200), (KEY, 104), (RB, 95),
                          (TAGS, 190), (LINKS, 64), (PLAYLISTS, 74), (FOLDER, 180), (DATE, 100)):
            self.table.setColumnWidth(col, width)
        for col in HIDDEN_BY_DEFAULT:
            self.table.hideColumn(col)
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._header_menu)
        self.table.doubleClicked.connect(self._play_index)
        self.table.clicked.connect(self._cell_clicked)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._track_menu)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search title, artist, key (8A), tag, folder…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda t: self._filter(text=t))
        self.fav_btn = QPushButton(icons.icon("star"), " Favorites")
        self.fav_btn.setCheckable(True)
        self.fav_btn.toggled.connect(lambda on: self._filter(only_favorites=on))
        self.count_label = QLabel()
        self.count_label.setFont(theme.font("caption", tabular=True))
        self.count_label.setProperty("muted", True)
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(theme.SPACE)
        top.addWidget(self.search, 1)
        top.addWidget(self.fav_btn)
        top.addWidget(self.count_label)
        self.filters = FilterBar()
        self.filters.changed.connect(lambda state: self._filter(**state))
        self.filters.clear_all.connect(self.clear_filters)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, theme.SPACE, 0, theme.SPACE)
        center_layout.setSpacing(theme.SPACE)
        center_layout.addLayout(top)
        center_layout.addWidget(self.filters)
        self.banner = QWidget()
        self.banner.setObjectName("Banner")
        self.banner_label = QLabel()
        banner_all = QPushButton("Show all")
        banner_all.clicked.connect(self._clear_check)
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(theme.SPACE + 4, 4, 4, 4)
        banner_layout.addWidget(self.banner_label, 1)
        banner_layout.addWidget(banner_all)
        self.banner.hide()
        center_layout.addWidget(self.banner)
        center_layout.addWidget(self.table, 1)

        # Right: tags
        self.tags = TagPanel(self.library)
        self.tags.tags_edited.connect(self._tags_edited)
        self.tags.definitions_edited.connect(self._definitions_edited)
        self.tags.filter_changed.connect(lambda state: self._filter(**state))

        self.splitter = splitter = QSplitter()
        splitter.addWidget(left_split)
        splitter.addWidget(center)
        splitter.addWidget(self.tags)
        splitter.setSizes([230, 900, 270])
        splitter.setStretchFactor(1, 1)

        self.player = PlayerBar()
        self.player.error.connect(lambda msg: self.statusBar().showMessage(f"Playback error: {msg}", 8000))
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.statusBar().addPermanentWidget(self.progress)

        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(splitter, 1)
        root_layout.addWidget(self.player)
        self.setCentralWidget(root)

        # Keys (scoped to the table so typing in search isn't hijacked).
        # `repeat` is off for everything but seeking. Committing a title edit with Return closes the
        # editor and hands focus back to the table *while the key is still down*, so the next
        # auto-repeat arrives at the table and plays the track the user was only renaming. Holding
        # Backspace walking down the list trashing tracks is the same bug with worse consequences.
        for seq, handler, repeat in (
            (Qt.Key_Space, self.player.toggle, False),
            (Qt.Key_Return, lambda: self._play_index(self.table.currentIndex()), False),
            (QKeySequence.Delete, self.trash_selected, False),
            (Qt.Key_Backspace, self.trash_selected, False),
            (Qt.Key_F2, lambda: self.edit_cell(TITLE), False),
            (Qt.Key_Left, lambda: self.player.seek_by(-10000), True),
            (Qt.Key_Right, lambda: self.player.seek_by(10000), True),
        ):
            shortcut = QShortcut(QKeySequence(seq), self.table)
            shortcut.setContext(Qt.WidgetShortcut)
            shortcut.setAutoRepeat(repeat)
            shortcut.activated.connect(handler)

        self.vim = VimKeys(self)
        self._build_menus()
        self._restore_layout()

        self.rb_running = rekordbox.is_running() if self.library.rekordbox_enabled else False
        self.rb_timer = QTimer(self)
        self.rb_timer.timeout.connect(self._poll_rekordbox)
        self._apply_rekordbox_mode()

        if self.library.root and os.path.isdir(self.library.root):
            self._set_tree_root()
            self.reload()
        else:
            self._update_status()
            self.tags.rebuild()
            if self.library.root:
                self.statusBar().showMessage(f"Library folder not found (is the USB stick plugged in?): {self.library.root}")

    # --- loading ---------------------------------------------------------------------------------

    def choose_root(self):
        start = self.library.root or "/Volumes"
        path = QFileDialog.getExistingDirectory(self, "Choose the folder holding your tracks", start)
        if path:
            self.library.settings.library_root = path
            self._set_tree_root()
            self.reload()

    def _set_tree_root(self):
        self.fs_model.setRootPath(self.library.root)
        self.tree.setRootIndex(self.fs_model.index(self.library.root))
        self.proxy.update(folder=None)

    def _busy(self):
        return any(w is not None and w.isRunning() for w in (self.worker, self.import_worker, self.analyze_worker))

    def reload(self):
        if self._busy():
            return
        self.rescan_action.setEnabled(False)
        self.sync_action.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.statusBar().showMessage(
            "Scanning library and reading rekordbox…" if self.library.rekordbox_enabled else "Scanning library…"
        )
        self.worker = LoadWorker(self.library)
        self.worker.progress.connect(self._scan_progress)
        self.worker.done.connect(self._loaded)
        self.worker.start()

    def _scan_progress(self, i, n):
        self.progress.setRange(0, n)
        self.progress.setValue(i)

    def _loaded(self, state, error, count):
        adopted, orphans = self.library.apply_rekordbox_state(state, error)
        self.progress.hide()
        self.rescan_action.setEnabled(True)
        self._rebuild()
        missing = sum(1 for r in self.model.rows if not r.in_rekordbox)
        msg = f"{count} tracks."
        if not self.library.rekordbox_enabled:
            msg += " rekordbox is off: tags stay in DJTools (Library → Sync tags with rekordbox)."
        elif error:
            msg += f" rekordbox: {error}"
        elif missing:
            msg += f" {missing} not in rekordbox yet: import and analyze them there, then Rescan."
        self.statusBar().showMessage(msg, 15000)
        if adopted or orphans:
            self._report_adoption(adopted, orphans)

    def _report_adoption(self, adopted, orphans):
        """Say what became of the columns tagged while rekordbox was off. They changed identity silently
        otherwise, and the user is about to sync tags they made before rekordbox was in the picture."""
        lines = [f"• {local} → rekordbox's “{rb}”" for local, rb in adopted]
        text = (
            "The tags you made with rekordbox off now belong to its My Tag columns:\n\n"
            + "\n".join(lines)
            + "\n\nSync to rekordbox renames those columns and sends the tags."
        ) if adopted else ""
        if orphans:
            text += (
                ("\n\n" if text else "")
                + "rekordbox had no column left for: " + ", ".join(orphans)
                + ".\nThose tags stay in DJTools and are never synced."
            )
        QMessageBox.information(self, "Tags made without rekordbox", text)

    def _rebuild(self, keep_undo=False):
        if not keep_undo:
            self.library.clear_undo()  # paths or tag definitions may have changed under the recorded steps
        selected = set(self._selected_paths())
        values = self.library.cache.values()
        self.model.links = self.library.link_map()
        self.model.playlists = self.library.playlist_map()
        self.model.playlist_names = {p["id"]: p["name"] for p in self.library.playlists()}
        self.model.rekordbox = self.library.rekordbox_enabled
        self.model.set_data(self.library.rows(), {v["id"]: v["name"] for v in values}, self._favorite_id(create=False))
        self.tags.rebuild()
        self._refresh_health(refilter=True)
        self._restore_selection(selected)
        if self.links_window is not None:
            self.links_window.refresh()
        if self.playlists_window is not None:
            self.playlists_window.refresh()
        self._update_status()

    def _favorite_id(self, create):
        return self.library.favorite_value_id(create=create)

    def _restore_selection(self, paths):
        if not paths:
            self.tags.set_selection([])
            return
        sel = self.table.selectionModel()
        for row in range(self.proxy.rowCount()):
            index = self.proxy.index(row, 0)
            if index.data(PATH_ROLE) in paths:
                sel.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def _poll_rekordbox(self):
        running = rekordbox.is_running()
        if running != self.rb_running:
            self.rb_running = running
            self._update_status()

    # --- rekordbox on or off ---------------------------------------------------------------------

    def _apply_rekordbox_mode(self):
        """Show or hide everything that only means something with rekordbox behind the app."""
        on = self.library.rekordbox_enabled
        self.sync_action.setVisible(on)
        self.table.setColumnHidden(RB, not on)  # after _restore_layout: the saved state may disagree
        if on:
            if not self.rb_timer.isActive():
                self.rb_timer.start(3000)  # a pgrep every 3s, pointless when nothing can be synced
        else:
            self.rb_timer.stop()
            self.rb_running = False
        if hasattr(self, "rb_enabled_action"):
            self.rb_enabled_action.setChecked(on)

    def _toggle_rekordbox(self, on):
        if not on:
            changes, _waiting = self.library.pending_counts()
            if changes and QMessageBox.question(
                self, "Turn rekordbox off",
                f"{changes} change{'s' * (changes != 1)} haven't been synced yet.\n\n"
                "They stay here and are sent when you turn rekordbox back on. Nothing is lost.\n\nTurn it off?",
            ) != QMessageBox.Yes:
                self.rb_enabled_action.setChecked(True)
                return
        self.library.settings.rekordbox_enabled = on
        self._apply_rekordbox_mode()
        self.reload()  # off: drops the state. On: full read, so columns are adopted and tags line up.

    def _update_status(self):
        changes, waiting = self.library.pending_counts()
        running = self.rb_running
        self.sync_action.setText(f"Sync to rekordbox ({changes})" if changes else "Sync to rekordbox")
        self.sync_action.setEnabled(bool(changes) and self.library.rb_state is not None)
        parts = []
        if not self.library.rekordbox_enabled:
            parts.append("rekordbox off · tags stay in DJTools")
        elif self.library.rb_error:
            parts.append(f"<span style='color:{theme.TODO.name()}'>rekordbox unreadable</span>")
        else:
            parts.append("rekordbox open: quit it to sync" if running else "rekordbox closed")
        if waiting:
            parts.append(f"{waiting} tagged track{'s' * (waiting > 1)} waiting for import into rekordbox")
        self.rb_label.setText(" · ".join(parts))
        self.count_label.setText(f"{self.proxy.rowCount()} / {self.model.rowCount()}")
        self.tags.set_counts(self.proxy.value_counts())
        if hasattr(self, "undo_action"):
            self.undo_action.setEnabled(self.library.can_undo)
            self.redo_action.setEnabled(self.library.can_redo)

    # --- filtering / selection -------------------------------------------------------------------

    def _filter(self, **changes):
        self.proxy.update(**changes)
        self._update_status()

    def _folder_selected(self, current, _previous):
        if current.isValid():
            if self.proxy.paths is not None:  # a folder click replaces the health filter rather than combining
                self.banner.hide()
                self.banner_kind = self.linked_source = self.shown_playlist = None
                self.health.clear_selection()
                self.proxy.update(paths=None)
            self._filter(folder=self.fs_model.filePath(current))

    def _show_all(self):
        self.tree.clearSelection()
        self.tree.setCurrentIndex(self.tree.rootIndex())
        self._filter(folder=None)

    def _current_folder(self):
        index = self.tree.currentIndex()
        return self.fs_model.filePath(index) if index.isValid() else self.library.root

    def _selected_paths(self):
        return [i.data(PATH_ROLE) for i in self.table.selectionModel().selectedRows()]

    def _selection_changed(self, *_):
        self.tags.set_selection(self._selected_paths())

    def _tags_edited(self):
        values = self.library.cache.values()
        self.model.refresh_tags(
            {v["id"]: v["name"] for v in values}, self._favorite_id(create=False), self.library.cache.track_tag_map()
        )
        self.proxy.update()
        self._refresh_health(refilter=False)  # keep the shown tracks: tagging one shouldn't make it vanish
        self._update_status()

    def _definitions_edited(self):
        self._rebuild()

    # --- health and cleanup ----------------------------------------------------------------------

    def _refresh_health(self, refilter):
        if not self.library.root:
            return
        self.health_report = self.library.health(self.model.rows, files_changed=refilter)
        self.health.set_report(self.health_report)
        current = self.health.checks.get(self.health.current_id)
        if self.proxy.paths is not None and self.banner_kind == "health":
            if current is None:
                self._clear_check()
            elif refilter:
                self._show_check(current)

    def _show_check(self, check):
        if check.id == "empty_folders":  # folders, not tracks: the panel lists them
            return
        self._show_paths(set(check.paths), f"Showing: <b>{check.label}</b> ({check.count})", "health")

    def _show_paths(self, paths, text, kind):
        """Narrow the list to these tracks, with the banner saying why."""
        if self.proxy.folder:
            self.tree.clearSelection()
            self.tree.setCurrentIndex(self.tree.rootIndex())
        if kind != "health":
            self.health.clear_selection()
        self.banner_kind = kind
        self.proxy.update(folder=None, paths=paths)
        self.banner_label.setText(text)
        self.banner.show()
        self._update_status()

    def _clear_check(self):
        self.banner.hide()
        self.banner_kind = self.linked_source = self.shown_playlist = None
        self.health.clear_selection()
        self._filter(paths=None)

    def _fix_check(self, check):
        if check.fix == "cleanup":
            self.open_cleanup(focus_paths=set(check.paths))
        elif check.fix == "trash":
            self._trash(list(check.paths), what="tracks")
        elif check.fix == "trash_folders":
            text = f"Move {check.count} empty folder{'s' * (check.count != 1)} to the Trash?\n\n" + "\n".join(
                os.path.relpath(p, self.library.root) for p in check.paths[:15]
            )
            if QMessageBox.question(self, "Empty folders", text) != QMessageBox.Yes:
                return
            _trashed, errors = self.library.trash(list(check.paths))
            self._rebuild()
            if errors:
                QMessageBox.warning(self, "Empty folders", "\n".join(errors[:20]))

    def open_cleanup(self, focus_paths=None):
        if not self.library.root:
            return
        if not os.path.isdir(self.library.root):
            QMessageBox.warning(self, "Clean up names", f"The library folder isn't available (is the USB stick plugged in?):\n\n{self.library.root}")
            return
        proposals = self.library.cleanup_proposals()
        if not proposals:
            QMessageBox.information(self, "Clean up names", "Every title, artist and filename already follows the rules.")
            return
        dialog = CleanupDialog(self.library, proposals, focus_paths=focus_paths, parent=self)
        dialog.exec()
        if dialog.applied or dialog.errors:
            self._rebuild()
            self.statusBar().showMessage(
                f"Cleaned up {dialog.applied} track{'s' * (dialog.applied != 1)}."
                + (" Renamed files reach rekordbox at the next Sync; use Reload Tag in rekordbox to refresh titles."
                   if self.library.rekordbox_enabled else ""), 15000
            )

    # --- playback / favorites --------------------------------------------------------------------

    def _play_index(self, index):
        if not index.isValid():
            return
        row = self.model.row_for(index.data(PATH_ROLE))
        if row:
            self.player.play(row.path, row.title, row.artist)

    def _cell_clicked(self, index):
        if index.column() == FAV:
            self.toggle_favorite([index.data(PATH_ROLE)])

    def toggle_favorite(self, paths=None):
        paths = paths or self._selected_paths()
        if not paths:
            return
        # With rekordbox off there is no state either, but the tag columns are local and perfectly usable.
        if self.library.rb_state is None and self.library.rekordbox_enabled:
            QMessageBox.warning(self, "Favorites", "rekordbox's database couldn't be read, so tags are unavailable.")
            return
        fav = self._favorite_id(create=True)
        if fav is None:
            QMessageBox.information(
                self, "Favorites", "Rename one of the tag columns to “Favorite” (the pencil in the tag panel) to use it."
            )
            return
        tag_map = self.library.cache.track_tag_map()
        on = not all(fav in tag_map.get(p, ()) for p in paths)
        self.library.set_tags([(paths, fav, on)])
        self.tags.rebuild()
        self._tags_edited()

    # --- undo, copy / paste tags -----------------------------------------------------------------

    def undo(self):
        if self.library.undo():
            self._after_undo("Undid the last change.")

    def redo(self):
        if self.library.redo():
            self._after_undo("Redid the change.")

    def _after_undo(self, message):
        self.tags.refresh_states()
        self._tags_edited()
        self._links_edited()
        self._playlists_edited()
        self._message(message, 4000)

    def _message(self, text, ms):
        """Status bar, and the other windows' own line while one is open (they hide the status bar)."""
        self.statusBar().showMessage(text, ms)
        for window in (self.links_window, self.playlists_window):
            if window is not None and window.isVisible():
                window.show_message(text)

    def copy_tags(self):
        row = self.model.row_for(self.table.currentIndex().data(PATH_ROLE)) if self.table.currentIndex().isValid() else None
        if row is None:
            return
        self.copied_tags = set(row.value_ids) - {self.model.favorite_id}
        names = sorted(self.model.value_names.get(v, "?") for v in self.copied_tags)
        self.statusBar().showMessage(
            f"Copied tags: {', '.join(names)}. ⌘V adds them to the selected tracks." if names
            else "That track has no tags to copy.", 6000
        )

    def paste_tags(self):
        paths = self._selected_paths()
        if not paths or not self.copied_tags:
            return
        live = {v["id"] for v in self.library.cache.values()}
        if self.library.set_tags([(paths, vid, True) for vid in self.copied_tags if vid in live]):
            self.tags.refresh_states()
            self._tags_edited()
        self.statusBar().showMessage(f"Pasted tags onto {len(paths)} track{'s' * (len(paths) != 1)}. ⌘Z undoes it.", 5000)

    # --- mixing ----------------------------------------------------------------------------------

    def show_compatible(self):
        """Filter to tracks that mix with the playing track (or, with nothing playing, the current one)."""
        row = self.model.row_for(self.player.path) if self.player.path else None
        if row is None and self.table.currentIndex().isValid():
            row = self.model.row_for(self.table.currentIndex().data(PATH_ROLE))
        if row is None:
            return
        if not row.key and not row.bpm:
            self.statusBar().showMessage("That track has no key or BPM yet: analyze it in rekordbox, then Rescan.", 6000)
            return
        label = f"{row.artist} – {row.title}" if row.artist else row.title
        self.filters.match_track(label, row.key, row.bpm, self.settings.get("compatible_bpm_percent", 6))

    def set_bpm_range(self):
        percent, ok = QInputDialog.getInt(
            self, "Mixes with…", "BPM range around the track, in percent:",
            self.settings.get("compatible_bpm_percent", 6), 0, 50,
        )
        if ok:
            self.settings.set("compatible_bpm_percent", percent)

    def clear_filters(self):
        self.search.clear()
        self.fav_btn.setChecked(False)
        self.filters.clear()
        self.tags.clear_filter()
        if self.proxy.paths is not None:
            self._clear_check()
        self._show_all()

    # --- links ("goes well with") ----------------------------------------------------------------

    def _current_row(self):
        """The one selected track; with several (or none) selected, the current row."""
        paths = self._selected_paths()
        if len(paths) == 1:
            return self.model.row_for(paths[0])
        index = self.table.currentIndex()
        return self.model.row_for(index.data(PATH_ROLE)) if index.isValid() else None

    def link_tracks(self):
        """Several tracks selected: link them all together. One: pick what it goes well with."""
        paths = self._selected_paths()
        if len(paths) >= 2:
            pairs = [(x, y) for i, x in enumerate(paths) for y in paths[i + 1:]]
            n = self.library.set_links(pairs, on=True)
            self._links_edited()
            self.statusBar().showMessage(
                f"Linked {len(paths)} tracks together ({n} new link{'s' * (n != 1)}). ⌘Z undoes it." if n
                else "Those tracks were already linked together.", 6000
            )
            return
        row = self._current_row()
        if row is None:
            return
        linked = self.library.link_map().get(row.path, set())
        dialog = LinkPicker(row, self.model.rows, linked, self.settings.get("compatible_bpm_percent", 6), self)
        if dialog.exec() != LinkPicker.Accepted:
            return
        add, remove = dialog.changes()
        added, removed = len(add), len(remove)
        self.library.edit_links([([(row.path, p) for p in add], True), ([(row.path, p) for p in remove], False)])
        self._links_edited()
        if added or removed:
            parts = [f"linked {added}"] * bool(added) + [f"unlinked {removed}"] * bool(removed)
            self.statusBar().showMessage(f"{track_label(row)}: {', '.join(parts)}. ⌘Z undoes it.", 6000)

    def link_with_playing(self):
        playing = self.player.path
        paths = [p for p in self._selected_paths() if p != playing]
        if not playing or not paths:
            return
        n = self.library.set_links([(playing, p) for p in paths], on=True)
        self._links_edited()
        row = self.model.row_for(playing)
        name = track_label(row) if row else os.path.basename(playing)
        self.statusBar().showMessage(
            f"Linked with {name}. ⌘Z undoes it." if n else f"Already linked with {name}.", 6000
        )

    def unlink(self, pairs):
        n = self.library.set_links(pairs, on=False)
        self._links_edited()
        if n:
            self._message(f"Removed {n} link{'s' * (n != 1)}. ⌘Z brings {'them' if n > 1 else 'it'} back.", 6000)

    def show_linked(self, path=None):
        """Narrow the list to a track and everything it goes well with."""
        row = self.model.row_for(path) if path else self._current_row()
        if row is None:
            return
        linked = {p for p in self.library.link_map().get(row.path, set()) if self.model.row_for(p)}
        if not linked:
            self.statusBar().showMessage(f"{track_label(row)} isn't linked to anything here yet: ⌘L links it.", 6000)
            return
        self.linked_source = row.path
        self._show_paths(
            linked | {row.path}, f"Goes well with <b>{track_label(row)}</b> ({len(linked)})", "links"
        )
        self.select_track(row.path)

    def select_track(self, path):
        """Make a track current in the list, clearing filters if they hide it."""
        source = self.model.index_for(path, TITLE)
        if not source.isValid():
            self.statusBar().showMessage(f"Not in the library: {os.path.basename(path)}", 6000)
            return
        index = self.proxy.mapFromSource(source)
        if not index.isValid():
            self.clear_filters()
            index = self.proxy.mapFromSource(source)
        self.table.setCurrentIndex(index)
        self.table.selectionModel().select(index, QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        self.table.scrollTo(index, QAbstractItemView.PositionAtCenter)
        self.activateWindow()
        self.table.setFocus()

    def play_path(self, path):
        row = self.model.row_for(path)
        if row:
            self.player.play(row.path, row.title, row.artist)

    def open_links(self):
        if self.links_window is None:
            self.links_window = LinksWindow(self.library, self.model, self)
            self.links_window.jump_requested.connect(self.select_track)
            self.links_window.play_requested.connect(self.play_path)
            self.links_window.show_pair.connect(
                lambda paths: self._show_paths(paths, f"Showing <b>{len(paths)}</b> linked tracks", "links")
            )
            self.links_window.unlink_requested.connect(self.unlink)
            self.links_window.note_edited.connect(self._update_status)
            self.links_window.undo_requested.connect(self.undo)
            self.links_window.redo_requested.connect(self.redo)
        else:
            self.links_window.refresh()
        self.links_window.show()
        self.links_window.raise_()
        self.links_window.activateWindow()

    def open_playlists(self):
        if self.playlists_window is None:
            self.playlists_window = PlaylistsWindow(self.library, self.model, self)
            self.playlists_window.jump_requested.connect(self.select_track)
            self.playlists_window.play_requested.connect(self.play_path)
            self.playlists_window.show_playlist.connect(self._show_playlist)
            self.playlists_window.edited.connect(self._playlists_edited)
            self.playlists_window.undo_requested.connect(self.undo)
            self.playlists_window.redo_requested.connect(self.redo)
        else:
            self.playlists_window.refresh()
        self.playlists_window.show()
        self.playlists_window.raise_()
        self.playlists_window.activateWindow()

    def _playlists_edited(self):
        """Cheap path after a playlist edit: repaint the column, leave the rows and the filter alone."""
        self.model.set_playlists(
            self.library.playlist_map(), {p["id"]: p["name"] for p in self.library.playlists()}
        )
        if self.banner_kind == "playlist" and self.shown_playlist is not None:
            shown = self._playlist_filter(self.shown_playlist)
            if shown is None:  # trashed or purged while shown
                self._clear_check()
            else:
                self.proxy.update(paths=shown[0])
                self.banner_label.setText(shown[1])
        if self.playlists_window is not None and not self.playlists_window.isActiveWindow():
            self.playlists_window.refresh()
        self._update_status()

    def _pick_playlist(self, title):
        """Choose one editable playlist, with "New playlist…" first. Returns its id, or None."""
        rows = [p for p in self.library.playlists() if p["kind"] == cache.PLAYLIST]
        names = ["New playlist…"] + [p["name"] for p in rows]
        name, ok = QInputDialog.getItem(self, title, "Playlist:", names, 0, False)
        if not ok:
            return None
        if name == "New playlist…":
            new_name, ok = QInputDialog.getText(self, "New playlist", "Name of the new playlist:")
            return self.library.create_playlist(new_name.strip()) if ok and new_name.strip() else None
        return next(p["id"] for p in rows if p["name"] == name)

    def add_to_playlist_dialog(self):
        """Put the selected tracks in a playlist."""
        paths = self._selected_paths()
        if not paths:
            self.statusBar().showMessage("Select the tracks to add first.", 6000)
            return
        playlist_id = self._pick_playlist(f"Add {len(paths)} track{'s' * (len(paths) != 1)} to a playlist")
        if playlist_id is None:
            return
        added = self.library.add_to_playlist(playlist_id, paths)
        name = self.library.playlist(playlist_id)["name"]
        self._playlists_edited()
        if self.playlists_window is not None:
            self.playlists_window.refresh()
        self._message(
            f"Added {added} track{'s' * (added != 1)} to {name}. ⌘Z undoes it." if added
            else f"Already in {name}.", 6000,
        )

    def show_playlists(self, path=None):
        """Narrow the list to a playlist the current track is in."""
        row = self.model.row_for(path) if path else self._current_row()
        if row is None:
            return
        ids = self.library.playlist_map().get(row.path, set())
        names = {p["id"]: p["name"] for p in self.library.playlists()}
        ids = sorted((i for i in ids if i in names), key=lambda i: names[i].lower())
        if not ids:
            self.statusBar().showMessage(
                f"{track_label(row)} isn't in any playlist yet: ⇧P adds it to one.", 6000
            )
            return
        playlist_id = ids[0]
        if len(ids) > 1:
            name, ok = QInputDialog.getItem(
                self, "Show a playlist", "This track is in:", [names[i] for i in ids], 0, False
            )
            if not ok:
                return
            playlist_id = next(i for i in ids if names[i] == name)
        self._show_playlist(playlist_id)
        self.select_track(row.path)

    def _links_edited(self):
        links = self.library.link_map()
        self.model.set_links(links)
        if self.banner_kind == "links" and self.linked_source:
            row = self.model.row_for(self.linked_source)
            linked = {p for p in links.get(self.linked_source, set()) if self.model.row_for(p)}
            if row is not None:
                self.proxy.update(paths=linked | {row.path})
                self.banner_label.setText(f"Goes well with <b>{track_label(row)}</b> ({len(linked)})")
        if self.links_window is not None:
            self.links_window.refresh()
        self._update_status()

    # --- in-place title / artist edits -----------------------------------------------------------

    def edit_cell(self, column):
        index = self.table.currentIndex()
        if index.isValid():
            self.table.setFocus()
            self.table.edit(index.siblingAtColumn(column))

    def _edit_cell(self, path, column, text):
        """Called by the model when a Title or Artist cell is edited: write the tag into the file."""
        self.player.stop_if([path])  # the file is rewritten; don't read it mid-write
        field = "title" if column == TITLE else "artist"
        try:
            self.library.edit_title_artist(path, **{field: text})
        except Exception as exc:
            self.statusBar().showMessage(f"Couldn't write the {field} into {os.path.basename(path)}: {exc}", 10000)
            return False
        self.library.invalidate_health()
        QTimer.singleShot(0, lambda: (self._refresh_health(refilter=False), self._update_status()))
        self.statusBar().showMessage(
            f"Saved the {field} into the file. In rekordbox, right-click the track → Reload Tag to see it.", 8000
        )
        return True

    # --- importing -------------------------------------------------------------------------------

    def _import_destination(self):
        """Where a drop on the track list goes: the folder being shown, else the inbox, else the library root."""
        if self.proxy.folder:
            return self.proxy.folder
        inbox = os.path.join(self.library.root or "", INBOX)
        return inbox if os.path.isdir(inbox) else self.library.root

    def import_dialog(self):
        if not self._library_available("Import"):
            return
        files, _ = QFileDialog.getOpenFileNames(
            self, "Import tracks into the library", os.path.expanduser("~/Downloads"),
            "Audio (*.mp3 *.flac *.wav *.aif *.aiff *.m4a *.aac *.ogg)",
        )
        if files:
            self.import_into(files, self._import_destination())

    def import_into(self, files, dest):
        if not files or not dest or not self._library_available("Import"):
            return
        if self._busy():
            self.statusBar().showMessage("Busy scanning, importing or detecting BPM; try again in a moment.", 5000)
            return
        if not self.library.contains(dest):
            return
        self.progress.setRange(0, 0)
        self.progress.show()
        self.statusBar().showMessage(f"Copying into {os.path.relpath(dest, self.library.root)}…")
        self.import_worker = ImportWorker(files, dest, self.library.root)
        self.import_worker.progress.connect(self._scan_progress)
        self.import_worker.done.connect(self._imported)
        self.import_worker.start()

    def _imported(self, imported, errors):
        self.progress.hide()
        self._rebuild()
        n = len(imported)
        self.statusBar().showMessage(
            f"Imported {n} track{'s' * (n != 1)}. Import them into rekordbox and analyze, then Rescan." if n
            else "Nothing imported.", 15000
        )
        if errors:
            QMessageBox.warning(self, "Import", "\n".join(errors[:20]) + ("\n…" if len(errors) > 20 else ""))

    # --- BPM detection -----------------------------------------------------------------------------

    def detect_bpm_selected(self):
        self.detect_bpm(self._selected_paths())

    def detect_missing_bpm(self):
        paths = [r.path for r in self.model.rows if not r.bpm and r.readable]
        if not paths:
            self.statusBar().showMessage("Every track already has a BPM.", 5000)
            return
        answer = QMessageBox.question(
            self, "Detect missing BPMs",
            f"Detect the BPM of {len(paths)} track{'s' * (len(paths) != 1)} with no BPM from rekordbox or the file?"
            "\n\nIt takes a few seconds per track and runs in the background. Detected values are shown in grey, "
            "stay in DJTools and are never written to rekordbox or the files.",
        )
        if answer == QMessageBox.Yes:
            self.detect_bpm(paths)

    def detect_bpm(self, paths):
        if not paths or not self._library_available("Detect BPM"):
            return
        ok, why = analysis.available()
        if not ok:
            QMessageBox.information(
                self, "Detect BPM",
                "BPM detection needs beat_this (and PyTorch, about 1 GB), which isn't installed.\n\n"
                f"In the DJTools folder, run:\n\n    {analysis.INSTALL_HINT}\n\nthen restart the app.\n\n({why})",
            )
            return
        if self._busy():
            self.statusBar().showMessage("Busy scanning, importing or detecting BPM; try again in a moment.", 5000)
            return
        self.progress.setRange(0, 0)
        self.progress.show()
        self.stop_bpm_action.setEnabled(True)
        self.statusBar().showMessage("Loading the BPM model (the first time downloads about 80 MB)…")
        bpm_range = tuple(self.settings.get("bpm_detect_range", analysis.DEFAULT_RANGE))
        self.analyze_worker = AnalyzeWorker(paths, bpm_range)
        self.analyze_worker.progress.connect(self._detect_progress)
        self.analyze_worker.done.connect(self._bpm_detected)
        self.analyze_worker.start()

    def _detect_progress(self, i, n):
        self._scan_progress(i, n)
        self.statusBar().showMessage(f"Detecting BPM… {i + 1} / {n}")

    def stop_detecting_bpm(self):
        if self.analyze_worker is not None and self.analyze_worker.isRunning():
            self.analyze_worker.stop = True
            self.statusBar().showMessage("Stopping after the current track…")

    def _bpm_detected(self, stored, skipped, errors, fatal):
        self.progress.hide()
        self.stop_bpm_action.setEnabled(False)
        if fatal:
            QMessageBox.warning(self, "Detect BPM", f"The BPM model couldn't be loaded:\n\n{fatal}")
            return
        self._rebuild(keep_undo=True)  # only BPMs changed: paths and tag definitions are as they were
        self.statusBar().showMessage(
            f"BPM detected for {stored} track{'s' * (stored != 1)}"
            + (f"; {skipped} had no steady beat" if skipped else "")
            + (f"; {len(errors)} couldn't be read." if errors else "."), 15000
        )
        if errors:
            QMessageBox.warning(self, "Detect BPM", "\n".join(errors[:20]) + ("\n…" if len(errors) > 20 else ""))

    def _library_available(self, title):
        if self.library.root and os.path.isdir(self.library.root):
            return True
        QMessageBox.warning(self, title, "The library folder isn't available (is the USB stick plugged in?).")
        return False

    # --- files -----------------------------------------------------------------------------------

    def new_folder(self, parent):
        if not parent:
            return
        name, ok = QInputDialog.getText(self, "New folder", f"New folder inside “{os.path.basename(parent)}”:")
        name = name.strip()
        if not ok or not name:
            return
        if "/" in name or name.startswith("."):
            QMessageBox.warning(self, "New folder", "Folder names can't contain “/” or start with a dot.")
            return
        try:
            self.library.create_folder(parent, name)
        except OSError as exc:
            QMessageBox.warning(self, "New folder", str(exc))

    def move_tracks(self, paths, dest):
        paths = [p for p in paths if os.path.dirname(p) != dest]
        if not paths:
            return
        self.player.stop_if(paths)
        errors = self.library.move_tracks(paths, dest)
        self._rebuild()
        moved = len(paths) - len(errors)
        self.statusBar().showMessage(f"Moved {moved} track{'s' * (moved != 1)} to {os.path.basename(dest)}.", 8000)
        if errors:
            QMessageBox.warning(self, "Move", "\n".join(errors[:20]))

    def move_selected_dialog(self):
        paths = self._selected_paths()
        if not paths:
            return
        dest = QFileDialog.getExistingDirectory(self, "Move to folder", self.library.root)
        if not dest:
            return
        if not self.library.contains(dest):
            QMessageBox.warning(self, "Move", "Pick a folder inside the library, or the tracks would leave it.")
            return
        self.move_tracks(paths, dest)

    def trash_selected(self):
        self._trash(self._selected_paths(), what="tracks")

    def _trash(self, paths, what):
        if not paths:
            return
        if what == "folder":
            inside = [r.path for r in self.model.rows if r.path.startswith(paths[0].rstrip("/") + "/")]
            text = f"Move the folder “{os.path.basename(paths[0])}” and its {len(inside)} tracks to the Trash?"
        else:
            inside = paths
            text = f"Move {len(paths)} track{'s' * (len(paths) > 1)} to the Trash?"
        in_rb = self.library.in_rekordbox(inside)
        if in_rb:
            text += (
                f"\n\n{in_rb} of them are in your rekordbox collection. rekordbox will show them as missing files; "
                "remove them there."
            )
        text += "\n\nYou can restore them from the Trash."
        if QMessageBox.question(self, "Move to Trash", text) != QMessageBox.Yes:
            return
        self.player.stop_if(paths)
        _trashed, errors = self.library.trash(paths)
        self._rebuild()
        if errors:
            QMessageBox.warning(self, "Move to Trash", "\n".join(errors[:20]))

    def _reveal(self, path):
        subprocess.run(["open", "-R", path])

    def _track_menu(self, pos):
        paths = self._selected_paths()
        if not paths:
            return
        menu = QMenu(self)
        menu.addAction(icons.icon("play"), "Play", lambda: self._play_index(self.table.currentIndex()))
        menu.addAction(icons.icon("star"), "Toggle favorite  (F)", self.toggle_favorite)
        menu.addAction("Show tracks that mix with this one  (⌘K)", self.show_compatible)
        menu.addAction(f"Detect BPM  ({self.keymap['detect_bpm']})", self.detect_bpm_selected)
        menu.addSeparator()
        self._link_menu(menu, paths)
        menu.addSeparator()
        self._playlist_menu(menu, paths)
        menu.addSeparator()
        menu.addAction("Edit title  (F2)", lambda: self.edit_cell(TITLE))
        menu.addAction("Edit artist", lambda: self.edit_cell(ARTIST))
        menu.addAction("Copy tags  (⌘C)", self.copy_tags)
        paste = menu.addAction(f"Paste tags onto {len(paths)}  (⌘V)", self.paste_tags)
        paste.setEnabled(bool(self.copied_tags))
        menu.addSeparator()
        menu.addAction("Move to folder…", self.move_selected_dialog)
        menu.addAction("Reveal in Finder", lambda: self._reveal(paths[0]))
        menu.addSeparator()
        menu.addAction(f"Move {len(paths)} to Trash…  (⌫)", self.trash_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _playlist_menu(self, menu, paths):
        menu.addAction(
            icons.icon("folder"), f"Add {len(paths)} to a playlist…  (⌘P)" if len(paths) > 1
            else "Add to a playlist…  (⌘P)", self.add_to_playlist_dialog,
        )
        current = self._current_row()
        names = {p["id"]: p["name"] for p in self.library.playlists()}
        in_playlists = sorted(
            (i for i in self.library.playlist_map().get(current.path, set()) if i in names),
            key=lambda i: names[i].lower(),
        ) if current else []
        if not in_playlists:
            return
        sub = menu.addMenu(f"In playlists ({len(in_playlists)})")
        for playlist_id in in_playlists:
            entry = sub.addMenu(names[playlist_id])
            entry.addAction("Show it in the list  (⇧⌘P)", lambda i=playlist_id: self._show_playlist(i))
            entry.addAction("Open the Playlists window", self.open_playlists)
            entry.addAction(
                "Take this track out of it",
                lambda i=playlist_id, path=current.path: self._remove_from_playlist(i, path),
            )

    def _playlist_filter(self, playlist_id):
        """(the playlist's tracks that are in the list, the banner text), or None if it's gone or trashed."""
        row = self.library.playlist(playlist_id)
        if row is None or row["deleted"]:
            return None
        paths = {p for p in self.library.playlist_paths(playlist_id) if self.model.row_for(p)}
        return paths, f"Playlist: <b>{row['name']}</b> ({len(paths)})"

    def _show_playlist(self, playlist_id):
        shown = self._playlist_filter(playlist_id)
        if shown is None:
            return
        self._show_paths(*shown, "playlist")
        self.shown_playlist = playlist_id

    def _remove_from_playlist(self, playlist_id, path):
        name = self.library.playlist(playlist_id)["name"]
        if self.library.remove_from_playlist(playlist_id, [path]):
            self._playlists_edited()
            if self.playlists_window is not None:
                self.playlists_window.refresh()
            self._message(f"Took it out of {name}. ⌘Z puts it back.", 6000)

    def _link_menu(self, menu, paths):
        if len(paths) >= 2:
            menu.addAction(icons.icon("link"), f"Link these {len(paths)} together  (⌘L)", self.link_tracks)
        else:
            menu.addAction(icons.icon("link"), "Goes well with…  (⌘L)", self.link_tracks)
        playing = self.player.path
        if playing and any(p != playing for p in paths):
            row = self.model.row_for(playing)
            name = track_label(row) if row else os.path.basename(playing)
            if len(name) > 40:
                name = name[:39] + "…"
            menu.addAction(icons.icon("link"), f"Goes well with the playing track: {name}", self.link_with_playing)
        current = self._current_row()
        linked = self.library.link_map().get(current.path, set()) if current else set()
        if not linked:
            return
        sub = menu.addMenu(f"Linked tracks ({len(linked)})")
        sub.addAction("Show them in the list  (⇧⌘L)", lambda: self.show_linked(current.path))
        sub.addSeparator()
        rows = sorted((r for r in map(self.model.row_for, linked) if r), key=lambda r: track_label(r).lower())
        for r in rows:
            sub.addAction(track_label(r), lambda p=r.path: self.select_track(p))
        missing = len(linked) - len(rows)
        if missing:
            sub.addAction(f"{missing} missing file{'s' * (missing != 1)}…", self.open_links)
        unlink = sub.addMenu("Unlink")
        for r in rows:
            unlink.addAction(track_label(r), lambda p=r.path: self.unlink([(current.path, p)]))

    def _folder_menu(self, pos):
        index = self.tree.indexAt(pos)
        folder = self.fs_model.filePath(index) if index.isValid() else self.library.root
        if not folder:
            return
        menu = QMenu(self)
        menu.addAction("New folder…", lambda: self.new_folder(folder))
        menu.addAction("Reveal in Finder", lambda: self._reveal(folder))
        if index.isValid():
            menu.addSeparator()
            menu.addAction("Move folder to Trash…", lambda: self._trash([folder], what="folder"))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --- rekordbox sync --------------------------------------------------------------------------

    def sync(self):
        if rekordbox.is_running():
            QMessageBox.warning(self, "Sync to rekordbox", "rekordbox is open.\n\nQuit rekordbox, then sync again.")
            self._update_status()
            return
        changes, waiting = self.library.pending_counts()
        text = (
            f"Write {changes} change{'s' * (changes != 1)} into rekordbox's database?\n\n"
            f"A backup of master.db is made first."
        )
        pending = self.library.cache.pending()
        renames = pending["column_renames"] + pending["value_renames"]
        if renames:
            old_names = {c[0]: c[1] for c in self.library.rb_state.columns} if self.library.rb_state else {}
            old_names.update({v[0]: v[2] for v in self.library.rb_state.values} if self.library.rb_state else {})
            lines = [f"  {old_names.get(rb_id, '?')} → {name}" for rb_id, name in renames]
            text += "\n\nRenamed in rekordbox's My Tags:\n" + "\n".join(lines)
        if pending["new_values"]:
            text += "\n\nNew My Tags: " + ", ".join(name for _, _, name in pending["new_values"])
        pl_names = {p["id"]: p["name"] for p in self.library.playlists()}
        trashed = {p["rb_id"]: p["name"] for p in self.library.trashed_playlists()}
        pl_lines = [f"  New: {name}" for _local_id, _parent, name in pending["playlist_creates"]]
        pl_lines += [f"  Renamed: {name}" for _rb_id, name in pending["playlist_renames"]]
        pl_lines += [
            f"  Tracks changed: {pl_names.get(local_id, '?')} ({len(entries)})"
            for local_id, entries in pending["playlist_members"].items()
        ]
        pl_lines += [f"  Deleted: {trashed.get(rb_id, '?')}" for rb_id in pending["playlist_deletes"]]
        if pl_lines:
            text += "\n\nPlaylists:\n" + "\n".join(pl_lines)
        if waiting:
            text += f"\n\n{waiting} tagged tracks aren't in rekordbox yet; their tags stay here until you import them."
        if QMessageBox.question(self, "Sync to rekordbox", text) != QMessageBox.Yes:
            return
        try:
            result = self.library.sync()
        except rekordbox.RekordboxError as exc:
            QMessageBox.critical(self, "Sync to rekordbox", str(exc))
            self._update_status()
            return
        self._rebuild()
        msg = f"Synced {len(result.synced_paths)} tracks"
        if result.moves_applied:
            msg += f", updated {result.moves_applied} moved paths"
        msg += f". Backup: {result.backup_path.name}"
        self.statusBar().showMessage(msg, 15000)
        if result.playlist_drops:
            dropped = "\n".join(
                f"• {pl_names.get(local_id, '?')}: {len(paths)} track{'s' * (len(paths) != 1)}"
                for local_id, paths in result.playlist_drops.items()
            )
            QMessageBox.warning(
                self, "Sync to rekordbox",
                "Some playlist tracks aren't in rekordbox, so they were left out of the playlists it got:\n\n"
                + dropped + "\n\nImport them into rekordbox and sync again to put them back.",
            )
        if result.anlz_errors:
            QMessageBox.warning(
                self, "Sync to rekordbox", "Database updated, but some analysis files couldn't be rewritten:\n\n"
                + "\n".join(result.anlz_errors[:10])
            )

    # --- menus and layout ------------------------------------------------------------------------

    def _action(self, menu, text, handler, shortcut=None, tip=None):
        """`shortcut` is a keymap.MENU_KEYS id, so the user can rebind it."""
        action = QAction(text, self)
        if shortcut is not None:
            self.menu_actions[shortcut] = action
            action.setShortcut(QKeySequence(self.keymap[shortcut]))
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(lambda _checked=False: handler())
        menu.addAction(action)
        return action

    def _build_menus(self):
        bar = self.menuBar()
        lib = bar.addMenu("Library")
        self._action(lib, "Choose library folder…", self.choose_root)
        self._action(lib, "Import tracks…", self.import_dialog, "m_import", "Copy audio files into the library")
        self._action(lib, "Rescan", self.reload, "m_rescan")
        lib.addSeparator()
        self._action(lib, "Clean up names…", self.open_cleanup)
        self._action(lib, "New folder…", lambda: self.new_folder(self._current_folder()), "m_new_folder")
        lib.addSeparator()
        self.rb_enabled_action = QAction("Sync tags with rekordbox", self, checkable=True)
        self.rb_enabled_action.setChecked(self.library.rekordbox_enabled)
        self.rb_enabled_action.setToolTip(
            "Off: DJTools never opens rekordbox's database. Tags, links and everything else keep working here."
        )
        if os.environ.get("DJTOOLS_RB_DB"):
            self.rb_enabled_action.setEnabled(False)
            self.rb_enabled_action.setToolTip("DJTOOLS_RB_DB is set, which asks for a specific database.")
        self.rb_enabled_action.triggered.connect(self._toggle_rekordbox)
        lib.addAction(self.rb_enabled_action)
        lib.addAction(self.sync_action)

        edit = bar.addMenu("Edit")
        self.undo_action = self._action(edit, "Undo tag or link change", self.undo, "m_undo")
        self.redo_action = self._action(edit, "Redo tag or link change", self.redo, "m_redo")
        edit.addSeparator()
        self._action(edit, "Copy tags", self.copy_tags, "m_copy_tags")
        self._action(edit, "Paste tags", self.paste_tags, "m_paste_tags")
        self._action(edit, "Toggle favorite", self.toggle_favorite)
        edit.addSeparator()
        self._action(edit, "Edit title", lambda: self.edit_cell(TITLE))
        self._action(edit, "Edit artist", lambda: self.edit_cell(ARTIST))
        edit.addSeparator()
        self._action(
            edit, "Goes well with… / Link selected together", self.link_tracks, "m_link",
            "One track: pick what it goes well with. Several: link them all together.",
        )
        self._action(edit, "Goes well with the playing track", self.link_with_playing)
        edit.addSeparator()
        self._action(
            edit, "Add to playlist…", self.add_to_playlist_dialog, "m_add_playlist",
            "Put the selected tracks in a playlist rekordbox will see",
        )
        for action in (self.undo_action, self.redo_action):
            action.setEnabled(False)

        view = bar.addMenu("View")
        self._action(view, "Search", lambda: (self.search.setFocus(), self.search.selectAll()), "m_search")
        self._action(view, "Show tracks that mix with this one", self.show_compatible, "m_compatible")
        self._action(view, "BPM range for mixing…", self.set_bpm_range)
        view.addSeparator()
        self._action(view, "Detect BPM of selection", self.detect_bpm_selected)
        self._action(view, "Detect missing BPMs…", self.detect_missing_bpm)
        self.stop_bpm_action = self._action(view, "Stop detecting BPM", self.stop_detecting_bpm)
        self.stop_bpm_action.setEnabled(False)
        view.addSeparator()
        self._action(view, "Clear filters", self.clear_filters, "m_clear_filters")
        view.addSeparator()
        self._action(view, "Show linked tracks", self.show_linked, "m_show_linked")
        self._action(view, "All links…", self.open_links, "m_all_links")
        view.addSeparator()
        self._action(view, "Show a playlist this track is in", self.show_playlists, "m_show_playlist")
        self._action(view, "All playlists…", self.open_playlists, "m_playlists")
        view.addSeparator()
        columns = view.addMenu("Columns")
        columns.aboutToShow.connect(lambda: self._fill_column_menu(columns))
        self._action(view, "Reset layout", self._reset_layout)
        view.addSeparator()
        self._action(view, "Keyboard shortcuts", lambda: show_help(self), "m_help")

    def _apply_menu_keys(self):
        for name, action in self.menu_actions.items():
            action.setShortcut(QKeySequence(self.keymap[name]))

    def _fill_column_menu(self, menu):
        menu.clear()
        for col, name in enumerate(HEADERS):
            if col == TITLE or (col == RB and not self.library.rekordbox_enabled):
                continue
            action = menu.addAction("Favorite" if col == FAV else name)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(col))
            action.toggled.connect(lambda on, c=col: self.table.setColumnHidden(c, not on))

    def _header_menu(self, pos):
        menu = QMenu(self)
        self._fill_column_menu(menu)
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _restore_layout(self):
        layout = self.settings.get("layout") or {}
        try:
            if layout.get("version") == LAYOUT_VERSION and layout.get("header"):
                header = self.table.horizontalHeader()
                if header.restoreState(QByteArray(base64.b64decode(layout["header"]))):
                    self.table.sortByColumn(header.sortIndicatorSection(), header.sortIndicatorOrder())
            for key, widget in (("geometry", self), ("splitter", self.splitter), ("left_split", self.left_split)):
                if layout.get(key):
                    restore = widget.restoreGeometry if widget is self else widget.restoreState
                    restore(QByteArray(base64.b64decode(layout[key])))
        except (ValueError, TypeError):
            pass  # a damaged entry just means the default layout
        self.player.volume.setValue(int(layout.get("volume", self.player.volume.value())))

    def _save_layout(self):
        encode = lambda data: base64.b64encode(bytes(data)).decode()  # noqa: E731
        self.settings.set("layout", {
            "version": LAYOUT_VERSION,
            "header": encode(self.table.horizontalHeader().saveState()),
            "geometry": encode(self.saveGeometry()),
            "splitter": encode(self.splitter.saveState()),
            "left_split": encode(self.left_split.saveState()),
            "volume": self.player.volume.value(),
        })

    def _reset_layout(self):
        self.settings.set("layout", {})
        QMessageBox.information(self, "Reset layout", "The default layout is used from the next launch.")
        self._layout_reset = True

    def closeEvent(self, event):
        if not getattr(self, "_layout_reset", False):
            self._save_layout()
        if self.analyze_worker is not None:
            self.analyze_worker.stop = True
        for worker in (self.worker, self.import_worker, self.analyze_worker):
            if worker is not None:
                worker.wait(5000)
        super().closeEvent(event)
