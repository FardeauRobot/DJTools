"""Bottom transport bar: play / pause, seek, volume."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QToolButton, QVBoxLayout, QWidget

from . import icons, theme
from .track_model import fmt_time


class PlayerBar(QWidget):
    error = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.player.setAudioOutput(self.audio)
        self.audio.setVolume(0.8)
        self.path = None
        self.setObjectName("PlayerBar")
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.play_btn = QToolButton()
        self.play_btn.setObjectName("Transport")
        self.play_btn.setIcon(icons.icon("play", theme.INK_0, 16))
        self.play_btn.setFixedSize(34, 34)
        self.play_btn.setToolTip("Play / pause  (Space)")
        self.play_btn.clicked.connect(self.toggle)

        self.title = QLabel("Nothing playing")
        self.title.setObjectName("NowPlaying")
        self.title.setFont(theme.font("label"))
        self.subtitle = QLabel("Double-click a track")
        self.subtitle.setFont(theme.font("caption"))
        self.subtitle.setProperty("muted", True)
        now = QWidget()
        now.setMinimumWidth(220)
        now.setMaximumWidth(320)
        now_layout = QVBoxLayout(now)
        now_layout.setContentsMargins(0, 0, 0, 0)
        now_layout.setSpacing(0)
        now_layout.addWidget(self.title)
        now_layout.addWidget(self.subtitle)

        self.seek = QSlider(Qt.Horizontal)
        self.seek.setObjectName("Seek")
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.time = QLabel("0:00 / 0:00")
        self.time.setFont(theme.font("caption", mono=True, tabular=True))
        self.time.setProperty("muted", True)
        self.volume_icon = QLabel()
        self.volume_icon.setPixmap(icons.pixmap("volume", theme.MUTED, 15))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setObjectName("Volume")
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(84)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.SPACE + 4, theme.SPACE, theme.SPACE + 4, theme.SPACE)
        layout.setSpacing(theme.SPACE + 4)
        layout.addWidget(self.play_btn)
        layout.addWidget(now)
        layout.addWidget(self.seek, 1)
        layout.addWidget(self.time)
        layout.addWidget(self.volume_icon)
        layout.addWidget(self.volume)

        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(lambda _e, msg: self.error.emit(msg))

    def play(self, path, label, artist=""):
        if path != self.path:
            self.path = path
            self.player.setSource(QUrl.fromLocalFile(path))
            self.title.setText(label)
            self.subtitle.setText(artist or "—")
        self.player.play()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        elif self.path:
            self.player.play()

    def seek_by(self, ms):
        if self.path:
            self.player.setPosition(max(0, min(self.player.position() + ms, self.player.duration())))

    def volume_by(self, step):
        self.volume.setValue(self.volume.value() + step)

    def stop_if(self, paths):
        """Release a file before it is moved or trashed."""
        if self.path and any(self.path == p or self.path.startswith(p.rstrip("/") + "/") for p in paths):
            self.player.stop()
            self.player.setSource(QUrl())
            self.path = None
            self.title.setText("Nothing playing")
            self.subtitle.setText("Double-click a track")

    def _position(self, pos):
        if not self.seek.isSliderDown():
            self.seek.setValue(pos)
        self.time.setText(f"{fmt_time(pos / 1000) or '0:00'} / {fmt_time(self.player.duration() / 1000) or '0:00'}")

    def _state(self, state):
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setIcon(icons.icon("pause" if playing else "play", theme.INK_0, 16))
