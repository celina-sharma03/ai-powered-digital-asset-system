"""Download a mixed-media dataset (images, videos, PDFs) from public sources.

Sources
  * Pexels API (free licence)  -> photos and videos
  * Wikimedia Commons API      -> PDF documents / brochures

Files get opaque names (``pexels-photo-123.jpg``) and are spread randomly over
``batch_XX`` folders, so neither filenames nor folders reveal what a file shows.
That keeps the search evaluation honest: the app has to understand the content.

A ``manifest.csv`` records where every file came from and which source query found
it. The indexer never reads it; only the evaluation uses it as a relevance label.

Standard library only, so it runs before the backend dependencies are installed.

Usage (from repo root):
    python backend/scripts/download_dataset.py                 # default mix, ~2-3 GB
    python backend/scripts/download_dataset.py --photos-per-query 40 --videos-per-query 5   # quick small set
"""

import argparse
import csv
import json
import os
import random
import shutil
import sys
import threading
import time
import urllib.parse
import urllib.request
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "vanco-dam-assignment/1.0 (educational dataset builder)"
NUM_BATCH_FOLDERS = 6

PHOTO_QUERIES = [
    "woman with cat", "cat", "dog playing", "modern living room", "kitchen interior",
    "bedroom interior", "construction site", "business meeting", "handshake",
    "city skyline night", "beach", "food", "mountain landscape", "apartment building exterior",
]
VIDEO_QUERIES = [
    "construction site", "construction workers", "person talking to camera",
    "testimonial interview", "modern living room", "cat", "city traffic", "ocean waves",
    "cooking", "office work", "aerial view houses",
]
PDF_QUERIES = [
    "real estate brochure", "housing project", "residential apartments", "tourism brochure",
    "annual report", "product catalogue", "restaurant menu", "architecture plan",
]

MANIFEST_FIELDS = ["relative_path", "media_type", "source", "source_id", "source_query",
                   "title", "source_url", "license", "bytes"]


# --------------------------------------------------------------------------- helpers

def load_env() -> dict:
    env = {}
    env_file = REPO_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return {**env, **os.environ}


def http_json(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def download(url: str, dest: Path, retries: int = 4) -> int:
    """Stream to a temp file, then rename, so an interrupted download never leaves a
    half-written file with the final name. Retries timeouts and HTTP 429 with backoff."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as fh:
                shutil.copyfileobj(resp, fh, length=1024 * 1024)
            tmp.replace(dest)
            return dest.stat().st_size
        except Exception as exc:
            retryable = isinstance(exc, (TimeoutError, OSError)) or getattr(exc, "code", None) in (429, 500, 502, 503)
            if attempt == retries - 1 or not retryable:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(10 * (attempt + 1))
    raise RuntimeError("unreachable")


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.Lock()
        self.known: set[str] = set()
        if path.exists():
            with open(path, newline="", encoding="utf-8") as fh:
                self.known = {row["source_id"] for row in csv.DictReader(fh)}
        else:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, MANIFEST_FIELDS).writeheader()

    def add(self, row: dict) -> None:
        with self.lock:
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                csv.DictWriter(fh, MANIFEST_FIELDS).writerow(row)
            self.known.add(row["source_id"])


# --------------------------------------------------------------------------- sources

def pexels_photos(api_key: str, query: str, count: int) -> list[dict]:
    items, page = [], 1
    while len(items) < count:
        q = urllib.parse.urlencode({"query": query, "per_page": 80, "page": page})
        data = http_json(f"https://api.pexels.com/v1/search?{q}", {"Authorization": api_key})
        photos = data.get("photos", [])
        if not photos:
            break
        for p in photos:
            items.append({
                "media_type": "image", "source": "pexels", "source_id": f"pexels-photo-{p['id']}",
                "url": p["src"]["large2x"], "ext": ".jpg", "title": p.get("alt") or "",
                "source_url": p["url"], "license": "Pexels License",
            })
        page += 1
    return items[:count]


def pexels_videos(api_key: str, query: str, count: int, max_duration: int = 60) -> list[dict]:
    items, page = [], 1
    while len(items) < count and page <= 5:
        q = urllib.parse.urlencode({"query": query, "per_page": 40, "page": page})
        data = http_json(f"https://api.pexels.com/videos/search?{q}", {"Authorization": api_key})
        videos = data.get("videos", [])
        if not videos:
            break
        for v in videos:
            if v.get("duration", 0) > max_duration:
                continue
            # Prefer an mp4 rendition around 720p: good enough for AI, keeps size sane.
            files = [f for f in v.get("video_files", [])
                     if f.get("file_type") == "video/mp4" and f.get("width") and f["width"] <= 1280]
            if not files:
                continue
            best = max(files, key=lambda f: f["width"])
            items.append({
                "media_type": "video", "source": "pexels", "source_id": f"pexels-video-{v['id']}",
                "url": best["link"], "ext": ".mp4", "title": "",
                "source_url": v["url"], "license": "Pexels License",
            })
        page += 1
    return items[:count]


def commons_pdfs(query: str, count: int, max_bytes: int = 15 * 1024 * 1024) -> list[dict]:
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{query} filemime:application/pdf", "gsrnamespace": 6, "gsrlimit": 50,
        "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata",
    })
    data = http_json(f"https://commons.wikimedia.org/w/api.php?{params}")
    items = []
    for page in (data.get("query", {}).get("pages", {}) or {}).values():
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") != "application/pdf" or info.get("size", 0) > max_bytes:
            continue
        licence = (info.get("extmetadata", {}).get("LicenseShortName", {}) or {}).get("value", "")
        items.append({
            "media_type": "pdf", "source": "wikimedia-commons", "source_id": f"commons-{page['pageid']}",
            "url": info["url"], "ext": ".pdf", "title": page.get("title", ""),
            "source_url": info.get("descriptionurl", ""), "license": licence,
        })
    return items[:count]


# --------------------------------------------------------------------------- edge cases

def make_edge_cases(media_dir: Path, rng: random.Random) -> None:
    """Plant files that exercise failure handling: duplicates in other folders,
    corrupted files with valid extensions, and unsupported file types."""
    edge = media_dir / "misc_uploads"
    edge.mkdir(parents=True, exist_ok=True)

    existing = [p for p in media_dir.rglob("*") if p.is_file() and p.parent != edge
                and p.suffix in {".jpg", ".mp4", ".pdf"}]
    for src in rng.sample(existing, min(15, len(existing))):
        shutil.copy2(src, edge / f"copy_of_{src.name}")  # exact duplicate, different folder + name

    jpgs = [p for p in existing if p.suffix == ".jpg"]
    if jpgs:
        (edge / "broken_photo.jpg").write_bytes(jpgs[0].read_bytes()[:2048])  # truncated image
    (edge / "corrupted_clip.mp4").write_bytes(os.urandom(50_000))              # random bytes
    (edge / "not_really.pdf").write_text("This is plain text, not a PDF.", encoding="utf-8")
    (edge / "meeting_notes.txt").write_text("Unsupported file type example.", encoding="utf-8")
    (edge / "slides.pptx").write_bytes(b"PK\x03\x04" + os.urandom(1000))
    (edge / "archive.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    print(f"Edge cases written to {edge}")


# --------------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--photos-per-query", type=int, default=110)
    ap.add_argument("--videos-per-query", type=int, default=18)
    ap.add_argument("--pdfs-per-query", type=int, default=25)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--no-edge-cases", action="store_true")
    args = ap.parse_args()

    env = load_env()
    api_key = env.get("PEXELS_API_KEY", "")
    if not api_key:
        sys.exit("PEXELS_API_KEY is missing. Add it to .env (see .env.example).")

    media_dir = Path(env.get("MEDIA_DIR", "./data/media"))
    media_dir = media_dir if media_dir.is_absolute() else (REPO_ROOT / media_dir).resolve()
    media_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(media_dir.parent / "manifest.csv")
    rng = random.Random(42)

    # 1. Collect candidate items from every source/query.
    plan: list[tuple[str, dict]] = []
    for q in PHOTO_QUERIES:
        plan += [(q, it) for it in pexels_photos(api_key, q, args.photos_per_query)]
        print(f"[plan] photos  {q!r}")
    for q in VIDEO_QUERIES:
        plan += [(q, it) for it in pexels_videos(api_key, q, args.videos_per_query)]
        print(f"[plan] videos  {q!r}")
    for q in PDF_QUERIES:
        try:
            plan += [(q, it) for it in commons_pdfs(q, args.pdfs_per_query)]
            print(f"[plan] pdfs    {q!r}")
        except Exception as exc:  # Commons search hiccups shouldn't kill the whole run
            print(f"[plan] pdfs    {q!r} failed: {exc}")

    # The same item can come back for several queries; keep the first one.
    seen, unique = set(manifest.known), []
    for q, it in plan:
        if it["source_id"] not in seen:
            seen.add(it["source_id"])
            unique.append((q, it))
    print(f"{len(unique)} new files to download")

    # 2. Download in parallel. Already-downloaded ids are skipped, so the script can be re-run.
    done = {"n": 0, "bytes": 0}
    lock = threading.Lock()
    commons_lock = threading.Lock()   # Wikimedia rate-limits parallel downloads: one at a time, paced

    def fetch(entry):
        q, it = entry
        if it["source"] == "wikimedia-commons":
            with commons_lock:
                time.sleep(1.5)
                return _fetch(q, it)
        return _fetch(q, it)

    def _fetch(q, it):
        # Stable pseudo-random folder per file (crc32 of its id), independent of download order.
        folder = media_dir / f"batch_{zlib.crc32(it['source_id'].encode()) % NUM_BATCH_FOLDERS + 1:02d}"
        folder.mkdir(exist_ok=True)
        dest = folder / f"{it['source_id']}{it['ext']}"
        try:
            size = download(it["url"], dest)
        except Exception as exc:
            print(f"  ! failed {it['source_id']}: {exc}")
            return
        manifest.add({
            "relative_path": dest.relative_to(media_dir).as_posix(), "media_type": it["media_type"],
            "source": it["source"], "source_id": it["source_id"], "source_query": q,
            "title": it["title"], "source_url": it["source_url"], "license": it["license"], "bytes": size,
        })
        with lock:
            done["n"] += 1
            done["bytes"] += size
            if done["n"] % 25 == 0:
                print(f"  {done['n']}/{len(unique)} files, {done['bytes'] / 1e9:.2f} GB")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(fetch, unique))

    if not args.no_edge_cases:
        make_edge_cases(media_dir, rng)
    print(f"Done. {done['n']} files downloaded ({done['bytes'] / 1e9:.2f} GB). Manifest: {manifest.path}")


if __name__ == "__main__":
    main()
