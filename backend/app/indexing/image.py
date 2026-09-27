"""Image processor: validate + metadata, one CLIP-ready image, optional OCR and caption."""

from pathlib import Path

from PIL import Image, ImageOps

from ..ai import ocr
from ..config import settings
from .types import CaptionBudget, Extracted, ProcessingError, TextItem, VisualItem

Image.MAX_IMAGE_PIXELS = 200_000_000   # allow large photos but still guard against decompression bombs


def process_image(path: Path, captions: CaptionBudget) -> Extracted:
    try:
        with Image.open(path) as probe:
            probe.verify()                       # detects truncated / corrupted files
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)       # respect camera orientation
        width, height = img.size
        img = img.convert("RGB")
        img.thumbnail((1024, 1024))              # the models never need more than this
    except Exception as exc:
        raise ProcessingError(f"cannot decode image: {exc}") from exc

    out = Extracted(metadata={"width": width, "height": height})
    out.visuals.append(VisualItem("image", None, img))
    out.thumbnail = img
    out.stages["clip"] = "ok"

    if settings.ocr_images:
        try:
            text = ocr.ocr_image(img)
            if text:
                out.texts.append(TextItem("ocr", None, text))
            out.stages["ocr"] = "ok"
        except Exception as exc:
            out.stages["ocr"] = f"failed: {exc}"

    captions.try_caption(img, out)
    return out
