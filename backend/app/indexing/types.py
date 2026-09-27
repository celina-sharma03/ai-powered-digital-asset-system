"""Shared types for processors. A processor turns a file into `Extracted` content;
the pipeline then embeds and stores it. Keeping extraction separate from indexing
means a new file type only needs a new processor."""

from dataclasses import dataclass, field

from PIL import Image

from ..ai import captioner

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
PDF_EXTS = {".pdf"}


def file_type_for(ext: str) -> str | None:
    ext = ext.lower()
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in PDF_EXTS:
        return "pdf"
    return None


class ProcessingError(Exception):
    """The file can't be processed at all (corrupted, encrypted, unreadable)."""


@dataclass
class VisualItem:
    kind: str                 # image | frame | page
    locator: float | int | None
    image: Image.Image


@dataclass
class TextItem:
    source: str               # ocr | pdf_text | transcript | caption
    locator: float | int | None
    text: str


@dataclass
class Extracted:
    metadata: dict = field(default_factory=dict)
    visuals: list[VisualItem] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    thumbnail: Image.Image | None = None
    summary: str = ""
    stages: dict = field(default_factory=dict)   # stage -> "ok" | "skipped: why" | "failed: why"
    dedupe_visuals: bool = False                  # drop near-identical video frames


class CaptionBudget:
    """Caps caption API calls per run (free tiers are rate-limited)."""

    def __init__(self, limit: int):
        self.remaining = limit

    def try_caption(self, image: Image.Image, out: Extracted, locator=None) -> None:
        if not captioner.enabled():
            return
        if self.remaining <= 0:
            out.stages["caption"] = "skipped: per-run caption budget used up"
            return
        self.remaining -= 1
        try:
            text = captioner.caption(image)
            out.texts.append(TextItem("caption", locator, text))
            out.summary = out.summary or text
            out.stages["caption"] = "ok"
        except Exception as exc:  # AI failure never fails the whole asset
            out.stages["caption"] = f"failed: {exc}"


def chunk_text(text: str, size: int = 120, overlap: int = 20) -> list[str]:
    """Split into overlapping ~`size`-word chunks (small enough for the 512-token text model)."""
    words = text.split()
    if len(words) <= size:
        return [" ".join(words)] if words else []
    step = size - overlap
    return [" ".join(words[i:i + size]) for i in range(0, len(words) - overlap, step)]
