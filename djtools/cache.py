"""Local SQLite store: scanned file metadata plus the working copy of tags and pending rekordbox changes."""
import sqlite3

from .rekordbox import norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    path TEXT PRIMARY KEY, nkey TEXT NOT NULL, title TEXT, artist TEXT, file_key TEXT, file_bpm REAL,
    duration REAL, size INTEGER, mtime REAL, title_tag INTEGER, bitrate INTEGER, has_cover INTEGER, readable INTEGER
);
CREATE INDEX IF NOT EXISTS tracks_nkey ON tracks(nkey);
CREATE TABLE IF NOT EXISTS tag_columns (
    rb_id TEXT PRIMARY KEY, name TEXT NOT NULL, position INTEGER NOT NULL, dirty INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tag_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT, column_rb_id TEXT NOT NULL, name TEXT NOT NULL, seq INTEGER NOT NULL,
    rb_id TEXT UNIQUE, dirty INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS track_tags (
    path TEXT NOT NULL, value_id INTEGER NOT NULL, PRIMARY KEY (path, value_id)
);
CREATE TABLE IF NOT EXISTS dirty_tracks (path TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS pending_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, old_path TEXT NOT NULL, new_path TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS track_links (
    a TEXT NOT NULL, b TEXT NOT NULL, note TEXT NOT NULL DEFAULT '', created REAL, PRIMARY KEY (a, b)
);
"""
TRACK_COLUMNS_V2 = ("title_tag", "bitrate", "has_cover", "readable")


class Cache:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._migrate()
        self.db.commit()

    def _migrate(self):
        have = {r["name"] for r in self.db.execute("PRAGMA table_info(tracks)")}
        added = [c for c in TRACK_COLUMNS_V2 if c not in have]
        for column in added:
            self.db.execute(f"ALTER TABLE tracks ADD COLUMN {column} INTEGER")
        if added:
            self.db.execute("UPDATE tracks SET size=-1")  # re-read every file at the next scan

    def close(self):
        self.db.close()

    # --- tracks -----------------------------------------------------------------------------------

    def track_stamps(self):
        return {r["path"]: (r["size"], r["mtime"]) for r in self.db.execute("SELECT path, size, mtime FROM tracks")}

    def upsert_tracks(self, infos):
        self.db.executemany(
            "INSERT OR REPLACE INTO tracks (path, nkey, title, artist, file_key, file_bpm, duration, size, mtime, "
            "title_tag, bitrate, has_cover, readable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (i.path, norm(i.path), i.title, i.artist, i.key, i.bpm, i.duration, i.size, i.mtime,
                 int(i.title_tag), i.bitrate, int(i.has_cover), int(i.readable))
                for i in infos
            ],
        )
        self.db.commit()

    def forget_tracks(self, paths):
        """Drop files that no longer exist. Pending moves are kept: they still describe rekordbox's paths.
        Links are kept too: they exist only here, and a file renamed outside DJTools would otherwise lose them.
        The Links window lists them as missing."""
        rows = [(p,) for p in paths]
        self.db.executemany("DELETE FROM tracks WHERE path=?", rows)
        self.db.executemany("DELETE FROM track_tags WHERE path=?", rows)
        self.db.executemany("DELETE FROM dirty_tracks WHERE path=?", rows)
        self.db.commit()

    def tracks(self, root):
        like = root.rstrip("/") + "/%"
        return self.db.execute("SELECT * FROM tracks WHERE path LIKE ? ORDER BY path", (like,)).fetchall()

    def rename_track(self, old, new, record_move=True):
        for table in ("tracks", "track_tags", "dirty_tracks"):
            self.db.execute(f"UPDATE OR REPLACE {table} SET path=? WHERE path=?", (new, old))
        self.db.execute("UPDATE tracks SET nkey=? WHERE path=?", (norm(new), new))
        self._rename_links(old, new)
        # Chain moves so rekordbox gets a single old -> final update.
        row = self.db.execute("SELECT id FROM pending_moves WHERE new_path=?", (old,)).fetchone()
        if row:
            self.db.execute("UPDATE pending_moves SET new_path=? WHERE id=?", (new, row["id"]))
        elif record_move:
            self.db.execute("INSERT OR REPLACE INTO pending_moves (old_path, new_path) VALUES (?,?)", (old, new))
        self.db.commit()

    # --- tags ------------------------------------------------------------------------------------

    def columns(self):
        return self.db.execute("SELECT * FROM tag_columns ORDER BY position").fetchall()

    def values(self):
        return self.db.execute("SELECT * FROM tag_values WHERE deleted=0 ORDER BY column_rb_id, seq, id").fetchall()

    def track_tag_map(self):
        out = {}
        for r in self.db.execute("SELECT path, value_id FROM track_tags"):
            out.setdefault(r["path"], set()).add(r["value_id"])
        return out

    def rename_column(self, rb_id, name):
        self.db.execute("UPDATE tag_columns SET name=?, dirty=1 WHERE rb_id=?", (name, rb_id))
        self.db.commit()

    def add_value(self, column_rb_id, name):
        seq = self.db.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM tag_values WHERE column_rb_id=?", (column_rb_id,)
        ).fetchone()[0]
        cur = self.db.execute(
            "INSERT INTO tag_values (column_rb_id, name, seq) VALUES (?,?,?)", (column_rb_id, name, seq)
        )
        self.db.commit()
        return cur.lastrowid

    def rename_value(self, value_id, name):
        self.db.execute("UPDATE tag_values SET name=?, dirty=1 WHERE id=?", (name, value_id))
        self.db.commit()

    def delete_value(self, value_id):
        row = self.db.execute("SELECT rb_id FROM tag_values WHERE id=?", (value_id,)).fetchone()
        if row is None:
            return
        # No need to dirty the tracks carrying it: sync removes every link to a deleted tag.
        if row["rb_id"] is None:
            self.db.execute("DELETE FROM tag_values WHERE id=?", (value_id,))
        else:
            self.db.execute("UPDATE tag_values SET deleted=1 WHERE id=?", (value_id,))
        self.db.execute("DELETE FROM track_tags WHERE value_id=?", (value_id,))
        self.db.commit()

    def set_tag(self, paths, value_id, on):
        if on:
            self.db.executemany("INSERT OR IGNORE INTO track_tags VALUES (?,?)", [(p, value_id) for p in paths])
        else:
            self.db.executemany("DELETE FROM track_tags WHERE path=? AND value_id=?", [(p, value_id) for p in paths])
        self.db.executemany("INSERT OR IGNORE INTO dirty_tracks VALUES (?)", [(p,) for p in paths])
        self.db.commit()

    # --- links ("goes well with") --------------------------------------------------------------------
    # Undirected: stored once, with a < b.

    @staticmethod
    def link_pair(x, y):
        return (x, y) if x < y else (y, x)

    def links(self):
        return self.db.execute("SELECT * FROM track_links ORDER BY created").fetchall()

    def link(self, x, y):
        return self.db.execute("SELECT * FROM track_links WHERE a=? AND b=?", self.link_pair(x, y)).fetchone()

    def add_link(self, x, y, note="", created=None):
        a, b = self.link_pair(x, y)
        self.db.execute(
            "INSERT OR IGNORE INTO track_links (a, b, note, created) VALUES (?,?,?,?)", (a, b, note or "", created)
        )
        self.db.commit()

    def remove_link(self, x, y):
        self.db.execute("DELETE FROM track_links WHERE a=? AND b=?", self.link_pair(x, y))
        self.db.commit()

    def set_link_note(self, x, y, note):
        self.db.execute("UPDATE track_links SET note=? WHERE a=? AND b=?", (note, *self.link_pair(x, y)))
        self.db.commit()

    def _rename_links(self, old, new):
        rows = self.db.execute("SELECT * FROM track_links WHERE a=? OR b=?", (old, old)).fetchall()
        for r in rows:
            self.db.execute("DELETE FROM track_links WHERE a=? AND b=?", (r["a"], r["b"]))
            other = r["b"] if r["a"] == old else r["a"]
            if other != new:
                a, b = self.link_pair(new, other)
                self.db.execute(
                    "INSERT OR IGNORE INTO track_links (a, b, note, created) VALUES (?,?,?,?)",
                    (a, b, r["note"], r["created"]),
                )

    # --- rekordbox reconciliation ------------------------------------------------------------------

    def import_rekordbox(self, state):
        """Bring rekordbox's tags in, without overwriting anything edited here and not yet synced."""
        db = self.db
        rb_column_ids = [c[0] for c in state.columns]
        for position, (rb_id, name) in enumerate(state.columns):
            db.execute(
                "INSERT INTO tag_columns (rb_id, name, position) VALUES (?,?,?) "
                "ON CONFLICT(rb_id) DO UPDATE SET position=excluded.position, "
                "name=CASE WHEN dirty THEN name ELSE excluded.name END",
                (rb_id, name, position),
            )
        if rb_column_ids:
            db.execute(
                f"DELETE FROM tag_columns WHERE rb_id NOT IN ({','.join('?' * len(rb_column_ids))})", rb_column_ids
            )

        rb_value_ids = {v[0] for v in state.values}
        for rb_id, parent, name, seq in state.values:
            db.execute(
                "INSERT INTO tag_values (column_rb_id, name, seq, rb_id) VALUES (?,?,?,?) "
                "ON CONFLICT(rb_id) DO UPDATE SET column_rb_id=excluded.column_rb_id, seq=excluded.seq, "
                "name=CASE WHEN dirty THEN name ELSE excluded.name END",
                (parent, name, seq, rb_id),
            )
        for row in db.execute("SELECT id, rb_id FROM tag_values WHERE rb_id IS NOT NULL").fetchall():
            if row["rb_id"] not in rb_value_ids:  # deleted inside rekordbox
                db.execute("DELETE FROM tag_values WHERE id=?", (row["id"],))
                db.execute("DELETE FROM track_tags WHERE value_id=?", (row["id"],))

        local_by_rb = {r["rb_id"]: r["id"] for r in db.execute("SELECT id, rb_id FROM tag_values WHERE rb_id IS NOT NULL")}
        dirty = {r["path"] for r in db.execute("SELECT path FROM dirty_tracks")}
        for row in db.execute("SELECT path, nkey FROM tracks").fetchall():
            if row["path"] in dirty:
                continue
            rb_track = self.rekordbox_track(state, row["path"])
            db.execute("DELETE FROM track_tags WHERE path=?", (row["path"],))
            if rb_track is None:
                continue
            db.executemany(
                "INSERT OR IGNORE INTO track_tags VALUES (?,?)",
                [(row["path"], local_by_rb[t]) for t in rb_track.tag_ids if t in local_by_rb],
            )
        db.commit()

    def rekordbox_track(self, state, path):
        """rekordbox's entry for a file, following a move that hasn't been synced yet."""
        row = self.db.execute("SELECT old_path FROM pending_moves WHERE new_path=?", (path,)).fetchone()
        return state.tracks.get(norm(row["old_path"] if row else path))

    def pending(self):
        q = lambda sql: self.db.execute(sql).fetchall()  # noqa: E731
        return {
            "column_renames": [(r["rb_id"], r["name"]) for r in q("SELECT * FROM tag_columns WHERE dirty=1")],
            "new_values": [
                (r["id"], r["column_rb_id"], r["name"])
                for r in q("SELECT * FROM tag_values WHERE rb_id IS NULL AND deleted=0")
            ],
            "value_renames": [
                (r["rb_id"], r["name"])
                for r in q("SELECT * FROM tag_values WHERE dirty=1 AND rb_id IS NOT NULL AND deleted=0")
            ],
            "value_deletes": [r["rb_id"] for r in q("SELECT rb_id FROM tag_values WHERE deleted=1")],
            "moves": [(r["old_path"], r["new_path"]) for r in q("SELECT * FROM pending_moves ORDER BY id")],
            "dirty_tracks": [r["path"] for r in q("SELECT path FROM dirty_tracks")],
        }

    def value_rb_ids(self):
        return {r["id"]: r["rb_id"] for r in self.db.execute("SELECT id, rb_id FROM tag_values WHERE rb_id IS NOT NULL")}

    def mark_synced(self, result, plan):
        db = self.db
        for local_id, rb_id in result.created.items():
            db.execute("UPDATE tag_values SET rb_id=? WHERE id=?", (rb_id, local_id))
        db.execute("UPDATE tag_columns SET dirty=0")
        db.execute("UPDATE tag_values SET dirty=0")
        db.execute("DELETE FROM tag_values WHERE deleted=1")
        # Moves of files rekordbox doesn't know are dropped too: there is nothing left to update.
        db.execute("DELETE FROM pending_moves")
        db.executemany("DELETE FROM dirty_tracks WHERE path=?", [(p,) for p in result.synced_paths])
        db.commit()
