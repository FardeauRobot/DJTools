"""The app's icon set: inline SVG, recoloured on demand, cached.

Every icon is drawn on the same 24x24 grid with the same 1.6 stroke, round caps and round
joins, and each one is sized to fill roughly the same optical box, so they read as one family
rather than sixteen unrelated drawings. Nothing is loaded from disk and QtSvg ships with
PySide6, so this adds no file and no dependency.

The shapes that have to stay symmetric (the stars, the refresh arrow's head) are computed
rather than typed, so they can't drift apart when one of them is tweaked.

`PATHS` entries are (d, mode) with mode "s" for stroked and "f" for filled.
"""
import math

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

STROKE = 1.6


def _star(points, outer, inner, cx=12.0, cy=12.0, turn=-90.0):
    """A symmetric star. `points` 5 gives the favourite star, 4 the sparkle."""
    step = 180.0 / points
    xy = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        a = math.radians(turn + step * i)
        xy.append(f"{cx + r * math.cos(a):.2f} {cy + r * math.sin(a):.2f}")
    return "M" + "L".join(xy) + "Z"


def _arc(cx, cy, r, start, end):
    """A clockwise arc from `start` to `end` degrees, screen angles (0 = right, 90 = down)."""
    x0, y0 = cx + r * math.cos(math.radians(start)), cy + r * math.sin(math.radians(start))
    x1, y1 = cx + r * math.cos(math.radians(end)), cy + r * math.sin(math.radians(end))
    large = 1 if (end - start) % 360 > 180 else 0
    return f"M{x0:.2f} {y0:.2f}A{r} {r} 0 {large} 1 {x1:.2f} {y1:.2f}"


def _head(cx, cy, r, at, length=3.0, width=2.4, reach=2.2):
    """A chevron arrowhead on a clockwise arc, pointing along the tangent at `at` degrees."""
    a = math.radians(at)
    px, py = cx + r * math.cos(a), cy + r * math.sin(a)
    dx, dy = -math.sin(a), math.cos(a)  # clockwise tangent
    nx, ny = -dy, dx
    tx, ty = px + dx * reach, py + dy * reach
    bx, by = tx - dx * length, ty - dy * length
    return (f"M{bx + nx * width:.2f} {by + ny * width:.2f}"
            f"L{tx:.2f} {ty:.2f}L{bx - nx * width:.2f} {by - ny * width:.2f}")


_STAR = _star(5, 7.4, 3.1, cy=12.2)

PATHS = {
    "folder": [("M3.5 8a2.3 2.3 0 0 1 2.3-2.3h3.1a1.55 1.55 0 0 1 1.1.45l1.35 1.35h6.85"
                "A2.3 2.3 0 0 1 20.5 9.8v7.6a2.3 2.3 0 0 1-2.3 2.3H5.8a2.3 2.3 0 0 1-2.3-2.3z", "s")],
    "refresh": [(_arc(12, 12, 7.4, -58, 252), "s"), (_head(12, 12, 7.4, 252), "s")],
    "sparkle": [(_star(4, 6.8, 1.9, cx=10.3, cy=13.4), "s"),
                (_star(4, 3.7, 1.05, cx=17.6, cy=7.2), "s")],
    "upload": [("M12 16.6V5.1", "s"),
               ("M8.1 9L12 5.1 15.9 9", "s"),
               ("M5.1 15.6v2.2a1.9 1.9 0 0 0 1.9 1.9h10a1.9 1.9 0 0 0 1.9-1.9v-2.2", "s")],
    "star": [(_STAR, "s")],
    "star-filled": [(_STAR, "f")],
    "link": [("M10.3 13.7a3.5 3.5 0 0 0 5.28.38l2.33-2.33a3.5 3.5 0 0 0-4.95-4.95l-1.34 1.33", "s"),
             ("M13.7 10.3a3.5 3.5 0 0 0-5.28-.38l-2.33 2.33a3.5 3.5 0 0 0 4.95 4.95l1.33-1.33", "s")],
    "play": [("M9.4 6.9l8.2 5.1-8.2 5.1z", "f")],
    "pause": [("M9.1 6.9h2.2v10.2H9.1z", "f"), ("M12.7 6.9h2.2v10.2h-2.2z", "f")],
    "volume": [("M11.3 6.5L7.7 9.5H5.1v5h2.6l3.6 3z", "s"),
               ("M14.8 9.7a3.3 3.3 0 0 1 0 4.6", "s"),
               ("M17.3 7.2a6.8 6.8 0 0 1 0 9.6", "s")],
    "pencil": [("M16.2 5.1l2.7 2.7-9.5 9.5-3.6.9.9-3.6z", "s"), ("M14.2 7.1l2.7 2.7", "s")],
    "plus": [("M12 6.9v10.2", "s"), ("M6.9 12h10.2", "s")],
    "check": [("M6.4 12.3l3.9 3.9 7.3-8.6", "s")],
    "close": [("M7.3 7.3l9.4 9.4", "s"), ("M16.7 7.3L7.3 16.7", "s")],
    "search": [("M11 5.2a5.9 5.9 0 1 0 0 11.8 5.9 5.9 0 0 0 0-11.8z", "s"),
               ("M15.4 15.4l3.4 3.4", "s")],
    "alert": [("M12 5.6l7 12.2H5z", "s"), ("M12 10.2v3.4", "s"), ("M12 15.9v.05", "s")],
}

_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="24" height="24">{body}</svg>'

_cache = {}


def _svg(name, color):
    parts = []
    for d, mode in PATHS[name]:
        if mode == "f":
            parts.append(f'<path d="{d}" fill="{color}"/>')
        else:
            parts.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{STROKE}"'
                         ' stroke-linecap="round" stroke-linejoin="round"/>')
    return _SVG.format(body="".join(parts))


def pixmap(name, color, size=18, ratio=None):
    """A rendered icon. `ratio` defaults to the screen's, so icons stay crisp on retina."""
    if ratio is None:
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        ratio = screen.devicePixelRatio() if screen else 1.0
    key = (name, str(color), size, ratio)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    px = QPixmap(round(size * ratio), round(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, color).encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    _cache[key] = px
    return px


def icon(name, color=None, size=18):
    if color is None:
        from . import theme
        color = theme.TEXT
    return QIcon(pixmap(name, color, size))
