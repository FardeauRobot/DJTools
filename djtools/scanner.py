"""Walk the library folder, read file metadata, and write title/artist tags back."""
import os
import re
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.id3 import ID3, TIT2, TPE1
from mutagen.mp4 import MP4

from .config import AUDIO_EXTS
from .keys import to_camelot

_SKIP_DIRS = {"System Volume Information", "$RECYCLE.BIN", "PIONEER"}
_JUNK = re.compile(r"[‎‏‪-‮\x00-\x1f]")


@dataclass
class FileInfo:
    path: str
    title: str  # the title tag, or the filename stem when there is none
    artist: str
    key: str  # Camelot or None
    bpm: float  # or None
    duration: float  # seconds, or None
    size: int
    mtime: float
    title_tag: bool = False  # False: `title` is only the filename
    bitrate: int = None  # kbps
    has_cover: bool = False
    readable: bool = True


def iter_audio_files(root):
    """Yield (path, size, mtime) for audio files, skipping hidden and macOS "._" files."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in filenames:
            if name.startswith(".") or Path(name).suffix.lower() not in AUDIO_EXTS:
                continue
            path = os.path.join(dirpath, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            yield path, st.st_size, st.st_mtime


def _texts(values):
    """Tag values as clean, de-duplicated strings, in order."""
    out = []
    for v in values or ():
        if isinstance(v, bytes):  # MP4 freeform atoms
            v = v.decode("utf-8", "replace")
        text = _JUNK.sub("", str(v)).strip()
        if text and text not in out:
            out.append(text)
    return out


def _read_tags(audio):
    """(title, artists, key, bpm, has_cover) across ID3 (MP3/WAV/AIFF), Vorbis comments (FLAC/OGG) and MP4."""
    tags = audio.tags
    if tags is None:
        return None, [], None, None, bool(getattr(audio, "pictures", None))
    if isinstance(tags, ID3):
        get = lambda frame: _texts(tags[frame].text) if frame in tags else []  # noqa: E731
        artists = get("TPE1") or get("TPE2")
        cover = any(k.startswith("APIC") for k in tags.keys())
        return next(iter(get("TIT2")), None), artists, next(iter(get("TKEY")), None), next(iter(get("TBPM")), None), cover
    if isinstance(audio, MP4):
        get = lambda atom: _texts(tags.get(atom))  # noqa: E731
        bpm = next(iter(get("tmpo")), None)
        key = next(iter(get("----:com.apple.iTunes:initialkey")), None)
        return next(iter(get("©nam")), None), get("©ART") or get("aART"), key, bpm, "covr" in tags
    # Vorbis comments: keys are case-insensitive
    get = lambda name: _texts(tags.get(name)) if name in tags else []  # noqa: E731
    cover = bool(getattr(audio, "pictures", None)) or "metadata_block_picture" in tags
    key = next(iter(get("initialkey") or get("key")), None)
    return next(iter(get("title")), None), get("artist") or get("albumartist"), key, next(iter(get("bpm")), None), cover


def _bpm(raw):
    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return value if 20 <= value <= 400 else None


def read_file(path, size, mtime) -> FileInfo:
    title, artists, key, bpm, cover, duration, bitrate = None, [], None, None, False, None, None
    try:
        audio = mutagen.File(path)
    except Exception:  # corrupt or unsupported: list it by filename anyway
        audio = None
    if audio is not None:
        if audio.info is not None:
            duration = getattr(audio.info, "length", None)
            rate = getattr(audio.info, "bitrate", None)
            bitrate = round(rate / 1000) if rate else None
        try:
            title, artists, key, bpm, cover = _read_tags(audio)
        except Exception:
            pass
    return FileInfo(
        path=path,
        title=title or Path(path).stem,
        artist=", ".join(artists),  # multi-valued artists joined the way the library's filenames are
        key=to_camelot(key),
        bpm=_bpm(bpm),
        duration=duration,
        size=size,
        mtime=mtime,
        title_tag=bool(title),
        bitrate=bitrate,
        has_cover=cover,
        readable=audio is not None,
    )


def write_title_artist(path, title=None, artist=None):
    """Set the title and/or artist tag, leaving every other tag (cues, keys, artwork) untouched."""
    audio = mutagen.File(path)
    if audio is None:
        raise ValueError("unsupported or unreadable file")
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if isinstance(tags, ID3):
        if title is not None:
            tags.setall("TIT2", [TIT2(encoding=3, text=[title])])
        if artist is not None:
            tags.setall("TPE1", [TPE1(encoding=3, text=[artist])])
    elif isinstance(audio, MP4):
        if title is not None:
            tags["©nam"] = [title]
        if artist is not None:
            tags["©ART"] = [artist]
    else:
        if title is not None:
            tags["title"] = [title]
        if artist is not None:
            tags["artist"] = [artist]
    audio.save()
