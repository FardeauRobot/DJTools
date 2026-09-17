"""Bottom transport bar: play / pause, seek, volume."""
from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QToolButton, QWidget

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

        self.play_btn = QToolButton(text="▶")
        self.play_btn.setFixedWidth(36)
        self.play_btn.clicked.connect(self.toggle)
        self.title = QLabel("Nothing playing")
        self.title.setObjectName("NowPlaying")
        self.title.setMinimumWidth(220)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 0)
        self.seek.sliderMoved.connect(self.player.setPosition)
        self.time = QLabel("0:00 / 0:00")
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.valueChanged.connect(lambda v: self.audio.setVolume(v / 100))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.addWidget(self.play_btn)
        layout.addWidget(self.title)
        layout.addWidget(self.seek, 1)
        layout.addWidget(self.time)
        layout.addWidget(QLabel("🔊"))
        layout.addWidget(self.volume)

        self.player.positionChanged.connect(self._position)
        self.player.durationChanged.connect(lambda d: self.seek.setRange(0, d))
        self.player.playbackStateChanged.connect(self._state)
        self.player.errorOccurred.connect(lambda _e, msg: self.error.emit(msg))

    def play(self, path, label):
        if path != self.path:
            self.path = path
            self.player.setSource(QUrl.fromLocalFile(path))
            self.title.setText(label)
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

    def _position(self, pos):
        if not self.seek.isSliderDown():
            self.seek.setValue(pos)
        self.time.setText(f"{fmt_time(pos / 1000) or '0:00'} / {fmt_time(self.player.duration() / 1000) or '0:00'}")

    def _state(self, state):
        self.play_btn.setText("⏸" if state == QMediaPlayer.PlayingState else "▶")
