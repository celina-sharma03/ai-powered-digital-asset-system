"""Download and warm up all local AI models once (~1 GB total), so the first indexing
run doesn't stall on downloads. Safe to re-run: cached models are reused.

Usage (from repo root):  backend/.venv/Scripts/python backend/scripts/download_models.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from app.ai import embeddings, ocr, transcriber  # noqa: E402
from app.config import settings  # noqa: E402


def step(name, fn):
    t = time.time()
    print(f"-> {name} ...", flush=True)
    fn()
    print(f"   ok ({time.time() - t:.1f}s)", flush=True)


step(f"CLIP ({settings.clip_model})", lambda: embeddings.embed_images([Image.new("RGB", (224, 224))]))
step(f"Text embeddings ({settings.text_model})", lambda: embeddings.embed_passages(["hello world"]))
step(f"Whisper ({settings.whisper_model})", transcriber._get)
step("OCR (RapidOCR)", lambda: ocr.ocr_image(Image.new("RGB", (64, 64), "white")))
print("All models ready.")
