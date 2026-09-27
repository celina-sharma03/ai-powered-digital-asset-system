"""PDF processor.

  * Text layer  -> extracted per page with PyMuPDF and chunked (keeps page numbers).
  * Scanned     -> pages with almost no text get OCR'd (capped per document).
  * Visual      -> the first pages are rendered and embedded with CLIP, because a
                   brochure is often mostly photos (a building, an interior) with little text.
"""

from pathlib import Path

import pymupdf
from PIL import Image

from ..ai import ocr
from ..config import settings
from .types import CaptionBudget, Extracted, ProcessingError, TextItem, VisualItem, chunk_text

MAX_TEXT_PAGES = 60         # bound the cost of very long documents
MAX_RENDERED_PAGES = 3      # pages embedded visually with CLIP
MAX_OCR_PAGES = 10
MIN_TEXT_CHARS = 40         # below this a page is treated as scanned/image-only


def _render(page: "pymupdf.Page", zoom: float) -> Image.Image:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def process_pdf(path: Path, captions: CaptionBudget) -> Extracted:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:
        raise ProcessingError(f"cannot open PDF: {exc}") from exc
    if doc.needs_pass:
        raise ProcessingError("PDF is password protected")
    if doc.page_count == 0:
        raise ProcessingError("PDF has no pages")

    with doc:
        title = (doc.metadata or {}).get("title") or ""
        out = Extracted(metadata={"page_count": doc.page_count, "title": title.strip() or None})

        ocr_used, ocr_errors = 0, 0
        for page_no in range(min(doc.page_count, MAX_TEXT_PAGES)):
            page = doc[page_no]
            text = page.get_text().strip()
            source = "pdf_text"
            if len(text) < MIN_TEXT_CHARS and settings.ocr_pdf_pages and ocr_used < MAX_OCR_PAGES:
                ocr_used += 1
                try:
                    text, source = ocr.ocr_image(_render(page, 2.0)), "ocr"
                except Exception:
                    ocr_errors += 1
            for chunk in chunk_text(text):
                out.texts.append(TextItem(source, page_no + 1, chunk))

        for page_no in range(min(doc.page_count, MAX_RENDERED_PAGES)):
            out.visuals.append(VisualItem("page", page_no + 1, _render(doc[page_no], 1.0)))

    out.thumbnail = out.visuals[0].image if out.visuals else None
    out.stages["text"] = f"ok ({len(out.texts)} chunks)"
    out.stages["page_render"] = f"ok ({len(out.visuals)} pages)"
    if ocr_used:
        out.stages["ocr"] = f"failed on {ocr_errors}/{ocr_used} pages" if ocr_errors else f"ok ({ocr_used} pages)"
    if not out.texts and not out.visuals:
        raise ProcessingError("no extractable content")

    first_text = next((t.text for t in out.texts), "")
    out.summary = (title.strip() + " — " if title.strip() else "") + first_text[:300]
    return out
