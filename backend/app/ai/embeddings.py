"""Embedding models (both run locally on CPU).

* CLIP (clip-ViT-B-32): images and text in the SAME 512-d vector space, so a text
  query can be compared directly with images, video frames and PDF page renders.
* BGE-small (bge-small-en-v1.5): 384-d text embeddings for extracted text
  (OCR, PDF text, transcripts, captions). Better than CLIP at longer text.

Models are loaded lazily on first use and shared across threads.
"""

import threading

import numpy as np
from PIL import Image

from ..config import settings

_lock = threading.Lock()
_models: dict = {}

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _get(name: str):
    with _lock:
        if name not in _models:
            from sentence_transformers import SentenceTransformer  # heavy import, keep lazy

            _models[name] = SentenceTransformer(name, device="cpu")
        return _models[name]


def clip_dim() -> int:
    return 512


def text_dim() -> int:
    return 384


def embed_images(images: list[Image.Image]) -> np.ndarray:
    model = _get(settings.clip_model)
    return model.encode(images, batch_size=16, normalize_embeddings=True, convert_to_numpy=True)


def embed_clip_text(text: str) -> np.ndarray:
    return _get(settings.clip_model).encode([text], normalize_embeddings=True, convert_to_numpy=True)[0]


def embed_passages(texts: list[str]) -> np.ndarray:
    model = _get(settings.text_model)
    return model.encode(texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True)


def embed_query_text(text: str) -> np.ndarray:
    model = _get(settings.text_model)
    return model.encode([BGE_QUERY_PREFIX + text], normalize_embeddings=True, convert_to_numpy=True)[0]
