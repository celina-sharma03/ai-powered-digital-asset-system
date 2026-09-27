"""Speech-to-text for video audio with faster-whisper (int8 on CPU)."""

import threading
from dataclasses import dataclass

from ..config import settings

_lock = threading.Lock()
_model = None


@dataclass
class Segment:
    start: float
    end: float
    text: str


def _get():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(settings.whisper_model, device="cpu", compute_type="int8")
        return _model


def transcribe(wav_path: str) -> tuple[list[Segment], str]:
    """Returns (segments, detected_language). Voice-activity filtering skips silence and
    music-only stretches, which also cuts down Whisper's hallucinated text."""
    segments, info = _get().transcribe(wav_path, beam_size=1, vad_filter=True)
    out = [Segment(s.start, s.end, s.text.strip()) for s in segments if s.text.strip()]
    return out, info.language
