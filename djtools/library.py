"""Application logic between the UI and the cache / rekordbox / filesystem. No Qt widgets here."""
import csv
import os
import shutil
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QFile

from . import health, naming, rekordbox
from .cache import Cache
from .config import APP_DIR, AUDIO_EXTS, CACHE_PATH, FAVORITE, SUGGESTED_COLUMNS
from .scanner import iter_audio_files, read_file, write_title_artist

REKORDBOX_DEFAULT_COLUMNS = ["Genre", "Components", "Situation", "Untitled Column"]


@dataclass
class Row:
    path: str
    folder: str
    title: str
    artist: str
    bpm: float
    bpm_src: str
    key: str
    key_src: str
    in_rekordbox: bool
    duration: float
    value_ids: set = field(default_factory=set)
    title_tag: bool = True
    bitrate: int = None
    has_cover: bool = False
    readable: bool = True
    size: int = 0

    @property
    def ext(self):
        return os.path.splitext(self.path)[1].lower()


def scan(root, progress=None):
    """Refresh the cache for `root`. Runs in a worker thread, so it opens its own connection."""
    cache = Cache(CACHE_PATH)
    try:
        known = cache.track_stamps()
        prefix = root.rstrip("/") + "/"
        seen, changed = set(), []
        files = list(iter_audio_files(root))
        for i, (path, size, mtime) in enumerate(files):
            seen.add(path)
            if known.get(path) != (size, mtime):
                changed.append(read_file(path, size, mtime))
                if len(changed) >= 50:
                    cache.upsert_tracks(changed)
                    changed = []
            if progress:
                progress(i + 1, len(files))
        cache.upsert_tracks(changed)
        gone = [p for p in known if p.startswith(prefix) and p not in seen]
        cache.forget_tracks(gone)
        return len(files)
    finally:
        cache.close()


def import_files(sources, dest, root, progress=None):
    """Copy audio files (or folders of them) from outside the library into `dest`, and add them to the cache.

    Runs in a worker thread, so it opens its own connection. A dropped folder keeps its name inside `dest`.
    An existing file is never overwritten. Returns (imported paths, error messages)."""
    root_prefix = root.rstrip("/") + "/"
    jobs, errors = [], []
    for src in sources:
        src = src.rstrip("/")
        if src.startswith(root_prefix):
            errors.append(f"Already in the library: {os.path.basename(src)} (drag it inside the app to move it)")
        elif os.path.isdir(src):
            parent = os.path.dirname(src)
            jobs += [(p, os.path.join(dest, os.path.relpath(p, parent))) for p, _size, _mtime in iter_audio_files(src)]
        elif os.path.splitext(src)[1].lower() in AUDIO_EXTS:
            jobs.append((src, os.path.join(dest, os.path.basename(src))))
        else:
            errors.append(f"Not an audio file: {os.path.basename(src)}")
    imported = []
    cache = Cache(CACHE_PATH)
    try:
        for i, (src, dst) in enumerate(jobs):
            if progress:
                progress(i, len(jobs))
            if os.path.exists(dst):
                errors.append(f"Already exists, skipped: {os.path.relpath(dst, root)}")
                continue
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copyfile(src, dst)
                try:
                    shutil.copystat(src, dst)  # dates only matter for sorting in Finder; FAT refuses some flags
                except OSError:
                    pass
            except OSError as exc:
                errors.append(f"{os.path.basename(src)}: {exc}")
                continue
            dst = Library._on_disk_name(dst)
            st = os.stat(dst)
            cache.upsert_tracks([read_file(dst, st.st_size, st.st_mtime)])
            imported.append(dst)
        if progress:
            progress(len(jobs), len(jobs))
    finally:
        cache.close()
    return imported, errors


class Library:
    def __init__(self, settings):
        self.settings = settings
        self.cache = Cache(CACHE_PATH)
        self.rb_state = None
        self.rb_error = None
        if not settings.rekordbox_enabled:
            # Before any load: with no library folder chosen yet there is no reload() to seed them,
            # and the tag panel would open empty on a fresh install without rekordbox.
            self.cache.ensure_local_columns(SUGGESTED_COLUMNS)
        self._health_inputs = None  # (proposals, empty folders): only files changing can change them
        # Undo steps. A step is a list of ("tag", value_id, on, paths that actually changed)
        # ("link", path, path, note, created, on) and ("note", path, path, old note, new note) entries.
        self._undo, self._redo = [], []

    @property
    def root(self):
        return self.settings.library_root

    # --- rekordbox ---------------------------------------------------------------------------------

    @property
    def rekordbox_enabled(self):
        return self.settings.rekordbox_enabled

    def load_rekordbox_state(self):
        """Read master.db. Safe while rekordbox is open.

        Returns (None, None) when rekordbox is switched off — no state, but no error either: everything
        downstream already treats a missing state as "tags stay local", and the UI tells the two apart
        by asking the setting rather than by the error being blank.
        """
        if not self.rekordbox_enabled:
            return None, None
        try:
            return rekordbox.read_state(self.settings.rekordbox_db), None
        except rekordbox.RekordboxError as exc:
            return None, str(exc)

    def apply_rekordbox_state(self, state, error):
        """Returns (adopted, orphaned) local columns, for the caller to report. See Cache.adopt_local_columns."""
        self.rb_state, self.rb_error = state, error
        if state is None:
            if self.rekordbox_enabled:
                return [], []  # unreadable, not switched off: don't invent columns over a transient failure
            self.cache.ensure_local_columns(SUGGESTED_COLUMNS)
            return [], []
        adopted, orphans = self.cache.adopt_local_columns(state)
        self.cache.import_rekordbox(state)
        self._suggest_columns_once()
        return adopted, orphans

    def _suggest_columns_once(self):
        """First run: rename rekordbox's default My Tag columns to Genre / Mood / Set position / Favorite.

        Only queued locally. Nothing reaches rekordbox until the user syncs, and they can rename first.
        """
        if self.settings.get("columns_suggested"):
            return
        columns = self.cache.columns()
        if [c["name"] for c in columns] == REKORDBOX_DEFAULT_COLUMNS:
            for column, name in zip(columns, SUGGESTED_COLUMNS):
                if column["name"] != name:
                    self.cache.rename_column(column["rb_id"], name)
            self.cache.add_value(columns[3]["rb_id"], FAVORITE)
        self.settings.set("columns_suggested", True)

    def sync(self):
        pending = self.cache.pending()
        tag_map = self.cache.track_tag_map()
        plan = rekordbox.SyncPlan(
            column_renames=pending["column_renames"],
            new_values=pending["new_values"],
            value_renames=pending["value_renames"],
            value_deletes=pending["value_deletes"],
            moves=pending["moves"],
            track_tags={p: tag_map.get(p, set()) for p in pending["dirty_tracks"]},
            value_rb_ids=self.cache.value_rb_ids(),
        )
        result = rekordbox.sync(self.settings.rekordbox_db, plan)
        self.cache.mark_synced(result, plan)
        self.apply_rekordbox_state(*self.load_rekordbox_state())
        return result

    def pending_counts(self):
        """(changes rekordbox will receive, tagged tracks rekordbox doesn't have yet)."""
        p = self.cache.pending()
        waiting = 0
        if self.rb_state is not None:
            waiting = sum(1 for path in p["dirty_tracks"] if self.cache.rekordbox_track(self.rb_state, path) is None)
        tag_changes = len(p["column_renames"]) + len(p["new_values"]) + len(p["value_renames"]) + len(p["value_deletes"])
        return tag_changes + len(p["moves"]) + len(p["dirty_tracks"]) - waiting, waiting

    # --- rows and tags -----------------------------------------------------------------------------

    def rows(self):
        if not self.root:
            return []
        tag_map = self.cache.track_tag_map()
        out = []
        for t in self.cache.tracks(self.root):
            rb = self.cache.rekordbox_track(self.rb_state, t["path"]) if self.rb_state else None
            bpm, bpm_src = (rb.bpm, "rekordbox") if rb and rb.bpm else (t["file_bpm"], "file" if t["file_bpm"] else "")
            key, key_src = (rb.key, "rekordbox") if rb and rb.key else (t["file_key"], "file" if t["file_key"] else "")
            folder = os.path.relpath(os.path.dirname(t["path"]), self.root)
            out.append(
                Row(
                    path=t["path"],
                    folder="" if folder == "." else folder,
                    title=t["title"],
                    artist=t["artist"],
                    bpm=bpm,
                    bpm_src=bpm_src,
                    key=key,
                    key_src=key_src,
                    in_rekordbox=rb is not None,
                    duration=t["duration"],
                    value_ids=tag_map.get(t["path"], set()),
                    title_tag=bool(t["title_tag"]),
                    bitrate=t["bitrate"],
                    has_cover=bool(t["has_cover"]),
                    readable=t["readable"] is None or bool(t["readable"]),
                    size=t["size"],
                )
            )
        return out

    def favorite_value_id(self, create=False):
        """The "Favorite" tag inside the column named "Favorite"; created on demand when `create`."""
        column = next((c for c in self.cache.columns() if c["name"].lower() == FAVORITE.lower()), None)
        if column is None:
            return None
        for v in self.cache.values():
            if v["column_rb_id"] == column["rb_id"] and v["name"].lower() == FAVORITE.lower():
                return v["id"]
        return self.cache.add_value(column["rb_id"], FAVORITE) if create else None

    # --- tag edits with undo ----------------------------------------------------------------------

    def set_tags(self, ops):
        """Apply [(paths, value_id, on)] as one undoable step. Returns True when anything changed."""
        tag_map = self.cache.track_tag_map()
        step = []
        for paths, value_id, on in ops:
            changed = [p for p in paths if (value_id in tag_map.get(p, ())) != on]
            if changed:
                self.cache.set_tag(changed, value_id, on)
                for p in changed:
                    tag_map.setdefault(p, set()).symmetric_difference_update({value_id})
                step.append(("tag", value_id, on, changed))
        if step:
            self._undo.append(step)
            self._redo.clear()
        return bool(step)

    def undo(self):
        return self._replay(self._undo, self._redo, reverse=True)

    def redo(self):
        return self._replay(self._redo, self._undo, reverse=False)

    def _replay(self, source, target, reverse):
        if not source:
            return False
        step = source.pop()
        for entry in reversed(step) if reverse else step:
            if entry[0] == "tag":
                _kind, value_id, on, paths = entry
                self.cache.set_tag(paths, value_id, on != reverse)
            elif entry[0] == "note":
                _kind, x, y, old, new = entry
                self.cache.set_link_note(x, y, old if reverse else new)
            else:
                _kind, x, y, note, created, on = entry
                if on != reverse:
                    self.cache.add_link(x, y, note, created)
                else:
                    self.cache.remove_link(x, y)
        target.append(step)
        return True

    # --- links ("goes well with") -------------------------------------------------------------------

    def set_links(self, pairs, on):
        """Link or unlink [(path, path)] as one undoable step. Returns how many links changed."""
        return self.edit_links([(pairs, on)])

    def edit_links(self, ops):
        """Apply [(pairs, on)] as one undoable step. Returns how many links changed."""
        step = []
        for pairs, on in ops:
            for x, y in pairs:
                if x == y:
                    continue
                existing = self.cache.link(x, y)
                if on and existing is None:
                    created = time.time()
                    self.cache.add_link(x, y, "", created)
                    step.append(("link", x, y, "", created, True))
                elif not on and existing is not None:
                    self.cache.remove_link(x, y)
                    step.append(("link", x, y, existing["note"], existing["created"], False))
        if step:
            self._undo.append(step)
            self._redo.clear()
        return len(step)

    def set_link_note(self, x, y, note):
        """Undoable. Returns True when the note changed."""
        existing = self.cache.link(x, y)
        if existing is None or existing["note"] == note:
            return False
        self.cache.set_link_note(x, y, note)
        self._undo.append([("note", x, y, existing["note"], note)])
        self._redo.clear()
        return True

    def link_map(self):
        """path -> set of paths it goes well with."""
        out = {}
        for r in self.cache.links():
            out.setdefault(r["a"], set()).add(r["b"])
            out.setdefault(r["b"], set()).add(r["a"])
        return out

    def clear_undo(self):
        """Tag definitions or file paths changed: old steps could point at tags or files that are gone."""
        self._undo.clear()
        self._redo.clear()

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    # --- files -------------------------------------------------------------------------------------

    def edit_title_artist(self, path, title=None, artist=None):
        """Write the title and/or artist tag into the file and refresh its cache row. The filename is unchanged."""
        write_title_artist(path, title=title, artist=artist)
        st = os.stat(path)
        info = read_file(path, st.st_size, st.st_mtime)
        self.cache.upsert_tracks([info])
        return info

    def contains(self, path):
        """True for the library folder itself or anything inside it."""
        if not self.root:
            return False
        root, path = os.path.abspath(self.root), os.path.abspath(path)
        return path == root or path.startswith(root.rstrip("/") + "/")

    def create_folder(self, parent, name):
        path = Path(parent) / name
        path.mkdir()
        return str(path)

    def move_tracks(self, paths, dest_dir):
        """Move files; rekordbox's paths are updated at the next sync. Returns error messages."""
        errors = []
        for src in paths:
            dst = os.path.join(dest_dir, os.path.basename(src))
            if os.path.abspath(dst) == os.path.abspath(src):
                continue
            if os.path.exists(dst):
                errors.append(f"Already exists in destination: {os.path.basename(src)}")
                continue
            try:
                shutil.move(src, dst)
            except OSError as exc:
                errors.append(f"{os.path.basename(src)}: {exc}")
                continue
            known = self.rb_state is None or self.cache.rekordbox_track(self.rb_state, src) is not None
            self.cache.rename_track(src, dst, record_move=known)
        return errors

    def trash(self, paths):
        """Move files or folders to the macOS Trash (recoverable). Returns (trashed, errors)."""
        trashed, errors = [], []
        for path in paths:
            if QFile.moveToTrash(path):
                trashed.append(path)
            else:
                errors.append(f"Could not move to Trash: {os.path.basename(path)}")
        gone = [
            t["path"]
            for t in self.cache.tracks(self.root)
            if any(t["path"] == p or t["path"].startswith(p.rstrip("/") + "/") for p in trashed)
        ]
        self.cache.forget_tracks(gone)
        return trashed, errors

    def in_rekordbox(self, paths):
        if self.rb_state is None:
            return 0
        return sum(1 for p in paths if self.cache.rekordbox_track(self.rb_state, p) is not None)

    # --- health and cleanup ------------------------------------------------------------------------

    def cleanup_tracks(self):
        """Cache rows by path, the input `naming.propose` works from."""
        return {t["path"]: t for t in self.cache.tracks(self.root)} if self.root else {}

    def cleanup_proposals(self):
        # Proposals depend on the folders on disk: with the stick unplugged they'd all be wrong.
        if not self.root or not os.path.isdir(self.root):
            return []
        return naming.propose_all(list(self.cleanup_tracks().values()), self.root)

    def health(self, rows, files_changed=True):
        """Pass files_changed=False after tag-only edits to skip re-reading names and folders."""
        if files_changed or self._health_inputs is None:
            empty = health.empty_folders(self.root) if self.root and os.path.isdir(self.root) else None
            self._health_inputs = (self.cleanup_proposals(), empty)
        proposals, empty = self._health_inputs
        return health.run(
            rows, proposals, empty, self.root, self.cache.columns(), self.cache.values(),
            rekordbox_readable=self.rb_state is not None,
        )

    def invalidate_health(self):
        """A file's name or tags changed: re-read naming proposals at the next health refresh."""
        self._health_inputs = None

    def apply_cleanup(self, proposals):
        """Write tags, then rename/move. Returns error messages; every attempt is logged to a CSV."""
        errors, log = [], []
        for p in proposals:
            result = "ok"
            try:
                final = self._apply_one(p)
            except Exception as exc:  # one bad file must not stop the rest
                final, result = p.path, f"error: {exc}"
                errors.append(f"{os.path.basename(p.path)}: {exc}")
            log.append((p.path, final, p.title, p.new_title, p.artist, p.new_artist, result))
        self._write_log(log)
        return errors

    def _apply_one(self, p):
        if p.path_changed and os.path.exists(p.new_path) and not naming.same_path(p.new_path, p.path):
            raise OSError("a file with the new name already exists")
        if p.title_changed or p.artist_changed:
            write_title_artist(
                p.path,
                title=p.new_title if p.title_changed else None,
                artist=p.new_artist if p.artist_changed else None,
            )
        path = p.path
        if p.path_changed:
            os.makedirs(os.path.dirname(p.new_path), exist_ok=True)
            if naming.same_path(p.new_path, p.path):
                # Case or accent-form change only: FAT sees the same name, so go through a temporary one.
                tmp = os.path.join(os.path.dirname(p.path), f".djtools-{uuid.uuid4().hex}.tmp")
                os.rename(p.path, tmp)
                os.rename(tmp, p.new_path)
            else:
                os.rename(p.path, p.new_path)
            path = self._on_disk_name(p.new_path)
            known = self.rb_state is None or self.cache.rekordbox_track(self.rb_state, p.path) is not None
            self.cache.rename_track(p.path, path, record_move=known)
        st = os.stat(path)
        self.cache.upsert_tracks([read_file(path, st.st_size, st.st_mtime)])
        return path

    @staticmethod
    def _on_disk_name(path):
        """The name as the filesystem lists it (it may store accents decomposed), so the next scan matches."""
        folder, name = os.path.split(path)
        target = unicodedata.normalize("NFC", name)
        for entry in os.listdir(folder):
            if unicodedata.normalize("NFC", entry) == target:
                return os.path.join(folder, entry)
        return path

    @staticmethod
    def _write_log(log):
        folder = APP_DIR / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        with open(folder / f"cleanup-{time.strftime('%Y%m%d-%H%M%S')}.csv", "a", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["old_path", "new_path", "old_title", "new_title", "old_artist", "new_artist", "result"])
            writer.writerows(log)
