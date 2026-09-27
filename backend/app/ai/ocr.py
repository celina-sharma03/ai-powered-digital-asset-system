"""OCR with RapidOCR (ONNX runtime, CPU, pip-installable: no system Tesseract needed)."""

import threading

import numpy as np
from PIL import Image

_lock = threading.Lock()
_engine = None


def _get():
    global _engine
    with _lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR

            _engine = RapidOCR()
        return _engine


def ocr_image(image: Image.Image, min_confidence: float = 0.5) -> str:
    result, _ = _get()(np.array(image.convert("RGB")))
    if not result:
        return ""
    # result rows: [box, text, confidence]
    return " ".join(text for _, text, conf in result if float(conf) >= min_confidence).strip()
