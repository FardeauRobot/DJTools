"""The rebindable keys: one table read by the vim dispatcher, the menus and the cheat sheet, overrides kept in settings.

Two kinds of binding:
- track-list keys: one typed character, dispatched by VimKeys (counts like 5j apply to them);
- menu shortcuts: a QKeySequence string on a QAction (Qt's "Ctrl" is ⌘ on macOS).

Anything else (counts, gg, dd, Esc, ?, Ctrl-d/u/h/l/r, Space, Enter, arrows, F2, the folder and tag panel keys) is
fixed: those are dispatcher grammar or Qt-level shortcuts that would silently win over a rebinding.
"""
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

TRACK_KEYS = {
    "down": ("move down", "j"),
    "up": ("move up", "k"),
    "extend_down": ("extend the selection down", "J"),
    "extend_up": ("extend the selection up", "K"),
    "last": ("last track", "G"),
    "visual": ("visual select", "v"),
    "visual_line": ("visual select", "V"),
    "search": ("search", "/"),
    "tags": ("jump to the tag panel", "t"),
    "play": ("play", "o"),
    "seek_back": ("seek back 5 s", "h"),
    "seek_forward": ("seek forward 5 s", "l"),
    "seek_back_long": ("seek back 30 s", "H"),
    "seek_forward_long": ("seek forward 30 s", "L"),
    "volume_down": ("volume down", "-"),
    "volume_up": ("volume up", "="),
    "compatible": ("tracks that mix with this one", "c"),
    "link": ("link: goes well with…", "w"),
    "show_linked": ("show the linked tracks", "W"),
    "favorite": ("favorite", "f"),
    "copy_tags": ("copy tags", "y"),
    "paste_tags": ("paste tags", "p"),
    "undo": ("undo a tag or link change", "u"),
    "edit_title": ("rename the title", "i"),
    "edit_artist": ("rename the artist", "a"),
    "trash": ("move to Trash", "x"),
    "move": ("move to folder…", "m"),
}

MENU_KEYS = {
    "m_search": ("search", "Ctrl+F"),
    "m_compatible": ("tracks that mix with this one", "Ctrl+K"),
    "m_clear_filters": ("clear filters", "Ctrl+Shift+K"),
    "m_link": ("link: goes well with…", "Ctrl+L"),
    "m_show_linked": ("show the linked tracks", "Ctrl+Shift+L"),
    "m_all_links": ("all links", "Ctrl+Alt+L"),
    "m_copy_tags": ("copy tags", "Ctrl+C"),
    "m_paste_tags": ("paste tags", "Ctrl+V"),
    "m_undo": ("undo", "Ctrl+Z"),
    "m_redo": ("redo", "Ctrl+Shift+Z"),
    "m_rescan": ("rescan", "Ctrl+R"),
    "m_import": ("import tracks", "Ctrl+I"),
    "m_new_folder": ("new folder", "Ctrl+Shift+N"),
    "m_help": ("keyboard shortcuts", "Ctrl+/"),
}

# Typed characters the dispatcher keeps for itself: counts, the first key of gg / dd, and help.
RESERVED = set("0123456789gd?")
# The fixed physical-Control pane and paging keys (Qt calls physical Control "Meta" on macOS).
_CTRL = "Meta" if sys.platform == "darwin" else "Ctrl"
RESERVED_SEQUENCES = [f"{_CTRL}+{k}" for k in "DUHLR"]


def label(action):
    return (TRACK_KEYS.get(action) or MENU_KEYS[action])[0]


def default(action):
    return (TRACK_KEYS.get(action) or MENU_KEYS[action])[1]


def display(action, key):
    """How a binding is shown: ⌘-style for menu shortcuts, the character itself for track keys."""
    return QKeySequence(key).toString(QKeySequence.NativeText) if action in MENU_KEYS else key


class Keymap:
    def __init__(self, settings):
        self.settings = settings
        self.listeners = []  # called with no arguments after any change
        self._rebuild()

    def _rebuild(self):
        saved = self.settings.get("keys") or {}
        self.keys = {a: saved.get(a, default(a)) for a in (*TRACK_KEYS, *MENU_KEYS)}
        self._by_char = {self.keys[a]: a for a in TRACK_KEYS}

    def __getitem__(self, action):
        return self.keys[action]

    def track_action(self, text):
        return self._by_char.get(text)

    def is_custom(self, action):
        return self.keys[action] != default(action)

    def check(self, action, key):
        """Why `key` can't be bound to `action`, or None. A key already in use is fine: set() swaps the two."""
        if action in TRACK_KEYS:
            if len(key) != 1 or not key.isprintable() or key.isspace():
                return "Track-list keys are a single character."
            if key in RESERVED:
                return f"“{key}” is kept for counts, gg / dd or help."
        else:
            seq = QKeySequence(key)
            if seq.isEmpty() or seq.count() != 1:
                return "Press one key combination."
            combo = seq[0]
            if not (combo.keyboardModifiers() & ~Qt.ShiftModifier):
                return "Menu shortcuts need ⌘, ⌃ or ⌥, or they would swallow typing."
            if any(_same_sequence(key, r) for r in RESERVED_SEQUENCES):
                return f"{display(action, key)} is kept for paging and switching panes."
        return None

    def set(self, action, key):
        """Bind `key` to `action`. Returns the action that had it and now takes `action`'s old key, or None."""
        group = TRACK_KEYS if action in TRACK_KEYS else MENU_KEYS
        same = (lambda a, b: a == b) if group is TRACK_KEYS else _same_sequence
        other = next((a for a in group if a != action and same(self.keys[a], key)), None)
        if other:
            self.keys[other] = self.keys[action]
        self.keys[action] = key
        self._save()
        return other

    def reset(self, action=None):
        """Back to the default: one action (swapping with whoever holds that key now), or all of them."""
        if action:
            return self.set(action, default(action))
        self.keys = {a: default(a) for a in self.keys}
        self._save()
        return None

    def _save(self):
        self.settings.set("keys", {a: k for a, k in self.keys.items() if k != default(a)})
        self._rebuild()
        for listener in self.listeners:
            listener()


def _same_sequence(a, b):
    return QKeySequence(a).matches(QKeySequence(b)) == QKeySequence.ExactMatch
