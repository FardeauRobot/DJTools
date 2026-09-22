"""BPM detection with beat_this, for tracks neither rekordbox nor the file tag gives a tempo for. No Qt here.

Optional: beat_this pulls in PyTorch, so it lives in requirements-analysis.txt and every import is deferred.
The result is a number without a beat grid, so it stays in the cache — never written to rekordbox or the file.
"""
import os
import subprocess
import tempfile

from .cache import Cache
from .config import CACHE_PATH

INSTALL_HINT = ".venv/bin/pip install -r requirements-analysis.txt"
DEFAULT_RANGE = (70, 180)
NEEDS_AFCONVERT = {".m4a", ".aac"}  # libsndfile reads everything else in config.AUDIO_EXTS


def available():
    """(True, "") when beat_this and soundfile import, else (False, why)."""
    try:
        import beat_this.inference  # noqa: F401
        import soundfile  # noqa: F401
    except Exception as exc:  # ImportError, or a torch that fails to load
        return False, str(exc)
    return True, ""


def fold(bpm, low, high):
    """Halve or double into [low, high]: the network's typical miss is a whole octave (87 for 174).
    The range must span an octave to hold every tempo, so a narrower one is widened upwards."""
    high = max(high, 2 * low)
    while bpm > high:
        bpm /= 2
    while bpm < low:
        bpm *= 2
    return bpm


def tempo(beats, windows=(16, 8)):
    """BPM from beat times in seconds, or None.

    Not 60 / median(interval): beat_this reports beats on a 20 ms frame grid, so one interval is only good
    to ±2% (142.86 or 136.36, never 140). Not one line through the whole track either: a single gap counted
    wrong after a breakdown skews everything after it. A line through each run of `window` evenly spaced
    beats averages the grid away locally, and the median over those runs ignores breakdowns and outros.
    Shorter runs are the fallback for a track whose steady stretches are brief (broken beats, many breaks)."""
    import numpy as np

    beats = np.asarray(beats, dtype=float)
    if len(beats) < 2:
        return None
    gaps = np.diff(beats)
    period = float(np.median(gaps))
    steady = np.abs(gaps / period - 1) < 0.15
    for window in windows:
        x = np.arange(window + 1)
        slopes = [
            np.polyfit(x, beats[i:i + window + 1], 1)[0]
            for i in range(0, len(gaps) - window + 1, 2)
            if steady[i:i + window].all()
        ]
        if len(slopes) >= 3:
            return 60.0 / float(np.median(slopes))
    return None


def _load(path):
    """(mono-or-stereo float32 samples, sample rate). No ffmpeg on this Mac: m4a/aac go through afconvert,
    and so does a file libsndfile gives up on (a FLAC that "lost sync")."""
    import soundfile as sf

    if os.path.splitext(path)[1].lower() not in NEEDS_AFCONVERT:
        try:
            return sf.read(path, dtype="float32")
        except Exception:
            pass
    with tempfile.TemporaryDirectory() as tmp:
        wav = os.path.join(tmp, "decoded.wav")
        subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16", path, wav], check=True, capture_output=True)
        return sf.read(wav, dtype="float32")


class Detector:
    """Loads the model once (the first run downloads ~78 MB into torch's cache), then detects per file."""

    def __init__(self, bpm_range=DEFAULT_RANGE):
        import torch
        from beat_this.inference import Audio2Beats

        self._make = lambda device: Audio2Beats(checkpoint_path="final0", device=device, dbn=False)
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model = self._make(self.device)
        self.low, self.high = bpm_range

    def bpm(self, path):
        """Detected tempo, or None when the track has no steady beat to measure."""
        signal, sr = _load(path)
        try:
            beats, _downbeats = self.model(signal, sr)
        except Exception:
            if self.device == "cpu":
                raise
            self.device = "cpu"  # an op missing on mps: finish the run on the CPU
            self.model = self._make("cpu")
            beats, _downbeats = self.model(signal, sr)
        bpm = tempo(beats)
        return None if bpm is None else round(fold(bpm, self.low, self.high), 1)


def analyze(paths, bpm_range=DEFAULT_RANGE, progress=None, cancelled=None):
    """Detect and store the BPM of `paths`. Runs in a worker thread, so it opens its own connection.

    Each result is committed as it comes, so stopping midway keeps what's done. Returns (stored, skipped,
    errors): `skipped` counts tracks with no steady beat (an answer, not a failure), `errors` the files that
    couldn't be decoded."""
    detector = Detector(bpm_range)
    cache = Cache(CACHE_PATH)
    stored, skipped, errors = 0, 0, []
    try:
        for i, path in enumerate(paths):
            if cancelled and cancelled():
                break
            if progress:
                progress(i, len(paths))
            try:
                bpm = detector.bpm(path)
            except Exception as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
                continue
            if bpm is None:
                skipped += 1
                continue
            cache.set_detected_bpm(path, bpm)
            stored += 1
        return stored, skipped, errors
    finally:
        cache.close()
