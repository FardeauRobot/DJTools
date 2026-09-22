"""Left-pane "Library health" list: progress, one line per check, and what each one means."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QListWidget, QListWidgetItem, QProgressBar, QPushButton, QVBoxLayout, QWidget

from . import icons, theme
from .theme import DONE as DONE_COLOR
from .theme import TODO as TODO_COLOR
from .theme import WARN as WARN_COLOR

CHECK_ROLE = Qt.UserRole + 1


class HealthPanel(QWidget):
    check_selected = Signal(object)  # Check, or None when the selection is cleared
    fix_requested = Signal(object)  # Check

    def __init__(self, parent=None):
        super().__init__(parent)
        self.checks = {}
        self.current_id = None

        title = QLabel("<b>Library health</b>")
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.list = QListWidget()
        self.list.setToolTip("Click a line to show those tracks")
        self.list.itemClicked.connect(self._clicked)
        self.explain = QLabel("Click a line to see the tracks concerned and what to do about them.")
        self.explain.setWordWrap(True)
        self.explain.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.explain.setFont(theme.font("caption"))
        self.explain.setProperty("muted", True)
        self.fix_btn = QPushButton()
        self.fix_btn.hide()
        self.fix_btn.clicked.connect(lambda: self.current_id in self.checks and self.fix_requested.emit(self.checks[self.current_id]))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.SPACE, theme.SPACE, theme.SPACE, 0)
        layout.setSpacing(theme.SPACE - 2)
        layout.addWidget(title)
        layout.addWidget(self.progress)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.explain)
        layout.addWidget(self.fix_btn)

    def set_report(self, report):
        self.checks = {c.id: c for c in report.checks}
        self.progress.setRange(0, max(1, report.total))
        self.progress.setValue(report.complete)
        self.progress.setFormat(f"{report.complete} / {report.total} tracks complete")
        self.progress.setToolTip(
            "A track is complete when it has an artist, a title and a clean name, is imported and analyzed in "
            "rekordbox, and has a Genre tag." if report.rekordbox else
            "A track is complete when it has an artist, a title and a clean name, and has a Genre tag."
        )
        self.list.clear()
        # Required checks first, then the warnings that have something to show.
        ordered = [c for c in report.checks if c.required] + [c for c in report.checks if not c.required and c.count]
        for c in ordered:
            color = DONE_COLOR if not c.count else (TODO_COLOR if c.required else WARN_COLOR)
            name = "check" if not c.count else ("close" if c.required else "alert")
            item = QListWidgetItem(icons.icon(name, color.name(), 14),
                                   c.label + (f"  ({c.count})" if c.count else ""))
            item.setData(CHECK_ROLE, c.id)
            item.setForeground(color)
            if not c.required:
                font = item.font()
                font.setItalic(True)
                item.setFont(font)
            self.list.addItem(item)
            if c.id == self.current_id:
                item.setSelected(True)
                self.list.setCurrentItem(item)
        if self.current_id not in self.checks:
            self.clear_selection()
        else:
            self._show(self.checks[self.current_id])

    def clear_selection(self):
        self.current_id = None
        self.list.clearSelection()
        self.explain.setText("Click a line to see the tracks concerned and what to do about them.")
        self.fix_btn.hide()

    def _clicked(self, item):
        check = self.checks.get(item.data(CHECK_ROLE))
        if check is None:
            return
        self.current_id = check.id
        self._show(check)
        self.check_selected.emit(check)

    def _show(self, check):
        text = check.explain
        if check.id == "empty_folders" and check.paths:
            names = [p.rsplit("/", 1)[-1] for p in check.paths[:8]]
            text += "\n\n" + "\n".join(names) + ("\n…" if len(check.paths) > 8 else "")
        self.explain.setText(text)
        self.fix_btn.setText(check.fix_label)
        self.fix_btn.setVisible(bool(check.fix and check.count))
