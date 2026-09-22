# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A PySide6 desktop app (macOS) for a DJ library: browse, play, tag and organise tracks, and push the tags
into rekordbox 6 as real **My Tags**. `README.md` is the user manual — features, keyboard shortcuts,
workflow. Don't restate it here; this file is the parts you only see by reading several modules at once.

## Commands

```sh
./run.sh                                        # start the app; bootstraps .venv on first run
DJTOOLS_RB_DB=/path/to/copy/master.db ./run.sh  # run against a COPY of the rekordbox database
```

`run.sh` finds any Python 3.10+ on `PATH`, creates `.venv`, and reinstalls when `requirements.txt`
**changes by content** (`cksum`, not mtime — a fresh clone's timestamps say nothing).

**There is no test suite** — no pytest, no test files, nothing to run. The verification story is the env
var above: `DJTOOLS_RB_DB` points the app at a copy of `master.db`, and it is how you exercise anything
that writes to rekordbox without risking the real collection. Use it whenever you touch `rekordbox.py`,
`cache.py`'s sync bookkeeping, or `library.sync()`.

## The three layers

```
disk (scanner.py) → local SQLite cache (cache.py) → rekordbox master.db (rekordbox.py)
```

- The **cache** (`~/Library/Application Support/DJTools/cache.db`) is the working copy and the only thing
  the UI reads. Tagging, linking and moving write here immediately.
- **`rekordbox.py` is the only module that opens `master.db`.** Reads are safe any time; every write goes
  through `sync()`, which refuses while rekordbox is running (it holds its own in-memory copy and would
  clobber ours) and takes a timestamped backup first (last 20 kept).
- `requirements.txt` pins `pyrekordbox==0.4.4` exactly while the others are `>=`: `rekordbox.py` reaches
  into `pyrekordbox.db6.tables` and `db.generate_unused_id`, and hand-rolls what `update_content_path`
  does because that helper crashes on unanalysed tracks. Don't bump the pin casually.
- The Mac's rekordbox is therefore the authoritative collection for these tags — a Windows export would
  overwrite the stick with a collection that has none. That constraint is why sync is this careful.

## Reconciliation invariants (the easiest thing to break)

- `dirty` on `tag_columns` / `tag_values`, and rows in `dirty_tracks`, mean **edited here, not yet pushed**.
  `import_rekordbox()`'s `name=CASE WHEN dirty THEN name ELSE excluded.name END` is what makes local edits
  survive a rescan. "Simplifying" that upsert silently discards the user's work.
- `rb_id IS NULL` = the tag exists only locally; `deleted=1` = a tombstone kept so sync can delete it in
  rekordbox. `mark_synced()` back-fills `rb_id` from `result.created`.
- `pending_moves` **chains** old → final so rekordbox gets one update per file, and
  `Cache.rekordbox_track()` looks a file up under its *pre-move* path. Moves of files rekordbox never knew
  are dropped at sync.
- **Links ("goes well with") never reach rekordbox.** Stored undirected with `a < b`, and deliberately
  *kept* by `forget_tracks()` — a file renamed outside DJTools shows as missing in the Links window rather
  than losing its links.
- `Cache._migrate` is additive only: `ALTER TABLE` plus `UPDATE tracks SET size=-1` to force a re-read.

### Playlists

- A `DjmdPlaylist` row with no `NODE` in **`masterPlaylists6.xml`** is invisible in rekordbox. That's why
  `sync()` uses pyrekordbox's `create_playlist`/`delete_playlist` (they write the XML) instead of hand-rolling
  rows, refuses when `db.playlist_xml is None` (a lone `master.db` copy), and `backup()` snapshots the XML.
  `DJTOOLS_RB_DB` must point into a copied rekordbox **directory**, or you're testing the refusal.
- Never call pyrekordbox's `remove_from_playlist()` inside `sync()`: it commits mid-call and breaks the
  single-transaction rollback. Members are replaced by deleting `DjmdSongPlaylist` rows and re-adding.
- `playlist_tracks.rb_content_id` is kept for **every** member, and the push falls back to it when the path
  doesn't resolve. Drop that and a set edited with the stick unplugged comes back truncated.
- `deleted=1` is the trash, not a tombstone: `mark_synced()` clears `rb_id` instead of deleting the row,
  and a playlist rekordbox dropped is trashed (never hard-deleted) with `rb_id` cleared so no delete is
  pushed back. Only `purge_playlist()` destroys one. `set_members` is delete-then-insert because
  `PRIMARY KEY (playlist_id, pos)` collides on in-place renumbering.
- Not pushed in v1: folder creation, smart playlists, sibling order (`move_playlist` renumbers per call and
  can't target the root). Trashing a folder or smart playlist is refused (`playlist_blockers`): it could
  never be restored. A child whose parent has no `rb_id` is created at the rekordbox root, and the next
  import flattens it there; the UI can't make that state except when rekordbox deletes a folder under an
  unsynced local playlist.

### Running without rekordbox

`Settings.rekordbox_enabled` is unset on a fresh install and answered by looking for `master.db`, so
someone without rekordbox gets a working app instead of a permanent error. `DJTOOLS_RB_DB` overrides a
stored *off*, or the testing recipe would silently do nothing. When it's off, `ensure_local_columns()`
invents columns with `local:` ids (rekordbox's are digits, so no collision), created `dirty` so their
names arrive as renames later. `adopt_local_columns()` trades them for rekordbox's real columns by
position and **must run before `import_rekordbox()`**, which deletes every column rekordbox doesn't list.
Note `load_rekordbox_state()` returns `(None, None)` for "switched off" vs `(None, error)` for
"unreadable" — the UI tells them apart by asking the setting, not by the error being blank.

### Detected BPM

`tracks.detected_bpm` is filled by `analysis.analyze()` (beat_this, an optional extra in
`requirements-analysis.txt`, so every import of it is deferred). It is **cache-only** and last in
`Library.rows()`' order (rekordbox → file tag → detected): a tempo with no beat grid must never reach
`master.db` or the file. `upsert_tracks()`' `INSERT OR REPLACE` drops it when a file changes, which is
intended. `analyze()` opens its own `Cache`, like `scan()`. `analysis.tempo()` fits short windows because
beat_this reports beats on a 20 ms grid: a median interval is only good to ±2%.

## Three different notions of path equality

Picking the wrong one is a silent bug:

- `rekordbox.norm()` — NFC only, for matching a file against rekordbox (macOS hands back NFD names).
- `naming.same_path()` — NFC **and** lowercase, because the stick is FAT and case-insensitive.
- `Library._on_disk_name()` — re-reads the directory to get the name as the filesystem actually stored it
  after a rename, so the next scan matches.

## Layering

- `library.py` holds the logic between UI and cache/rekordbox/filesystem. "No Qt **widgets**" — it does
  import `QtCore.QFile` for `moveToTrash`; don't "fix" that import, it's what trashing runs on.
- `scan()` and `import_files()` are module-level functions that open their **own** `Cache` connection:
  they run in a `QThread` and sqlite connections don't cross threads.
- `keymap.TRACK_KEYS` is the single table read by the vim dispatcher, the menus and the cheat sheet. Its
  docstring lists which keys are deliberately *not* rebindable (dispatcher grammar, or Qt shortcuts that
  would silently win) — keep that accurate.
- `naming.py` mirrors `MusicLib.ps1` on the stick so this app and the Windows scripts agree on what a
  clean name is. Rules DJTools adds are marked `(DJTools)`; don't change a shared rule without that in mind.

## UI refresh contract

- `_rebuild()` is the full reload: rows, tag panel, links, health with `refilter=True` — and it calls
  `library.clear_undo()`, because paths or tag definitions may have changed under the recorded steps.
  `keep_undo=True` skips that, for a reload after which neither can have changed (BPM detection).
- `_tags_edited()` is the cheap path after ticking tags: `model.refresh_tags()` keeps selection and scroll,
  and health runs with `files_changed=False` so tagging a track doesn't make it vanish from the view.
- `_definitions_edited()` (a tag renamed, added, deleted) goes through the full `_rebuild()`.
- `Library.health()` caches naming proposals and empty folders in `_health_inputs`; call
  `invalidate_health()` when a file's name or location changed.

## Destructive surface

`rekordbox.sync()` (writes `master.db`), `Library.trash()` (macOS Trash), `Library.apply_cleanup()`
(writes title/artist tags into files, then renames and moves them — every attempt logged to a CSV in
`~/Library/Application Support/DJTools/logs/`). Treat changes to these three as changes to user data.
