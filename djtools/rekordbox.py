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
        return RBState(columns, values, tracks)
    finally:
        db.close()


def backup(db_path: Path) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = BACKUP_DIR / f"master_{stamp}.db"
    shutil.copy2(db_path, target)
    for suffix in ("-wal", "-shm"):
        side = Path(str(db_path) + suffix)
        if side.exists():
            shutil.copy2(side, Path(str(target) + suffix))
    for old in sorted(BACKUP_DIR.glob("master_*.db"))[:-BACKUPS_KEPT]:
        for p in (old, Path(str(old) + "-wal"), Path(str(old) + "-shm")):
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


@dataclass
class SyncResult:
    backup_path: Path
    created: dict  # local value id -> new rb_id
    synced_paths: list
    not_in_rekordbox: list
    moves_applied: int
    anlz_errors: list


def sync(db_path: Path, plan: SyncPlan) -> SyncResult:
    if is_running():
        raise RekordboxError("rekordbox is open. Quit rekordbox, then sync again.")
    from pyrekordbox.db6 import tables as T

    backup_path = backup(db_path)
    db = _open(db_path)
    try:
        # 1. File moves first: the tagged paths below are the new locations.
        contents = {norm(c.FolderPath): c for c in db.get_content().all() if c.FolderPath}
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
    return SyncResult(backup_path, created, synced, missing, len(moved), anlz_errors)
