"""The app's colours, type scale and stylesheet.

One principle runs through all of it: **colour means harmonic key, and nothing else.**
The Camelot wheel in `key_color` is the only saturated colour the interface is allowed to
spend, because it is the only colour a DJ actually reads. Everything else lives on the neutral
ink ramp below, with amber reserved strictly for "this control is active", and the status
colours kept desaturated so a warning can never out-shout a key pill.

That is also why the track table is so quiet — no gridlines, no alternating rows, no tinted
cells. It clears the field for the one loud thing on screen.
"""
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QStyleFactory

# --- the ink ramp ---------------------------------------------------------------------------
INK_0 = "#0e1013"   # window
INK_1 = "#15181d"   # panes, table base
INK_2 = "#1c2027"   # raised: inputs, header, menus
INK_3 = "#242931"   # hover
LINE = "#262b33"    # the one hairline
LINE_SOFT = "#1e232a"
TEXT = "#e6e9ee"
MUTED = "#79818f"
DIM = "#545c68"
ACCENT = "#e8a33d"  # amber, the one interactive colour
SEL = "#332a18"     # selection fill: amber laid into the ink
SEL_LINE = "#50401f"

# Names the rest of the app already imports. Kept so nothing below ui/ has to change.
BG, BASE, ALT, RAISED, BORDER = INK_0, INK_1, INK_1, INK_2, LINE

# --- status, deliberately desaturated -------------------------------------------------------
FILE_SOURCE = QColor("#6b7280")  # BPM/key from the file tag or detection, not rekordbox's own analysis
WARN = QColor("#c08a44")
TODO = QColor("#cf6a5c")
DONE = QColor("#5fa87c")
FAV = QColor(ACCENT)
EDIT = QColor("#5d93c9")

# --- rhythm ---------------------------------------------------------------------------------
SPACE = 8
RADIUS = 10       # containers
RADIUS_SM = 6     # controls
ROW_HEIGHT = 32

# --- type -----------------------------------------------------------------------------------
# Verified to resolve on macOS: "SF Pro Text" and "SF Mono" do not, and Qt falls back to
# Helvetica in silence, which is how a type scale quietly fails to happen.
UI_FONT = ".AppleSystemUIFont"
MONO_FONT = "Menlo"

_ROLES = {           # size, weight
    "title": (15, QFont.DemiBold),
    "body": (13, QFont.Normal),
    "label": (12, QFont.Medium),
    "caption": (11, QFont.Medium),
}


def font(role="body", mono=False, tabular=False):
    size, weight = _ROLES[role]
    f = QFont(MONO_FONT if mono else UI_FONT)
    f.setPixelSize(size)
    f.setWeight(weight)
    if tabular:
        try:
            f.setFeature(QFont.Tag("tnum"), 1)
        except (AttributeError, TypeError):
            pass  # older PySide6: the mono face already lines its digits up
    return f


def restyle(widget):
    """Re-apply the stylesheet after a dynamic property changed. Without this the change is
    a silent no-op, which is the classic way a [muted] or [primary] rule appears not to work."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)


STYLESHEET = f"""
QWidget {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {INK_0}; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[accent="true"] {{ color: {ACCENT}; }}
QLabel[caption="true"] {{ color: {MUTED}; font-size: 11px; font-weight: 500; }}

QToolBar {{ background: {INK_0}; border: none; border-bottom: 1px solid {LINE_SOFT};
    padding: 6px 8px; spacing: 2px; }}
QToolBar QToolButton {{ padding: 6px 10px; border-radius: {RADIUS_SM}px; color: {TEXT}; }}
QToolBar QToolButton:hover {{ background: {INK_2}; }}
QToolBar QToolButton:pressed {{ background: {INK_3}; }}
QToolBar QToolButton:disabled {{ color: {DIM}; }}
QToolBar QToolButton[primary="true"] {{ background: {SEL}; color: {ACCENT}; font-weight: 600; }}
QToolBar QToolButton[primary="true"]:hover {{ background: {SEL_LINE}; }}
QToolBar QToolButton[primary="true"]:disabled {{ background: transparent; color: {DIM}; }}

QMenuBar {{ background: {INK_0}; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: {RADIUS_SM}px; }}
QMenuBar::item:selected {{ background: {INK_2}; }}
QMenu {{ background: {INK_2}; border: 1px solid {LINE}; border-radius: {RADIUS}px; padding: 5px; }}
QMenu::item {{ padding: 6px 14px 6px 10px; border-radius: {RADIUS_SM}px; }}
QMenu::item:selected {{ background: {SEL}; color: {TEXT}; }}
QMenu::item:disabled {{ color: {DIM}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 5px 8px; }}
QMenu::icon {{ padding-left: 8px; }}

QSplitter::handle {{ background: {INK_0}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}

QTableView, QTreeView, QListWidget, QListView {{ background: {INK_1}; border: none;
    border-radius: {RADIUS}px; outline: none; selection-background-color: {SEL};
    selection-color: {TEXT}; }}
QTableView {{ alternate-background-color: {INK_1}; }}
QTreeView::item, QListWidget::item {{ padding: 5px 6px; border-radius: {RADIUS_SM}px; }}
QTreeView::item:hover, QListWidget::item:hover {{ background: {INK_2}; }}
QTreeView::item:selected, QListWidget::item:selected {{ background: {SEL}; color: {TEXT}; }}
QTreeView::branch {{ background: transparent; }}

QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {INK_1}; color: {MUTED}; border: none;
    border-bottom: 1px solid {LINE}; padding: 7px 8px; font-size: 11px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {TEXT}; }}
QHeaderView::up-arrow, QHeaderView::down-arrow {{ width: 8px; height: 8px;
    margin-right: 4px; subcontrol-position: center right; }}
QTableCornerButton::section {{ background: {INK_1}; border: none; }}

QGroupBox {{ background: transparent; border: none; margin-top: 0; padding: 0; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {INK_2};
    border: 1px solid transparent; border-radius: {RADIUS_SM}px; padding: 5px 8px;
    selection-background-color: {SEL}; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ background: {INK_3}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {ACCENT}; background: {INK_2}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {INK_2}; border: 1px solid {LINE};
    border-radius: {RADIUS_SM}px; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 14px; border: none;
    background: transparent; }}

QPushButton {{ background: transparent; border: 1px solid {LINE};
    border-radius: {RADIUS_SM}px; padding: 5px 12px; color: {TEXT}; }}
QPushButton:hover {{ background: {INK_2}; border-color: {INK_3}; }}
QPushButton:pressed {{ background: {INK_3}; }}
QPushButton:disabled {{ color: {DIM}; border-color: {LINE_SOFT}; }}
QPushButton:checked {{ background: {SEL}; border-color: {SEL_LINE}; color: {ACCENT}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT};
    color: #17130a; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: #f0b257; border-color: #f0b257; }}
QPushButton[primary="true"]:disabled {{ background: {INK_2}; border-color: {LINE};
    color: {DIM}; }}
QPushButton[quiet="true"] {{ border-color: transparent; color: {MUTED}; }}
QPushButton[quiet="true"]:hover {{ background: {INK_2}; color: {TEXT}; }}

QToolButton {{ background: transparent; border: none; border-radius: {RADIUS_SM}px;
    padding: 4px; }}
QToolButton:hover {{ background: {INK_2}; }}
QToolButton:pressed {{ background: {INK_3}; }}
QToolButton:checked {{ background: {SEL}; }}

QProgressBar {{ background: {INK_2}; border: none; border-radius: 4px; text-align: center;
    color: {MUTED}; font-size: 11px; font-weight: 500; min-height: 16px; }}
QProgressBar::chunk {{ background: {DONE.name()}; border-radius: 4px; }}

QStatusBar {{ background: {INK_0}; color: {MUTED}; border-top: 1px solid {LINE_SOFT};
    padding: 2px {SPACE}px; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {MUTED}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox:focus {{ background: {SEL}; border-radius: {RADIUS_SM}px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {DIM};
    border-radius: 4px; background: {INK_2}; }}
QCheckBox::indicator:hover {{ border-color: {MUTED}; }}
QCheckBox::indicator:focus {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox::indicator:indeterminate {{ border-color: {TODO.name()};
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {INK_2}, stop:0.40 {INK_2},
    stop:0.41 {TODO.name()}, stop:0.59 {TODO.name()}, stop:0.60 {INK_2}, stop:1 {INK_2}); }}
QCheckBox::indicator:disabled {{ border-color: {LINE}; background: {LINE_SOFT}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: {INK_3}; border-radius: 4px; }}
QScrollBar::handle:vertical {{ min-height: 28px; margin: 2px 3px; }}
QScrollBar::handle:horizontal {{ min-width: 28px; margin: 3px 2px; }}
QScrollBar::handle:hover {{ background: #323945; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {INK_2}; color: {TEXT}; border: 1px solid {LINE};
    border-radius: {RADIUS_SM}px; padding: 5px 7px; }}

QWidget#Rule {{ background: {LINE}; }}
QCheckBox[excluded="true"] {{ color: {TODO.name()}; }}
#PlayerBar {{ background: {INK_1}; border-top: 1px solid {LINE_SOFT}; }}
#PlayerBar QToolButton#Transport {{ background: {ACCENT}; border-radius: 17px; }}
#PlayerBar QToolButton#Transport:hover {{ background: #f0b257; }}
#PlayerBar QToolButton#Transport:pressed {{ background: #d28f2e; }}
#PlayerBar QLabel#NowPlaying {{ color: {TEXT}; }}
#Banner {{ background: {SEL}; border: 1px solid {SEL_LINE}; border-radius: {RADIUS_SM}px; }}
#Banner QLabel {{ color: {TEXT}; }}

QSlider::groove:horizontal {{ height: 3px; background: {INK_3}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {INK_3}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; border: none; width: 12px; height: 12px;
    margin: -5px 0; border-radius: 6px; }}
QSlider::handle:horizontal:hover {{ background: #ffffff; }}
#PlayerBar QSlider#Volume::sub-page:horizontal {{ background: {MUTED}; }}
#PlayerBar QSlider#Volume::handle:horizontal {{ background: {MUTED}; width: 9px; height: 9px;
    margin: -3px 0; border-radius: 5px; }}
#PlayerBar QSlider#Volume::handle:horizontal:hover {{ background: {TEXT}; }}
"""


def apply(app):
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setFont(font("body"))
    from PySide6.QtGui import QPalette
    p = QPalette()
    roles = {
        QPalette.Window: INK_0, QPalette.WindowText: TEXT, QPalette.Base: INK_1,
        QPalette.AlternateBase: INK_1, QPalette.ToolTipBase: INK_2, QPalette.ToolTipText: TEXT,
        QPalette.Text: TEXT, QPalette.Button: INK_2, QPalette.ButtonText: TEXT,
        QPalette.BrightText: "#ffffff", QPalette.Highlight: SEL, QPalette.HighlightedText: TEXT,
        QPalette.Link: ACCENT, QPalette.PlaceholderText: MUTED, QPalette.Mid: LINE,
    }
    for role, color in roles.items():
        p.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor(DIM))
    app.setPalette(p)
    app.setStyleSheet(STYLESHEET)


def key_color(camelot, role="fill"):
    """A hue per Camelot number, so harmonic neighbours sit next to each other on the colour
    wheel. Minor (A) keys are a little darker than major (B).

    role "fill" is the key pill's background, "text" the label that sits on it, and "wash" a
    translucent version for anything that tints a whole cell.
    """
    if not camelot:
        return None
    number, letter = int(camelot[:-1]), camelot[-1]
    hue = int((number - 1) * 30) % 360
    minor = letter == "A"
    if role == "text":
        return QColor("#14161b")
    if role == "wash":
        color = QColor.fromHsv(hue, 150, 95 if minor else 125)
        color.setAlpha(150)
        return color
    return QColor.fromHsv(hue, 150, 168 if minor else 205)
