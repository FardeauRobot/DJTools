"""Table model for tracks, plus the proxy that searches, filters by folder and by tag, and sorts."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from .. import keys
from . import theme

FAV, TITLE, ARTIST, BPM, KEY, RB, TAGS, LINKS, FOLDER, TIME, FORMAT, BITRATE = range(12)
HEADERS = ["★", "Title", "Artist", "BPM", "Key", "rekordbox", "My Tags", "Links", "Folder", "Time", "Format", "Bitrate"]
HIDDEN_BY_DEFAULT = (FORMAT, BITRATE)
PATH_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2


def fmt_time(seconds):
    if not seconds:
        return ""
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


class TrackModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.value_names = {}  # value id -> name
        self.favorite_id = None
        self._index = {}
        self.editor = None  # callable(path, column, text) -> bool, for in-place title/artist edits
        self.links = {}  # path -> set of paths it goes well with

    def set_data(self, rows, value_names, favorite_id):
        self.beginResetModel()
        self.rows, self.value_names, self.favorite_id = rows, value_names, favorite_id
        self._index = {r.path: i for i, r in enumerate(rows)}
        self.endResetModel()

    def refresh_tags(self, value_names, favorite_id, tag_map):
        """Cheap update after tagging: keeps selection and scroll position."""
        self.value_names, self.favorite_id = value_names, favorite_id
        for r in self.rows:
            r.value_ids = tag_map.get(r.path, set())
        if self.rows:
            self.dataChanged.emit(self.index(0, FAV), self.index(len(self.rows) - 1, TAGS))

    def set_links(self, links):
        self.links = links
        if self.rows:
            self.dataChanged.emit(self.index(0, LINKS), self.index(len(self.rows) - 1, LINKS))

    def index_for(self, path, column=0):
        i = self._index.get(path)
        return self.index(i, column) if i is not None else QModelIndex()

    def row_for(self, path):
        i = self._index.get(path)
        return self.rows[i] if i is not None else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEADERS)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and index.column() in (TITLE, ARTIST) and self.editor is not None:
            flags |= Qt.ItemIsEditable
        return flags

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or index.column() not in (TITLE, ARTIST) or self.editor is None:
            return False
        r = self.rows[index.row()]
        value = str(value).strip()
        if value == (r.title if index.column() == TITLE else r.artist) or (index.column() == TITLE and not value):
            return False
        if not self.editor(r.path, index.column(), value):
            return False
        if index.column() == TITLE:
            r.title, r.title_tag = value, True
        else:
            r.artist = value
        self.dataChanged.emit(index, index)
        return True

    def update_row(self, path, **fields):
        i = self._index.get(path)
        if i is None:
            return
        for name, value in fields.items():
            setattr(self.rows[i], name, value)
        self.dataChanged.emit(self.index(i, 0), self.index(i, len(HEADERS) - 1))

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return HEADERS[section]
        return None

    def tag_text(self, r):
        names = sorted(self.value_names[i] for i in r.value_ids if i in self.value_names and i != self.favorite_id)
        return ", ".join(names)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, col = self.rows[index.row()], index.column()
        if role == PATH_ROLE:
            return r.path
        if role == Qt.EditRole and col in (TITLE, ARTIST):
            return r.title if col == TITLE else r.artist
        if role in (Qt.DisplayRole, SORT_ROLE):
            sort = role == SORT_ROLE
            if col == FAV:
                fav = self.favorite_id in r.value_ids
                return int(fav) if sort else ("★" if fav else "")
            if col == TITLE:
                return r.title.lower() if sort else r.title
            if col == ARTIST:
                return r.artist.lower() if sort else r.artist
            if col == BPM:
                if sort:
                    return r.bpm or 0.0
                return "" if not r.bpm else (f"{r.bpm:.0f}" if r.bpm == int(r.bpm) else f"{r.bpm:.1f}")
            if col == KEY:
                return keys.sort_key(r.key) if sort else keys.display(r.key)
            if col == RB:
                return int(r.in_rekordbox) if sort else ("✓" if r.in_rekordbox else "not imported")
            if col == TAGS:
                return self.tag_text(r).lower() if sort else self.tag_text(r)
            if col == LINKS:
                n = len(self.links.get(r.path, ()))
                return n if sort else (f"🔗 {n}" if n else "")
            if col == FOLDER:
                return r.folder.lower() if sort else r.folder
            if col == TIME:
                return (r.duration or 0) if sort else fmt_time(r.duration)
            if col == FORMAT:
                return r.ext.lstrip(".").upper()
            if col == BITRATE:
                return (r.bitrate or 0) if sort else (f"{r.bitrate} kbps" if r.bitrate else "")
        if role == Qt.ToolTipRole:
            if col == BPM and r.bpm_src:
                return f"BPM from {r.bpm_src}"
            if col == KEY and r.key_src:
                return f"Key from {r.key_src}"
            if col == RB and not r.in_rekordbox:
                return "Import this track into rekordbox (and analyze it) so its tags can be synced."
            if col == LINKS and self.links.get(r.path):
                linked = sorted(self.rows[self._index[p]].title for p in self.links[r.path] if p in self._index)
                return "Goes well with:\n" + "\n".join(f"• {t}" for t in linked[:15]) + "\n\n⇧⌘L shows them."
            if col in (TITLE, ARTIST):
                return f"{r.path}\n\nF2 edits the {'title' if col == TITLE else 'artist'} tag (the filename stays; " \
                    "Clean up names renames files)."
            return r.path
        if role == Qt.ForegroundRole:
            if (col == BPM and r.bpm_src == "file") or (col == KEY and r.key_src == "file"):
                return theme.FILE_SOURCE  # file tag: not rekordbox's own analysis yet
            if col == RB and not r.in_rekordbox:
                return theme.WARN
            if col == FAV:
                return theme.FAV
        if role == Qt.BackgroundRole and col == KEY:
            return theme.key_color(r.key)
        if role == Qt.TextAlignmentRole and col in (FAV, BPM, KEY, RB, LINKS, TIME, FORMAT, BITRATE):
            return int(Qt.AlignCenter)
        return None


class TrackFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.folder = None  # absolute path prefix, or None for everything
        self.value_ids = set()  # ticked tags: every one must be present (or any one, with match_any)
        self.match_any = False
        self.not_ids = set()  # tags that exclude a track
        self.untagged = False  # only tracks with no tag besides Favorite
        self.only_favorites = False
        self.paths = None  # set of paths from a health check, or None
        self.bpm_min = self.bpm_max = None
        self.keys = None  # set of Camelot keys, or None for any
        self.setSortRole(SORT_ROLE)

    def update(self, **changes):
        self.beginFilterChange()
        for name, value in changes.items():
            setattr(self, name, value)
        self.endFilterChange()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        r = model.rows[source_row]
        if self.paths is not None and r.path not in self.paths:
            return False
        if self.folder and not r.path.startswith(self.folder.rstrip("/") + "/"):
            return False
        if self.value_ids:
            if self.match_any and not self.value_ids & r.value_ids:
                return False
            if not self.match_any and not self.value_ids <= r.value_ids:
                return False
        if self.not_ids & r.value_ids:
            return False
        if self.untagged and r.value_ids - {model.favorite_id}:
            return False
        if self.only_favorites and model.favorite_id not in r.value_ids:
            return False
        if self.keys is not None and r.key not in self.keys:
            return False
        if (self.bpm_min or self.bpm_max) and not self.bpm_matches(r.bpm):
            return False
        if self.text:
            bpm = f"{r.bpm:.0f}" if r.bpm else ""
            haystack = " ".join((r.title, r.artist, r.folder, model.tag_text(r), r.key or "", bpm)).lower()
            return all(word in haystack for word in self.text.lower().split())
        return True

    def bpm_matches(self, bpm):
        """In range at its own tempo, half or double: a 70 BPM track mixes into a 140 set."""
        if not bpm:
            return False
        low, high = self.bpm_min or 0, self.bpm_max or 10_000
        return any(low <= b <= high for b in (bpm, bpm * 2, bpm / 2))

    @property
    def active(self):
        """True when anything besides the folder narrows the list."""
        return bool(
            self.text or self.value_ids or self.not_ids or self.untagged or self.only_favorites or self.paths is not None
            or self.keys is not None or self.bpm_min or self.bpm_max
        )

    def visible_rows(self):
        model = self.sourceModel()
        return [model.rows[self.mapToSource(self.index(i, 0)).row()] for i in range(self.rowCount())]

    def value_counts(self):
        """value id -> how many tracks in the current list carry it."""
        counts = {}
        for r in self.visible_rows():
            for vid in r.value_ids:
                counts[vid] = counts.get(vid, 0) + 1
        return counts
