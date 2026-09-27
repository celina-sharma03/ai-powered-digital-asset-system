"""Central configuration, read once from environment variables / the repo-root `.env` file."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

# Hugging Face's newer "Xet" download backend can hang on some networks; plain HTTPS is reliable.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# Bump this when the processing logic changes: every asset indexed with an older
# version is re-processed on the next run (see indexing/scanner.py).
PIPELINE_VERSION = 1


def _path(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    media_dir: Path = _path("MEDIA_DIR", "./data/media")
    storage_dir: Path = _path("STORAGE_DIR", "./storage")

    ffmpeg_bin: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    ffprobe_bin: str = os.getenv("FFPROBE_BIN", "ffprobe")

    clip_model: str = os.getenv("CLIP_MODEL", "clip-ViT-B-32")
    text_model: str = os.getenv("TEXT_MODEL", "BAAI/bge-small-en-v1.5")
    whisper_model: str = os.getenv("WHISPER_MODEL", "base")
    whisper_max_seconds: int = int(os.getenv("WHISPER_MAX_SECONDS", "180"))

    video_frame_interval: float = float(os.getenv("VIDEO_FRAME_INTERVAL_SEC", "5"))
    video_min_frames: int = int(os.getenv("VIDEO_MIN_FRAMES", "4"))
    video_max_frames: int = int(os.getenv("VIDEO_MAX_FRAMES", "12"))

    ocr_pdf_pages: bool = _bool("OCR_PDF_PAGES", True)
    ocr_images: bool = _bool("OCR_IMAGES", False)

    caption_provider: str = os.getenv("CAPTION_PROVIDER", "none").lower()
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    caption_model: str = os.getenv("CAPTION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    caption_max_per_run: int = int(os.getenv("CAPTION_MAX_PER_RUN", "200"))

    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "3"))

    @property
    def db_path(self) -> Path:
        return self.storage_dir / "dam.sqlite3"

    @property
    def qdrant_path(self) -> Path:
        return self.storage_dir / "qdrant"

    @property
    def thumbs_dir(self) -> Path:
        return self.storage_dir / "thumbnails"


settings = Settings()
