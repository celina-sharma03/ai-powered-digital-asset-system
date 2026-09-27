"""Video processor.

Strategy (we do NOT process every frame):
  1. ffprobe      -> duration, resolution, codec, whether there is an audio track.
  2. Keyframes    -> one frame every VIDEO_FRAME_INTERVAL_SEC seconds, clamped to
                     [VIDEO_MIN_FRAMES, VIDEO_MAX_FRAMES] and spread evenly over the whole
                     video (taken mid-segment to avoid black fade-in frames). Every
                     video costs at most ~12 CLIP embeddings no matter how long it is.
                     The pipeline then drops near-identical frames (static shots).
  3. Audio        -> first WHISPER_MAX_SECONDS transcribed with Whisper. This is what
                     finds talking-head content such as testimonials, which is about
                     speech rather than visuals.
Each keyframe keeps its timestamp, so search can open the video at the moment that matched.
"""

import io
import json
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from ..ai import transcriber
from ..config import settings
from .types import CaptionBudget, Extracted, ProcessingError, TextItem, VisualItem, chunk_text

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # no console popups on Windows


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


def probe(path: Path) -> dict:
    res = _run([settings.ffprobe_bin, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path)], timeout=60)
    if res.returncode != 0:
        first_line = (res.stderr.decode(errors="ignore").strip().splitlines() or ["unknown error"])[0]
        raise ProcessingError(f"not a readable video (ffprobe: {first_line.split('] ')[-1][:150]})")
    info = json.loads(res.stdout or b"{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProcessingError("no video stream found")
    duration = float(info.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ProcessingError("could not determine video duration")
    return {
        "duration_sec": round(duration, 2),
        "width": video.get("width"),
        "height": video.get("height"),
        "codec": video.get("codec_name"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def frame_timestamps(duration: float) -> list[float]:
    n = int(duration // settings.video_frame_interval)
    n = max(settings.video_min_frames, min(settings.video_max_frames, n))
    return [round((i + 0.5) * duration / n, 2) for i in range(n)]


def grab_frame(path: Path, ts: float) -> Image.Image | None:
    # -ss before -i = fast keyframe seek; scale down since CLIP uses 224px anyway.
    res = _run([settings.ffmpeg_bin, "-v", "error", "-ss", str(ts), "-i", str(path),
                "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
               timeout=60)
    if res.returncode != 0 or not res.stdout:
        return None
    return Image.open(io.BytesIO(res.stdout)).convert("RGB")


def transcribe_audio(path: Path) -> list[TextItem]:
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        res = _run([settings.ffmpeg_bin, "-v", "error", "-i", str(path), "-t", str(settings.whisper_max_seconds),
                    "-vn", "-ac", "1", "-ar", "16000", str(wav)], timeout=300)
        if res.returncode != 0 or not wav.exists():
            raise RuntimeError(f"audio extraction failed: {res.stderr.decode(errors='ignore')[:200]}")
        segments, _lang = transcriber.transcribe(str(wav))

    # Group segments into ~30 s windows so each chunk keeps a useful start timestamp.
    items, buf, start = [], [], None
    for seg in segments:
        start = seg.start if start is None else start
        buf.append(seg.text)
        if seg.end - start >= 30:
            items.append(TextItem("transcript", round(start, 1), " ".join(buf)))
            buf, start = [], None
    if buf:
        items.append(TextItem("transcript", round(start, 1), " ".join(buf)))
    # Guard against very long windows exceeding the text model's input size.
    return [TextItem(i.source, i.locator, c) for i in items for c in chunk_text(i.text)]


def process_video(path: Path, captions: CaptionBudget) -> Extracted:
    meta = probe(path)
    out = Extracted(metadata=meta, dedupe_visuals=True)

    frames = []
    for ts in frame_timestamps(meta["duration_sec"]):
        img = grab_frame(path, ts)
        if img is not None:
            frames.append(VisualItem("frame", ts, img))
    if not frames:
        raise ProcessingError("could not decode any frames")
    out.visuals = frames
    out.thumbnail = frames[len(frames) // 2].image
    out.stages["keyframes"] = f"ok ({len(frames)} frames)"

    if meta["has_audio"]:
        try:
            transcript = transcribe_audio(path)
            out.texts.extend(transcript)
            out.stages["transcript"] = "ok" if transcript else "ok (no speech detected)"
            if transcript:
                out.summary = transcript[0].text[:300]
        except Exception as exc:
            out.stages["transcript"] = f"failed: {exc}"
    else:
        out.stages["transcript"] = "skipped: no audio track"

    mid = frames[len(frames) // 2]
    captions.try_caption(mid.image, out, locator=mid.locator)
    return out
