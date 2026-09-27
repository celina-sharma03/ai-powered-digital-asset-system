"""Optional image captioning through Groq's OpenAI-compatible vision API.

Off by default (CAPTION_PROVIDER=none). Search works without it: CLIP covers visual
meaning. Captions add a text description, which helps the keyword and text-semantic
parts of hybrid search. The number of calls per run is capped because free tiers
are rate-limited, and a failed call only marks the "caption" stage as failed.
"""

import base64
import io
import time

import httpx
from PIL import Image

from ..config import settings

PROMPT = ("Describe this image in one or two factual sentences for a search index: "
          "main subjects, actions, setting, and any visible text. No speculation.")


class CaptionUnavailable(Exception):
    pass


def enabled() -> bool:
    return settings.caption_provider == "groq" and bool(settings.groq_api_key)


def caption(image: Image.Image, retries: int = 3) -> str:
    if not enabled():
        raise CaptionUnavailable("captioning disabled")

    img = image.convert("RGB")
    img.thumbnail((768, 768))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    data_url = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    payload = {
        "model": settings.caption_model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]}],
        "max_tokens": 120,
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {settings.groq_api_key}"}

    for attempt in range(retries):
        resp = httpx.post("https://api.groq.com/openai/v1/chat/completions",
                          json=payload, headers=headers, timeout=60)
        if resp.status_code == 429 or resp.status_code >= 500:   # rate limit / transient: back off
            time.sleep(2 ** attempt * 5)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise CaptionUnavailable(f"caption API kept failing (last status {resp.status_code})")
