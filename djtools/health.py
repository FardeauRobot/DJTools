"""Library health: what's missing or messy, each with a plain explanation of why it matters. No Qt here.

Required checks count toward "X / N tracks complete"; warnings are worth a look but a track can be fine without.
"""
import os
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field

from . import naming
from .config import FAVORITE
from .scanner import _SKIP_DIRS

INBOX = "A TRIER"
LOSSY_EXTS = {".mp3", ".m4a", ".aac", ".ogg"}
LOW_BITRATE = 250  # kbps: below this, a 320 kbps or lossless copy sounds clearly better on a club system


@dataclass
class Check:
    id: str
    label: str
    explain: str
    paths: list  # tracks concerned (or folders, for "empty_folders")
    required: bool = False
    fix: str = None  # "cleanup" | "trash" | "trash_folders" | None
    fix_label: str = ""

    @property
    def count(self):
        return len(self.paths)


@dataclass
class Report:
    checks: list = field(default_factory=list)
    total: int = 0
    complete: int = 0
    rekordbox: bool = True  # whether the rekordbox checks are part of "complete"


def _nfc_lower(text):
    return unicodedata.normalize("NFC", text).lower()


def empty_folders(root):
    """Folders with no files at all below them (macOS "._" and .DS_Store files don't count).

    The inbox and BY ARTIST are part of the library's layout, so they're never offered even when empty."""
    keep = {os.path.join(root, INBOX), os.path.join(root, naming.ARTIST_ROOT)}
    empty = []
    has_files = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=False):
        name = os.path.basename(dirpath)
        if dirpath != root and (name.startswith(".") or name in _SKIP_DIRS):
            has_files[dirpath] = True  # never offer these
            continue
        files = [f for f in filenames if not f.startswith(".")]
        children = [os.path.join(dirpath, d) for d in dirnames]
        has_files[dirpath] = bool(files) or any(has_files.get(c, True) for c in children)
        if dirpath != root and dirpath not in keep and not has_files[dirpath]:
            empty.append(dirpath)
    # Only report the outermost empty folder of an empty subtree.
    empty_set = set(empty)
    return sorted(p for p in empty if os.path.dirname(p) not in empty_set)


def run(rows, proposals, empty, root, columns, values, rekordbox_readable):
    """`rows`: library Rows; `proposals`: naming proposals; `empty`: empty_folders(root);
    `columns`/`values`: the cache's My Tag definitions."""
    checks = []
    add = checks.append

    no_artist = [r.path for r in rows if not r.artist.strip() or r.artist.strip().lower() in naming.PLACEHOLDER_ARTISTS]
    add(Check(
        "no_artist", "No artist", required=True, paths=no_artist, fix="cleanup", fix_label="Fix names…",
        explain="These files don't say who made them. rekordbox and the CDJs show an empty artist, search can't "
        "find them, and they can't be filed under BY ARTIST. The cleanup can often read the artist from the filename.",
    ))
    no_title = [r.path for r in rows if not r.title_tag]
    add(Check(
        "no_title", "No title tag", required=True, paths=no_title, fix="cleanup", fix_label="Fix names…",
        explain="There is no title inside the file, only a filename. Here and in rekordbox the filename is shown "
        "instead, which breaks as soon as a file is renamed. Common with WAV files. The cleanup writes the title "
        "from the filename.",
    ))
    add(Check(
        "names", "Names to clean up", required=True, paths=[p.path for p in proposals], fix="cleanup",
        fix_label="Review and fix…",
        explain="Titles, artists or filenames that don't follow the library's rules: “Title - Artist” filenames in "
        "the main artist's folder, no key or “Artist - ” stuck in the title, “(Original Mix)” in brackets. "
        "Clean names make duplicates obvious and keep searches reliable. Every change is previewed first.",
    ))
    if rekordbox_readable:
        not_in_rb = [r.path for r in rows if not r.in_rekordbox]
        add(Check(
            "not_in_rekordbox", "Not in rekordbox", required=True, paths=not_in_rb,
            explain="rekordbox doesn't know these files yet, so they can't go on a USB export and their My Tags "
            "can't be synced. In rekordbox, drag the library folder (or these files) into Collection, then "
            "Rescan here.",
        ))
        # The BPM (beat grid) is what analysis always produces; rekordbox finds no key for some percussive tracks.
        not_analyzed = [r.path for r in rows if r.in_rekordbox and r.bpm_src != "rekordbox"]
        add(Check(
            "not_analyzed", "Not analyzed by rekordbox", required=True, paths=not_analyzed,
            explain="rekordbox has these tracks but hasn't analyzed them: no beat grid, no reliable BPM or key, so "
            "sync and key matching on the decks won't work. In rekordbox select them, right-click → Analyze Track, "
            "then Rescan here. Grey BPM/key values come from the file tag or from DJTools' own detection and may be wrong.",
        ))

    value_column = {v["id"]: v["column_rb_id"] for v in values}
    for column in columns:
        name = column["name"]
        if name.lower() == FAVORITE.lower():
            continue
        missing = [r.path for r in rows if not any(value_column.get(v) == column["rb_id"] for v in r.value_ids)]
        is_genre = name.lower() == "genre"
        add(Check(
            f"no_tag_{column['rb_id']}", f"No {name} tag", required=is_genre, paths=missing,
            explain=(
                "Tracks without a Genre can't be found when you filter by style while preparing a set. One or two "
                "genres per track is enough; select several tracks and tick a genre to tag them all at once."
                if is_genre else
                f"Optional. Tracks with no “{name}” {'My Tag' if rekordbox_readable else 'tag'}. Filling it in makes "
                "filtering easier while you prepare a set; it's fine to leave it for later."
            ),
        ))

    # A TRIER is the inbox: Sort-Music.ps1 copies tracks there *and* into BY ARTIST.
    # Tags (and so the file size) may have been edited on one copy since, so compare the audio length instead.
    inbox = os.path.join(root, INBOX) + os.sep
    filed = defaultdict(list)  # name or match key -> durations of filed files
    for r in rows:
        if not r.path.startswith(inbox):
            filed[_nfc_lower(os.path.basename(r.path))].append(r.duration)
            if r.artist:
                filed[naming.match_key(r.artist, r.title)].append(r.duration)

    def already_filed(r):
        keys = [_nfc_lower(os.path.basename(r.path))] + ([naming.match_key(r.artist, r.title)] if r.artist else [])
        return any(r.duration and d and abs(d - r.duration) < 1.0 for k in keys for d in filed.get(k, ()))

    inbox_copies = [r.path for r in rows if r.path.startswith(inbox) and already_filed(r)]
    add(Check(
        "inbox_copies", f"{INBOX} copies already filed", paths=inbox_copies, fix="trash", fix_label="Move copies to Trash…",
        explain=f"{INBOX} is the inbox for new tracks. These files already have an identical copy (same name or "
        "same artist and title, same length) in BY ARTIST, so they're leftovers taking space and showing up twice. "
        "Moving them to the Trash doesn't touch the filed copy.",
    ))

    groups = defaultdict(list)
    for r in rows:
        if r.artist and not r.path.startswith(inbox):
            groups[naming.match_key(r.artist, r.title)].append(r.path)
    duplicates = sorted(p for paths in groups.values() if len(paths) > 1 for p in paths)
    add(Check(
        "duplicates", "Possible duplicates", paths=duplicates,
        explain="Several files with the same artist and title (ignoring case, accents and punctuation), often "
        "the same track in two formats. Play them, keep the best one (FLAC/WAV, or the higher bitrate) and move "
        "the others to the Trash. Sort by Title to see them side by side.",
    ))

    low = [r.path for r in rows if r.ext in LOSSY_EXTS and r.bitrate and r.bitrate < LOW_BITRATE]
    add(Check(
        "low_bitrate", f"Low quality (under {LOW_BITRATE} kbps)", paths=low,
        explain="Compressed files under 250 kbps lose detail that's clearly audible on a club sound system "
        "(harsh highs, weak bass). Look for a 320 kbps MP3, FLAC or WAV of these tracks.",
    ))
    no_cover = [r.path for r in rows if not r.has_cover]
    add(Check(
        "no_cover", "No artwork", paths=no_cover,
        explain="No cover image inside the file. Purely visual: artwork helps you recognise a track at a glance "
        "on the CDJ screen and in rekordbox. Nothing breaks without it.",
    ))
    unreadable = [r.path for r in rows if not r.readable]
    add(Check(
        "unreadable", "Unreadable files", paths=unreadable,
        explain="These files couldn't be opened: damaged, incomplete downloads, or not really audio. Try playing "
        "them; if they don't play, delete them and download them again.",
    ))
    loose = [r.path for r in rows if r.folder == ""]
    add(Check(
        "loose", "Loose files in the library root", paths=loose,
        explain=f"Files sitting directly in the library folder instead of {INBOX} or BY ARTIST. Drag them onto "
        "a folder, or run the Windows sort script, so every track has a place.",
    ))
    if empty is not None:
        add(Check(
            "empty_folders", "Empty folders", paths=empty, fix="trash_folders",
            fix_label="Move empty folders to Trash…",
            explain="Folders with no tracks in them, usually left behind after moving files. They clutter the "
            "folder list and rekordbox's browser.",
        ))

    required = [c for c in checks if c.required]
    failing = set()
    for c in required:
        failing.update(c.paths)
    return Report(
        checks=checks, total=len(rows), complete=sum(1 for r in rows if r.path not in failing),
        rekordbox=rekordbox_readable,
    )
