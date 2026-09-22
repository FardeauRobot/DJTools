"""The only module that touches rekordbox's master.db.

Reads are safe at any time. Every write goes through `sync()`, which refuses to run while rekordbox is
open (rekordbox keeps its own copy in memory and would overwrite or corrupt ours) and takes a timestamped
backup first.
"""
import logging
import os
import shutil
import subprocess
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import BACKUP_DIR, BACKUPS_KEPT
from .keys import to_camelot

logging.getLogger("pyrekordbox").setLevel(logging.ERROR)

ROOT_PARENT = "root"
# rekordbox's own internal lists (CUE analysis and friends). They have no NODE in masterPlaylists6.xml
# and never show up in its tree, so they are not the user's playlists and must not be mirrored.
SPECIAL_PLAYLISTS = {"100000", "200000"}


class RekordboxError(Exception):
    pass


def norm(path: str) -> str:
    """Path identity for matching files against rekordbox (macOS may hand back NFD names)."""
    return unicodedata.normalize("NFC", str(path))


def is_running() -> bool:
    try:
        return subprocess.run(["pgrep", "-x", "rekordbox"], capture_output=True).returncode == 0
    except OSError:
        return False


def _open(db_path: Path):
    from pyrekordbox import Rekordbox6Database  # slow import, keep it off app startup

    if not Path(db_path).exists():
        raise RekordboxError(f"rekordbox database not found: {db_path}")
    try:
        return Rekordbox6Database(path=str(db_path))
    except Exception as exc:
        raise RekordboxError(f"Could not open rekordbox database: {exc}") from exc


@dataclass
class RBTrack:
    content_id: str
    bpm: float  # or None when not analysed
    key: str  # Camelot or None
    tag_ids: set = field(default_factory=set)


@dataclass
class RBState:
    columns: list  # [(rb_id, name)] in rekordbox order
    values: list  # [(rb_id, parent_rb_id, name, seq)]
    tracks: dict  # norm(path) -> RBTrack
    playlists: list = field(default_factory=list)  # [(rb_id, parent_rb_id, name, seq, attribute, smart_xml)]
    playlist_songs: dict = field(default_factory=dict)  # playlist rb_id -> [content_id] in TrackNo order


def read_state(db_path: Path) -> RBState:
    db = _open(db_path)
    try:
        tags = db.get_my_tag().all()
        columns = [(t.ID, t.Name) for t in sorted(tags, key=lambda t: t.Seq or 0) if t.ParentID == ROOT_PARENT]
        column_ids = {c[0] for c in columns}
        values = [(t.ID, t.ParentID, t.Name, t.Seq or 0) for t in tags if t.ParentID in column_ids]

        tracks = {}
        for c in db.get_content().all():
            if not c.FolderPath:
                continue
            bpm = c.BPM / 100 if c.BPM else None
            key = to_camelot(c.Key.ScaleName) if c.Key is not None else None
            tracks[norm(c.FolderPath)] = RBTrack(c.ID, bpm, key)
        by_id = {t.content_id: t for t in tracks.values()}
        for link in db.get_my_tag_songs().all():
            track = by_id.get(link.ContentID)
            if track is not None:
                track.tag_ids.add(link.MyTagID)

        playlists = [
            (p.ID, p.ParentID, p.Name, p.Seq or 0, p.Attribute or 0, p.SmartList)
            for p in db.get_playlist().all()
            if p.ID not in SPECIAL_PLAYLISTS
        ]
        songs = {}
        for song in db.get_playlist_songs().all():
            songs.setdefault(song.PlaylistID, []).append((song.TrackNo or 0, song.ContentID))
        playlist_songs = {pid: [cid for _, cid in sorted(rows)] for pid, rows in songs.items()}
        return RBState(columns, values, tracks, playlists, playlist_songs)
    finally:
        db.close()


PLAYLIST_XML = "masterPlaylists6.xml"


def backup(db_path: Path) -> Path:
    """Snapshot everything a sync can write.

    That is master.db *and* masterPlaylists6.xml: rekordbox builds its playlist tree from the XML, which
    pyrekordbox saves as a plain file write outside the SQL transaction. Restoring the database alone
    would leave NODE entries for playlists that no longer exist.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = BACKUP_DIR / f"master_{stamp}.db"
    shutil.copy2(db_path, target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(target) + suffix))
    xml = Path(db_path).parent / PLAYLIST_XML
    if xml.exists():
        shutil.copy2(xml, BACKUP_DIR / f"masterPlaylists6_{stamp}.xml")
    for old in sorted(BACKUP_DIR.glob("master_*.db"))[:-BACKUPS_KEPT]:
        stale = old.name[len("master_"):-len(".db")]
        for p in (old, Path(str(old) + "-wal"), Path(str(old) + "-shm"),
                  BACKUP_DIR / f"masterPlaylists6_{stale}.xml"):
            p.unlink(missing_ok=True)
    return target


@dataclass
class SyncPlan:
    column_renames: list  # [(rb_id, name)]
    new_values: list  # [(local_id, parent_rb_id, name)]
    value_renames: list  # [(rb_id, name)]
    value_deletes: list  # [rb_id]
    moves: list  # [(old_path, new_path)]
    track_tags: dict  # path -> set of local value ids (the full desired set)
    value_rb_ids: dict  # local value id -> rb_id, for values that already exist in rekordbox
    playlist_creates: list = field(default_factory=list)  # [(local_id, parent_rb_id or None, name)]
    playlist_renames: list = field(default_factory=list)  # [(rb_id, name)]
    playlist_deletes: list = field(default_factory=list)  # [rb_id]
    playlist_members: dict = field(default_factory=dict)  # local_id -> [(path, rb_content_id)] in order
    playlist_rb_ids: dict = field(default_factory=dict)  # local playlist id -> rb_id, for ones rekordbox has

    def touches_playlists(self) -> bool:
        return bool(
            self.playlist_creates or self.playlist_renames
            or self.playlist_deletes or self.playlist_members
        )


@dataclass
class SyncResult:
    backup_path: Path
    created: dict  # local value id -> new rb_id
    synced_paths: list
    not_in_rekordbox: list
    moves_applied: int
    anlz_errors: list
    playlists_created: dict = field(default_factory=dict)  # local playlist id -> new rb_id
    playlist_drops: dict = field(default_factory=dict)  # local playlist id -> members rekordbox doesn't have


def sync(db_path: Path, plan: SyncPlan) -> SyncResult:
    if is_running():
        raise RekordboxError("rekordbox is open. Quit rekordbox, then sync again.")
    from pyrekordbox.db6 import tables as T

    backup_path = backup(db_path)
    db = _open(db_path)
    try:
        # 1. File moves first: the tagged paths below are the new locations.
        all_contents = db.get_content().all()
        contents = {norm(c.FolderPath): c for c in all_contents if c.FolderPath}
        content_by_id = {c.ID: c for c in all_contents}
        moved = []
        for old, new in plan.moves:
            content = contents.pop(norm(old), None)
            if content is None or not Path(new).exists():
                continue
            # Same columns pyrekordbox's update_content_path sets; that helper crashes on unanalysed tracks, and
            # ANLZ files are rewritten only once the database commit has succeeded.
            if content.OrgFolderPath == content.FolderPath:
                content.OrgFolderPath = new
            content.FolderPath = new
            content.FileNameL = os.path.basename(new)
            contents[norm(new)] = content
            moved.append(content)

        # 2. Tag columns and values.
        tags = {t.ID: t for t in db.get_my_tag().all()}
        for rb_id, name in plan.column_renames + plan.value_renames:
            if rb_id in tags:
                tags[rb_id].Name = name

        for rb_id in plan.value_deletes:
            for link in db.get_my_tag_songs(MyTagID=rb_id).all():
                db.delete(link)
            if rb_id in tags:
                db.delete(tags.pop(rb_id))

        created = {}
        for local_id, parent_id, name in plan.new_values:
            if parent_id not in tags:
                continue
            seq = max((t.Seq or 0 for t in tags.values() if t.ParentID == parent_id), default=0) + 1
            rb_id = str(db.generate_unused_id(T.DjmdMyTag))
            tag = T.DjmdMyTag(ID=rb_id, Seq=seq, Name=name, Attribute=0, ParentID=parent_id, UUID=str(uuid.uuid4()))
            db.add(tag)
            tags[rb_id] = tag
            created[local_id] = rb_id
        db.flush()

        # 3. Per-track assignments: make rekordbox's set equal the desired set.
        rb_ids = {**plan.value_rb_ids, **created}
        next_track_no = {}
        for link in db.get_my_tag_songs().all():
            next_track_no[link.MyTagID] = max(next_track_no.get(link.MyTagID, 0), link.TrackNo or 0)
        synced, missing = [], []
        for path, local_ids in plan.track_tags.items():
            content = contents.get(norm(path))
            if content is None:
                missing.append(path)
                continue
            wanted = {rb_ids[i] for i in local_ids if i in rb_ids and rb_ids[i] in tags}
            have = {}
            for link in db.get_my_tag_songs(ContentID=content.ID).all():
                have[link.MyTagID] = link
            for tag_id, link in have.items():
                if tag_id not in wanted:
                    db.delete(link)
            for tag_id in wanted - have.keys():
                next_track_no[tag_id] = next_track_no.get(tag_id, 0) + 1
                db.add(
                    T.DjmdSongMyTag(
                        ID=str(db.generate_unused_id(T.DjmdSongMyTag)),
                        MyTagID=tag_id,
                        ContentID=content.ID,
                        TrackNo=next_track_no[tag_id],
                        UUID=str(uuid.uuid4()),
                    )
                )
                db.flush()  # generate_unused_id only sees flushed rows
            synced.append(path)

        # 4. Playlists, last: they reference the post-move paths and the tags are already settled.
        #
        # These go through pyrekordbox's helpers rather than hand-rolled rows (as the My Tags above do)
        # because they also maintain masterPlaylists6.xml, which is what rekordbox reads to build its
        # playlist tree -- a DjmdPlaylist row with no NODE in that file is invisible in rekordbox.
        # `remove_from_playlist` is the one helper avoided: it commits mid-call, which would break the
        # single-transaction contract above. Reparenting isn't pushed at all in v1 (`move_playlist`
        # cannot move a playlist back to the root, and renumbers siblings on every call).
        playlists_created, playlist_drops = {}, {}
        if plan.touches_playlists():
            if db.playlist_xml is None:
                raise RekordboxError(
                    f"{PLAYLIST_XML} is missing next to the database. rekordbox builds its playlist tree "
                    "from that file, so playlists written without it would never show up."
                )
            for rb_id in plan.playlist_deletes:
                playlist = db.get_playlist(ID=rb_id)
                if playlist is None:
                    continue  # already gone from rekordbox
                for song in db.get_playlist_songs(PlaylistID=rb_id).all():
                    db.delete(song)
                db.flush()
                db.delete_playlist(playlist)

            for local_id, parent_rb_id, name in plan.playlist_creates:
                if parent_rb_id is not None and db.get_playlist(ID=parent_rb_id) is None:
                    parent_rb_id = None  # the folder went away in rekordbox; land at the root
                playlists_created[local_id] = db.create_playlist(name, parent=parent_rb_id).ID
            db.flush()

            for rb_id, name in plan.playlist_renames:
                if db.get_playlist(ID=rb_id) is not None:
                    db.rename_playlist(rb_id, name)

            rb_by_local = {**plan.playlist_rb_ids, **playlists_created}
            for local_id, entries in plan.playlist_members.items():
                rb_id = rb_by_local.get(local_id)
                if rb_id is None or db.get_playlist(ID=rb_id) is None:
                    continue
                for song in db.get_playlist_songs(PlaylistID=rb_id).all():
                    db.delete(song)
                db.flush()
                dropped, track_no = [], 0
                for path, content_id in entries:
                    # The stored content id is the fallback, so a list edited while the files are
                    # unreachable (stick unplugged) still pushes back whole instead of shrinking.
                    content = contents.get(norm(path)) if path else None
                    if content is None and content_id:
                        content = content_by_id.get(content_id)
                    if content is None:
                        dropped.append(path or content_id)
                        continue
                    track_no += 1  # dense: a hole would make the next sync compute a different order
                    db.add(
                        T.DjmdSongPlaylist(
                            ID=str(uuid.uuid4()),
                            PlaylistID=rb_id,
                            ContentID=content.ID,
                            TrackNo=track_no,
                            UUID=str(uuid.uuid4()),
                        )
                    )
                if dropped:
                    playlist_drops[local_id] = dropped

        db.commit()
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        db.close()
        raise RekordboxError(f"Sync failed, nothing was written ({exc}). Backup: {backup_path}") from exc

    anlz_errors = []
    try:
        for content in moved:
            if not content.AnalysisDataPath:
                continue
            for anlz_path, anlz in db.read_anlz_files(content.ID).items():
                try:
                    anlz.set_path(content.FolderPath)
                    anlz.save(anlz_path)
                except Exception as exc:  # the DB is already committed; report, don't fail the sync
                    anlz_errors.append(f"{anlz_path.name}: {exc}")
    finally:
        db.close()
    return SyncResult(
        backup_path, created, synced, missing, len(moved), anlz_errors, playlists_created, playlist_drops
    )
