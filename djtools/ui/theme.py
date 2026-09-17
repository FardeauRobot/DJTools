"""Dark theme and the app's shared colours. Every coloured cue reads from here so it stays legible on the theme."""
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QStyleFactory

BG = "#16181d"
BASE = "#1c1f26"
ALT = "#21252d"
RAISED = "#262a33"
BORDER = "#323743"
TEXT = "#e3e6eb"
MUTED = "#8b93a1"
ACCENT = "#e8a33d"  # warm amber, the DJTools highlight

FILE_SOURCE = QColor("#7c8492")  # BPM/key read from the file tag, not rekordbox's own analysis
WARN = QColor("#e39b4a")
TODO = QColor("#ef6a5a")
DONE = QColor("#5cc28a")
FAV = QColor("#f2c14e")
EDIT = QColor("#6aaef0")

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{ background: {BG}; border: none; border-bottom: 1px solid {BORDER}; padding: 4px; spacing: 4px; }}
QToolBar QToolButton {{ padding: 4px 10px; border-radius: 5px; }}
QToolBar QToolButton:hover {{ background: {RAISED}; }}
QToolBar QToolButton:disabled {{ color: {MUTED}; }}
QMenuBar {{ background: {BG}; }}
QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QSplitter::handle:vertical {{ height: 4px; }}
QTableView, QTreeView, QListWidget {{ background: {BASE}; alternate-background-color: {ALT};
    border: 1px solid {BORDER}; border-radius: 6px; gridline-color: {BORDER}; }}
QTableView::item:selected, QTreeView::item:selected, QListWidget::item:selected {{
    background: #3a3222; color: {TEXT}; }}
QHeaderView::section {{ background: {RAISED}; color: {MUTED}; border: none; border-right: 1px solid {BORDER};
    padding: 4px 6px; font-weight: 600; }}
QGroupBox {{ background: {BASE}; border: 1px solid {BORDER}; border-radius: 6px; margin-top: 2px; padding: 4px; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {BASE}; border: 1px solid {BORDER};
    border-radius: 5px; padding: 3px 6px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QPushButton {{ background: {RAISED}; border: 1px solid {BORDER}; border-radius: 5px; padding: 4px 10px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton:checked {{ background: #4a3a1c; border-color: {ACCENT}; color: {FAV.name()}; }}
QProgressBar {{ background: {BASE}; border: 1px solid {BORDER}; border-radius: 5px; text-align: center; }}
QProgressBar::chunk {{ background: #3f7f5a; border-radius: 4px; }}
QStatusBar {{ background: {BG}; color: {MUTED}; }}
QScrollArea {{ border: none; }}
QCheckBox::indicator {{ width: 13px; height: 13px; border: 1px solid {MUTED}; border-radius: 3px; background: {BASE}; }}
QCheckBox::indicator:hover {{ border-color: {TEXT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:indeterminate {{ border-color: {ACCENT};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {BASE}, stop:0.35 {BASE}, stop:0.36 {ACCENT},
    stop:0.64 {ACCENT}, stop:0.65 {BASE}, stop:1 {BASE}); }}
QCheckBox::indicator:disabled {{ border-color: {BORDER}; }}
#PlayerBar {{ background: {RAISED}; border-top: 1px solid {BORDER}; }}
#PlayerBar QLabel#NowPlaying {{ font-weight: 600; }}
#Banner {{ background: #3a3222; border: 1px solid {ACCENT}; border-radius: 5px; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; margin: -5px 0; border-radius: 6px; }}
"""


def apply(app):
    app.setStyle(QStyleFactory.create("Fusion"))
    p = QPalette()
    roles = {
        QPalette.Window: BG, QPalette.WindowText: TEXT, QPalette.Base: BASE, QPalette.AlternateBase: ALT,
        QPalette.ToolTipBase: RAISED, QPalette.ToolTipText: TEXT, QPalette.Text: TEXT, QPalette.Button: RAISED,
        QPalette.ButtonText: TEXT, QPalette.BrightText: "#ffffff", QPalette.Highlight: "#6b5020",
        QPalette.HighlightedText: TEXT, QPalette.Link: ACCENT, QPalette.PlaceholderText: MUTED,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(MUTED))
    app.setPalette(p)
    app.setStyleSheet(STYLESHEET)


def key_color(camelot):
    """A hue per Camelot number, so harmonic neighbours sit next to each other on the colour wheel.
    Minor (A) keys are a little darker than major (B)."""
    if not camelot:
        return None
    number, letter = int(camelot[:-1]), camelot[-1]
    color = QColor.fromHsv(int((number - 1) * 30) % 360, 150, 95 if letter == "A" else 125)
    color.setAlpha(150)
    return color
