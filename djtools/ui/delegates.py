"""How a row of the track table is drawn.

`RowDelegate` is the **base class**, not a fifth delegate sitting alongside the others.
Qt's `setItemDelegateForColumn` replaces the view-wide delegate for that column rather than
layering on top of it, so a standalone background delegate would leave gaps in the selection
and hover fill at exactly the four columns this module redesigns. So the base owns the
background pass and every content delegate inherits it.

None of these ever call `QStyledItemDelegate.paint` — they draw the cell themselves — which is
also why `TrackModel`'s SORT_ROLE branches must stay untouched: sorting and searching don't go
through DisplayRole, so repainting a column can't disturb them.
"""
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from . import icons, theme
from .. import keys
from .track_model import (
    FAV, FAV_ROLE, KEY_ROLE, LINKS, LINKS_ROLE, PLAYLISTS, PLAYLISTS_ROLE, RB, RB_ROLE, TAGS_ROLE,
)

PAD = 8
RADIUS = 7


class RowDelegate(QStyledItemDelegate):
    """Row hover and rounded selection, plus the ordinary text cells."""

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        self.background(painter, option, index)
        self.content(painter, option, index)
        painter.restore()

    # --- background ------------------------------------------------------------------------

    def _edges(self, view, column):
        """Is this the leftmost / rightmost *visible* column? Columns are hideable and
        movable, so this has to go through the header rather than a fixed index."""
        header = getattr(view, "horizontalHeader", None)
        if header is None:
            return True, True
        header = header()
        visible = [c for c in range(header.count()) if not header.isSectionHidden(c)]
        if not visible:
            return True, True
        order = sorted(visible, key=header.visualIndex)
        return column == order[0], column == order[-1]

    def background(self, painter, option, index):
        view = option.widget
        selected = bool(option.state & QStyle.State_Selected)
        hovered = view is not None and getattr(view, "hover_row", -1) == index.row()
        if not selected and not hovered:
            return
        first, last = self._edges(view, index.column())
        r = QRectF(option.rect).adjusted(0, 1, 0, -1)
        # Round only the row's outer corners: overshoot the inner sides past the clip so their
        # rounding is never drawn.
        r.setLeft(r.left() + 3 if first else r.left() - RADIUS * 2)
        r.setRight(r.right() - 3 if last else r.right() + RADIUS * 2)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.SEL) if selected else QColor(theme.INK_2))
        painter.drawRoundedRect(r, RADIUS, RADIUS)
        painter.restore()

    # --- content ---------------------------------------------------------------------------

    def content(self, painter, option, index):
        text = index.data(Qt.DisplayRole)
        if not text:
            return
        color = index.data(Qt.ForegroundRole) or QColor(theme.TEXT)
        font = index.data(Qt.FontRole) or option.font
        align = index.data(Qt.TextAlignmentRole)
        align = Qt.AlignVCenter | (Qt.AlignmentFlag(align) if align else Qt.AlignLeft)
        self.draw_text(painter, option.rect.adjusted(PAD, 0, -PAD, 0), str(text), color, font, align)

    @staticmethod
    def draw_text(painter, rect, text, color, font, align):
        painter.setFont(font)
        painter.setPen(QPen(QColor(color)))
        painter.drawText(rect, int(align), QFontMetrics(font).elidedText(text, Qt.ElideRight, rect.width()))


class KeyPillDelegate(RowDelegate):
    """The one place the app spends saturated colour: a filled pill in the key's Camelot hue,
    with the note name beside it for anyone who doesn't read Camelot."""

    def content(self, painter, option, index):
        camelot = index.data(KEY_ROLE)
        if not camelot:
            return
        fill = theme.key_color(camelot, "fill")
        font = theme.font("caption")
        font.setWeight(QFont.Bold)
        metrics = QFontMetrics(font)
        w = metrics.horizontalAdvance(camelot) + 14
        h = 19
        rect = option.rect
        pill = QRectF(rect.left() + PAD, rect.center().y() - h / 2 + 1, w, h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(pill, h / 2, h / 2)
        self.draw_text(painter, pill.toRect(), camelot, theme.key_color(camelot, "text"), font,
                       Qt.AlignCenter)
        note = keys.note(camelot)
        if note:
            rest = rect.adjusted(int(pill.width()) + PAD + 6, 0, -PAD, 0)
            if rest.width() > 8:
                self.draw_text(painter, rest, note, QColor(theme.MUTED), theme.font("caption"),
                               Qt.AlignVCenter | Qt.AlignLeft)


class TagChipDelegate(RowDelegate):
    """Tags as chips rather than a comma-joined run of text, so the eye can count them."""

    MAX = 3
    MIN_CHIP = 26  # narrower than this and a chip is a box with no readable word in it

    def content(self, painter, option, index):
        names = index.data(TAGS_ROLE) or []
        if not names:
            return
        font = theme.font("caption")
        metrics = QFontMetrics(font)
        rect = option.rect
        x, limit = rect.left() + PAD, rect.right() - PAD
        shown = 0
        for i, name in enumerate(names[:self.MAX]):
            # Whatever this chip does not use has to still hold the "+n" for the ones left over,
            # or a full column silently shows one chip and hides the other four.
            rest = len(names) - (i + 1)
            tail = metrics.horizontalAdvance(f"+{rest}") + 6 if rest else 0
            room = limit - tail - x
            if room < self.MIN_CHIP:
                break
            width = min(metrics.horizontalAdvance(name) + 14, room)
            chip = QRectF(x, rect.center().y() - 8.5, width, 17)
            painter.setPen(QPen(QColor(theme.LINE), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(chip.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
            inner = chip.adjusted(6, 0, -6, 0).toRect()
            self.draw_text(painter, inner, metrics.elidedText(name, Qt.ElideRight, inner.width()),
                           QColor(theme.MUTED), font, Qt.AlignCenter)
            x += width + 4
            shown += 1
        left = len(names) - shown
        if left > 0:
            self.draw_text(painter, QRect(int(x), rect.top(), int(limit - x), rect.height()),
                           f"+{left}", QColor(theme.DIM), font, Qt.AlignVCenter | Qt.AlignLeft)


class MarkDelegate(RowDelegate):
    """The columns that are a mark rather than a word: the favourite star, the rekordbox dot, and the
    links and playlists counts. Drawn, not typed, so they stop being colour emoji."""

    def content(self, painter, option, index):
        column = index.column()
        rect = option.rect
        if column == FAV:
            fav = bool(index.data(FAV_ROLE))
            hovered = getattr(option.widget, "hover_row", -1) == index.row()
            if fav:
                px = icons.pixmap("star-filled", theme.ACCENT, 15)
            elif hovered:
                px = icons.pixmap("star", theme.DIM, 15)
            else:
                return
            painter.drawPixmap(rect.center().x() - 7, rect.center().y() - 7, px)
            return
        if column == RB:
            here = bool(index.data(RB_ROLE))
            painter.setBrush(Qt.NoBrush if not here else QColor(theme.DIM))
            painter.setPen(Qt.NoPen if here else QPen(QColor(theme.WARN), 1.4))
            d = 7.0
            painter.drawEllipse(QRectF(rect.center().x() - d / 2, rect.center().y() - d / 2, d, d))
            return
        if column in (LINKS, PLAYLISTS):
            links = column == LINKS
            n = index.data(LINKS_ROLE if links else PLAYLISTS_ROLE) or 0
            if not n:
                return
            font = theme.font("caption")
            label = str(n)
            w = QFontMetrics(font).horizontalAdvance(label)
            total = 14 + 3 + w
            x = rect.center().x() - total / 2
            name = "link" if links else "folder"
            painter.drawPixmap(int(x), rect.center().y() - 7, icons.pixmap(name, theme.MUTED, 14))
            self.draw_text(painter, rect.adjusted(int(x + 17) - rect.left(), 0, 0, 0), label,
                           QColor(theme.MUTED), font, Qt.AlignVCenter | Qt.AlignLeft)


def install(table):
    """Point the track table at all of this."""
    from .track_model import KEY, TAGS
    table.setItemDelegate(RowDelegate(table))
    table.setItemDelegateForColumn(KEY, KeyPillDelegate(table))
    table.setItemDelegateForColumn(TAGS, TagChipDelegate(table))
    marks = MarkDelegate(table)
    for column in (FAV, RB, LINKS, PLAYLISTS):
        table.setItemDelegateForColumn(column, marks)
