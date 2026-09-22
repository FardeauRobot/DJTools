"""Musical key normalisation: everything is stored and shown as Camelot (8A) plus notation (Am)."""
import re

_CAMELOT = {
    "1A": "Abm", "2A": "Ebm", "3A": "Bbm", "4A": "Fm", "5A": "Cm", "6A": "Gm",
    "7A": "Dm", "8A": "Am", "9A": "Em", "10A": "Bm", "11A": "F#m", "12A": "C#m",
    "1B": "B", "2B": "F#", "3B": "Db", "4B": "Ab", "5B": "Eb", "6B": "Bb",
    "7B": "F", "8B": "C", "9B": "G", "10B": "D", "11B": "A", "12B": "E",
}
_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _pitch(name: str):
    m = re.fullmatch(r"([A-Ga-g])([#b♯♭]?)", name)
    if not m:
        return None
    acc = {"#": 1, "♯": 1, "b": -1, "♭": -1}.get(m.group(2), 0)
    return (_SEMITONE[m.group(1).upper()] + acc) % 12


_BY_PITCH = {}
for _cam, _note in _CAMELOT.items():
    _minor = _note.endswith("m")
    _BY_PITCH[(_pitch(_note[:-1] if _minor else _note), _minor)] = _cam


def to_camelot(raw):
    """Return a Camelot code ("8A") for any common key spelling, or None."""
    if not raw:
        return None
    s = str(raw).strip()
    m = re.fullmatch(r"0?(1[0-2]|[1-9])\s*([ABab])", s)
    if m:
        return m.group(1) + m.group(2).upper()
    m = re.fullmatch(r"([A-Ga-g][#b♯♭]?)\s*(m|min|minor|maj|major)?", s)
    if not m:
        return None
    pitch = _pitch(m.group(1))
    minor = (m.group(2) or "").lower() in ("m", "min", "minor")
    return _BY_PITCH.get((pitch, minor))


def note(camelot):
    """The key written as a note ("Am"), for showing beside the Camelot code."""
    return _CAMELOT.get(camelot, "") if camelot else ""


def display(camelot):
    if not camelot:
        return ""
    return f"{camelot} · {_CAMELOT[camelot]}"


def sort_key(camelot):
    """Order 1A, 1B, 2A, 2B … so harmonic neighbours sit together, blanks last.

    A plain int, not a tuple: the table hands this to Qt as a sort role, and Qt cannot compare
    two Python tuples wrapped in a QVariant — it silently leaves the column unsorted.
    """
    if not camelot:
        return 999
    return int(camelot[:-1]) * 2 + (camelot[-1] == "B")


def neighbours(camelot):
    """Keys that mix harmonically with `camelot`: itself, one step either way on the wheel, and its relative
    major/minor. Empty for an unknown key."""
    if camelot not in _CAMELOT:
        return set()
    number, letter = int(camelot[:-1]), camelot[-1]
    other = "B" if letter == "A" else "A"
    return {camelot, f"{number % 12 + 1}{letter}", f"{(number - 2) % 12 + 1}{letter}", f"{number}{other}"}


ALL = [f"{n}{l}" for n in range(1, 13) for l in "AB"]
