"""Filter strip above the track table: BPM range, key (optionally with its harmonic neighbours), untagged."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QSpinBox, QToolButton, QWidget

from .. import keys
from . import icons, theme


class FilterBar(QWidget):
    changed = Signal(object)  # {"bpm_min", "bpm_max", "keys", "untagged"}
    clear_all = Signal()  # "Clear filters": the window also resets search, favorites, tag filters

    def __init__(self, parent=None):
        super().__init__(parent)
        self._quiet = False

        self.bpm_min, self.bpm_max = QSpinBox(), QSpinBox()
        for box, tip in ((self.bpm_min, "Lowest BPM"), (self.bpm_max, "Highest BPM")):
            box.setRange(0, 300)
            box.setSpecialValueText("–")
            box.setToolTip(f"{tip}. Half and double tempo match too (70 counts as 140). – means no limit.")
            box.setFixedWidth(64)
            box.valueChanged.connect(self._user_changed)

        self.key = QComboBox()
        self.key.addItem("Any key", None)
        for camelot in keys.ALL:
            self.key.addItem(keys.display(camelot), camelot)
        self.key.currentIndexChanged.connect(self._user_changed)
        self.compatible = QCheckBox("+ compatible")
        self.compatible.setToolTip("Also show keys that mix with it: ±1 on the Camelot wheel and the relative major/minor")
        self.compatible.toggled.connect(self._user_changed)

        self.untagged = QCheckBox("Untagged")
        self.untagged.setToolTip("Only tracks with no My Tag yet (Favorite doesn't count)")
        self.untagged.toggled.connect(self._user_changed)

        self.context = QLabel()
        self.context.setFont(theme.font("caption"))
        self.context.setProperty("accent", True)
        self.context_clear = QToolButton(icon=icons.icon("close", theme.MUTED, 13))
        self.context_clear.setToolTip("Stop matching this track")
        self.context_clear.clicked.connect(self.clear)
        self.context.hide()
        self.context_clear.hide()

        clear = QPushButton("Clear filters")
        clear.clicked.connect(self.clear_all.emit)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("BPM"))
        layout.addWidget(self.bpm_min)
        layout.addWidget(QLabel("–"))
        layout.addWidget(self.bpm_max)
        layout.addSpacing(10)
        layout.addWidget(self.key)
        layout.addWidget(self.compatible)
        layout.addSpacing(10)
        layout.addWidget(self.untagged)
        layout.addSpacing(10)
        layout.addWidget(self.context)
        layout.addWidget(self.context_clear)
        layout.addStretch(1)
        layout.addWidget(clear)

    def state(self):
        camelot = self.key.currentData()
        if camelot is None:
            key_set = None
        else:
            key_set = keys.neighbours(camelot) if self.compatible.isChecked() else {camelot}
        return {
            "bpm_min": self.bpm_min.value() or None,
            "bpm_max": self.bpm_max.value() or None,
            "keys": key_set,
            "untagged": self.untagged.isChecked(),
        }

    def match_track(self, label, key, bpm, percent):
        """Fill the bar with what mixes into a track: its compatible keys and its BPM ± percent."""
        self._quiet = True
        index = self.key.findData(key) if key else 0
        self.key.setCurrentIndex(max(0, index))
        self.compatible.setChecked(bool(key))
        if bpm:
            self.bpm_min.setValue(int(bpm * (1 - percent / 100)))
            self.bpm_max.setValue(int(bpm * (1 + percent / 100) + 0.999))
        else:
            self.bpm_min.setValue(0)
            self.bpm_max.setValue(0)
        self._quiet = False
        self.context.setText(f"Mixes with: <b>{label}</b>")
        self.context.show()
        self.context_clear.show()
        self.changed.emit(self.state())

    def clear(self):
        self._quiet = True
        self.bpm_min.setValue(0)
        self.bpm_max.setValue(0)
        self.key.setCurrentIndex(0)
        self.compatible.setChecked(False)
        self.untagged.setChecked(False)
        self._quiet = False
        self._hide_context()
        self.changed.emit(self.state())

    def _hide_context(self):
        self.context.hide()
        self.context_clear.hide()

    def _user_changed(self, *_):
        if self._quiet:
            return
        self._hide_context()
        self.changed.emit(self.state())
