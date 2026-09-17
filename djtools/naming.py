"""The library's naming rules, and cleanup proposals built from them.

The rules mirror MusicLib.ps1 on the stick (Remove-CamelotKey, Remove-RepeatedSuffix, Convert-MixSuffix,
Build-TrackFileName, Get-ArtistList, Get-MatchKey, Resolve-ArtistFolder), so this app and the Windows scripts
agree on what a clean name is. Additions not in the scripts are marked "(DJTools)".

Files are named "Title - Artist.ext"; under BY ARTIST they live in the primary (first credited) artist's folder.
"""
import os
import re
import unicodedata
from dataclasses import dataclass, field

ARTIST_ROOT = "BY ARTIST"
MAX_STEM = 150
PROTECTED_ARTISTS = [
    "Earth, Wind & Fire",
    "Crosby, Stills & Nash",
    "Crosby, Stills, Nash & Young",
    "Blood, Sweat & Tears",
    "Emerson, Lake & Palmer",
]

_CAMELOT = re.compile(r"^\s*\d{1,2}[ABab]\s*-\s*")
_MIX = re.compile(
    r"^(.*\S)\s+-\s+(Original Mix|Extended Mix|Extended Version|Radio Edit|Radio Mix|Club Mix|Dub Mix|Instrumental|"
    r"Original Version|Vocal Mix|VIP Mix|VIP|.{2,40}?\s+Remix|.{2,40}?\s+Edit|.{2,40}?\s+Bootleg)\s*$",
    re.IGNORECASE,
)
_REPEATED = re.compile(r"([\(\[])\s*([^\)\]]+?)\s*[\)\]]\s*([\(\[])\s*([^\)\]]+?)\s*[\)\]]")
_TRACK_NUMBER = re.compile(r"^\s*\d{1,3}\s*[.)_-]\s+(?=\S)")  # "02. ", "01 - ", "3) " (DJTools)
_QUOTED = re.compile(r'^["“”](.+)["“”]$')  # (DJTools)
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_ARTIST_SPLIT = re.compile(r"\s*[;,，]\s*|\s+&\s+|\s+(?i:feat\.|ft\.|featuring|vs\.?|with)\s+")  # "，" (DJTools)
PLACEHOLDER_ARTISTS = {"various artists", "va", "unknown artist", "unknown"}  # treated as no artist (DJTools)


def same_path(a, b):
    """The stick is FAT (case-insensitive) and macOS may return decomposed accents."""
    return unicodedata.normalize("NFC", a).lower() == unicodedata.normalize("NFC", b).lower()


def _spaces(text):
    return re.sub(r"\s{2,}", " ", text).strip()


def remove_camelot_key(text):
    return _CAMELOT.sub("", text or "").strip()


def remove_repeated_suffix(text):
    def collapse(m):
        if m.group(2).strip().lower() != m.group(4).strip().lower():
            return m.group(0)
        return m.group(1) + m.group(2).strip() + ("]" if m.group(1) == "[" else ")")

    result = text or ""
    for _ in range(5):
        before, result = result, _REPEATED.sub(collapse, result)
        if result == before:
            break
    return _spaces(result)


def convert_mix_suffix(text):
    m = _MIX.match(text or "")
    return f"{m.group(1).strip()} ({m.group(2).strip()})" if m else (text or "")


def safe_name(text):
    return _spaces(_UNSAFE.sub("", text or "")).rstrip(". ")


def folded(text):
    """Accents stripped, for comparisons only."""
    decomposed = unicodedata.normalize("NFD", text or "")
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def match_key(artist, title):
    """Two files with the same key are the same track (Get-MatchKey)."""
    squash = lambda s: re.sub(r"[^a-z0-9]", "", folded(s).lower())  # noqa: E731
    return f"{squash(artist)}|{squash(title)}"


def artist_list(artists):
    artists = (artists or "").strip()
    if not artists:
        return []
    for name in PROTECTED_ARTISTS:
        if artists.lower() == name.lower():
            return [name]
    tokens = {}
    for i, name in enumerate(PROTECTED_ARTISTS):
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(artists):
            token = f"@@PROTECTED{i}@@"
            tokens[token] = name
            artists = pattern.sub(token, artists)
    out = []
    for part in _ARTIST_SPLIT.split(artists):
        for token, name in tokens.items():
            part = part.replace(token, name)
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def clean_artist(artist, split_slash=True):
    """Separators normalised to ", " the way the Windows Shell joins multi-valued artists (Get-AudioTags).

    "/" is kept when `split_slash` is False: some names contain it (Burger/Ink, KI/KI)."""
    artist = remove_camelot_key(artist)
    parts = [p.strip() for p in re.split(r"[;/，]" if split_slash else r"[;，]", artist)]
    return _spaces(", ".join(p for p in parts if p))


def clean_title(title, artist):
    t = convert_mix_suffix(remove_repeated_suffix(remove_camelot_key(title)))
    t = _TRACK_NUMBER.sub("", t)
    m = _QUOTED.match(t)
    if m:
        t = m.group(1).strip()
    credits = artist_list(artist)
    for prefix in [artist] + credits[:1]:  # taggers sometimes put "Artist - Title" in the title
        if prefix and len(t) > len(prefix) + 3 and t.lower().startswith(prefix.lower() + " - "):
            t = t[len(prefix) + 3 :].strip()
            break
    return _spaces(t)


def file_stem(title, artist):
    stem = f"{safe_name(title)} - {safe_name(artist) or 'Unknown Artist'}"
    return stem[:MAX_STEM].rstrip(". ") if len(stem) > MAX_STEM else stem


# --- proposals -----------------------------------------------------------------------------------


@dataclass
class Proposal:
    path: str
    title: str  # current tag values ("" when missing)
    artist: str
    new_title: str
    new_artist: str
    new_path: str
    notes: list = field(default_factory=list)
    ok: bool = True  # False: shown, but not ticked by default
    conflict: bool = False  # the new name is taken (recomputed by mark_conflicts)

    @property
    def safe(self):
        return self.ok and not self.conflict

    @property
    def title_changed(self):
        return self.new_title != self.title

    @property
    def artist_changed(self):
        return self.new_artist != self.artist

    @property
    def path_changed(self):
        return unicodedata.normalize("NFC", self.new_path) != unicodedata.normalize("NFC", self.path)

    @property
    def changed(self):
        return self.title_changed or self.artist_changed or self.path_changed

    def describe(self, root):
        parts = []
        if self.title_changed:
            parts.append("fill title" if not self.title else "fix title")
        if self.artist_changed:
            parts.append("fill artist" if not self.artist else "fix artist")
        if self.path_changed:
            old_dir, new_dir = os.path.dirname(self.path), os.path.dirname(self.new_path)
            parts.append("rename" if old_dir == new_dir else f"move to {os.path.relpath(new_dir, root)}")
        return ", ".join(parts)


class ArtistFolders:
    """Finds the existing BY ARTIST folder for an artist, ignoring case and then accents (Resolve-ArtistFolder)."""

    def __init__(self, root):
        self.base = os.path.join(root, ARTIST_ROOT)
        try:
            names = [n for n in os.listdir(self.base) if os.path.isdir(os.path.join(self.base, n))]
        except OSError:
            names = []
        self.by_lower = {}
        self.by_folded = {}
        for n in sorted(names):
            self.by_lower.setdefault(unicodedata.normalize("NFC", n).lower(), n)
            self.by_folded.setdefault(folded(n).lower(), n)

    def folder_for(self, artist):
        credits = artist_list(artist)
        safe = safe_name(credits[0] if credits else "") or "Unknown Artist"
        name = self.by_lower.get(unicodedata.normalize("NFC", safe).lower()) or self.by_folded.get(folded(safe).lower()) or safe
        return os.path.join(self.base, name)


def _slash_in_name(artist, name_artist, folders):
    """True when "/" belongs to the artist's name: the filename or an existing folder spells it without the "/"."""
    if "/" not in artist:
        return False
    joined = folded(safe_name(artist)).lower()
    candidates = [folded(name_artist).lower()] + [folded(n).lower() for n in folders.by_lower.values()]
    return any(c == joined or c.startswith(joined + ",") or c.startswith(joined + " &") for c in candidates)


def propose(track, root, folders, new_title=None, new_artist=None):
    """What `track` (a cache row: path, title, artist, title_tag, readable) should become.

    `new_title` / `new_artist` override the computed values (the user edited them)."""
    path = track["path"]
    stem, ext = os.path.splitext(os.path.basename(path))
    title = track["title"] if track["title_tag"] else ""
    artist = track["artist"] or ""
    if artist.lower() in PLACEHOLDER_ARTISTS:
        artist = ""
    notes = []

    # What the filename says, by the library's "Title - Artist" convention.
    name_title, name_artist = (stem.rsplit(" - ", 1) + [""])[:2] if " - " in stem else (stem, "")

    if new_artist is None:
        new_artist = clean_artist(artist or name_artist, split_slash=not _slash_in_name(artist, name_artist, folders))
        if not artist and name_artist:
            notes.append("artist taken from the filename")
    if new_title is None:
        new_title = clean_title(title or name_title, new_artist)
        if not title:
            notes.append("title taken from the filename")

    ok = bool(track["readable"])
    if not ok:
        notes.append("file can't be read, so its tags can't be written")
        new_title, new_artist = title, artist

    new_path = path
    if new_artist and new_title:
        folder = os.path.dirname(path)
        under_artists = os.path.normcase(folder).startswith(os.path.normcase(folders.base) + os.sep)
        if under_artists:
            folder = folders.folder_for(new_artist)
        new_path = os.path.join(folder, file_stem(new_title, new_artist) + ext.lower())
        # Never drop credits the filename has and the tags don't.
        if name_artist:
            had = {folded(safe_name(a)).lower() for a in artist_list(name_artist)}
            has = {folded(safe_name(a)).lower() for a in artist_list(new_artist)}
            lost = had - has
            if lost:
                notes.append("the filename and the tags disagree about the artist: check it")
                ok = False
    elif not new_artist:
        notes.append("no artist: type one to rename the file")

    return Proposal(path, title, artist, new_title, new_artist, new_path, notes, ok)


def propose_all(tracks, root):
    folders = ArtistFolders(root)
    proposals = [p for p in (propose(t, root, folders) for t in tracks) if p.changed or (p.notes and not p.new_artist)]
    mark_conflicts(proposals)
    return proposals


def mark_conflicts(proposals, edited=None):
    """Untick renames onto an existing file or onto another proposal's target.

    Of two proposals with the same target, the `edited` one is the one marked."""
    targets = {}
    for p in sorted(proposals, key=lambda q: q is edited):
        p.notes = [n for n in p.notes if not n.startswith("a file named")]
        p.conflict = False
        if not p.path_changed:
            continue
        key = unicodedata.normalize("NFC", p.new_path).lower()  # FAT: names are case-insensitive
        if (os.path.exists(p.new_path) and not same_path(p.new_path, p.path)) or key in targets:
            p.notes.append("a file named like this already exists (duplicate?)")
            p.conflict = True
        targets[key] = p
