# AI-Powered Digital Asset Management

Index a folder of **images, videos and PDFs** and find assets with **natural-language search**
("a woman standing with a cat", "videos containing construction activity"). Search uses the
*content* of the files, never their filenames or folder names.
Everything runs locally on a CPU-only laptop with free, open-source models.

- **Demo video:** _add link here_
- **Architecture and data flow:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Dataset summary:** [docs/DATASET.md](docs/DATASET.md)
- **Search evaluation:** [evaluation/README.md](evaluation/README.md) (method and findings) · [evaluation/results.md](evaluation/results.md) (raw results)

---

## How it works (short version)

| File type | What the AI extracts | Model |
|---|---|---|
| Image | Visual embedding (+ optional OCR / caption) | CLIP ViT-B/32 |
| Video | Evenly spaced keyframes (every 5 s, 4–12 per video, near-duplicates dropped) with timestamps, plus a speech transcript | CLIP + Whisper (base, int8) |
| PDF | Text per page (OCR for scanned pages), plus visual embeddings of the first 3 page renders | BGE-small + RapidOCR + CLIP |

A search runs **three retrievers**:
- **Visual:** CLIP matches the text query against images, frames and pages.
- **Text meaning:** BGE embeddings over the extracted text.
- **Keyword:** BM25 over the extracted text.

They're merged with **Reciprocal Rank Fusion**. Each result shows *why* it matched and opens at the matching
video timestamp or PDF page.

Metadata, job state and full-text search live in **SQLite**; vectors live in **Qdrant** (embedded mode, no server).

## Requirement checklist

| Requirement | Where / how |
|---|---|
| Ingest images, videos, PDFs | `backend/app/indexing/{image,video,pdf}.py` |
| Basic metadata (name, type, size, location, …) | `files` + `assets` tables (`app/db.py`); resolution, duration and page count too |
| AI understanding of content | CLIP (visual), Whisper (speech), OCR, BGE text embeddings |
| Natural-language / semantic search | `app/search/hybrid.py` |
| Ranked by relevance | Reciprocal Rank Fusion of three retrievers, with a soft boost for query intent ("videos", "brochures") |
| Preview images and documents | UI modal: image viewer, video player (seeks to the matched moment), PDF viewer (opens the matched page) |
| Locate the original file | Full path, "Show in folder" (opens Explorer with the file selected), "Copy path" |
| Filters | File type, folder, size, video duration; retrieval mode (for comparing signals) |
| Indexing progress and status | **Indexing** tab: live progress bar, counters, current file, per-asset status |
| Unsupported / failed files | Recorded with a reason and listed in the UI "Problems" table |
| No duplicate indexing | SHA-256 content hash. The same file in another folder links to the existing asset |
| Incremental re-runs | Unchanged path, size and mtime are skipped without hashing. Changed and new files are processed. Deleted files are removed from search |
| Persistent storage | `storage/` (SQLite + Qdrant + thumbnails) survives restarts |
| Crash / restart safety | Per-asset commits. Interrupted work returns to the queue on startup |
| AI / model failure | Models load before processing (fails fast with a message). Optional stages fail softly (`partial`). Transient errors are retried up to `MAX_ATTEMPTS` |

---

## Setup and run

Tested on Windows 11 (i3-1315U, 8 GB RAM, no GPU). The steps are the same on macOS/Linux, apart from the venv paths.

### 1. Prerequisites

| Tool | Version | Install (Windows) |
|---|---|---|
| Python | 3.11 | `winget install -e --id Python.Python.3.11` |
| Node.js | 18+ | `winget install -e --id OpenJS.NodeJS.LTS` |
| ffmpeg (includes ffprobe) | any recent | `winget install -e --id Gyan.FFmpeg` |

> After installing ffmpeg, open a **new** terminal so it's on `PATH` (or set `FFMPEG_BIN`/`FFPROBE_BIN` in `.env`).

### 2. Install

Run these from the repo root:

```bash
# Configuration
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env

# Backend
python -m venv backend/.venv
backend/.venv/Scripts/python -m pip install -r backend/requirements.txt     # macOS/Linux: backend/.venv/bin/python

# Frontend (built once, then served by the backend)
cd frontend && npm install && npm run build && cd ..
```

### 3. Get media

**Option A: download the same dataset used here** (needs a free Pexels key in `.env`):

```bash
backend/.venv/Scripts/python backend/scripts/download_dataset.py
```

This fetches about 1,550 photos and about 200 videos from Pexels, plus PDFs from Wikimedia Commons, into `data/media/`.
It also plants edge cases: duplicates, corrupted files and unsupported files.

**Option B: use your own folder.** Set `MEDIA_DIR=` in `.env` to any folder of images, videos and PDFs.

### 4. Download the AI models (once, about 1 GB)

```bash
backend/.venv/Scripts/python backend/scripts/download_models.py
```

### 5. Start

```bash
backend/.venv/Scripts/python -m uvicorn app.main:app --app-dir backend --port 8000
```

Open **http://localhost:8000**. Go to **Indexing**, click **Scan & index changes**, and watch the progress.
Then search from the **Search** tab. Search works while indexing runs; results fill in as assets complete.

**Frontend dev mode** (optional, hot reload): keep the backend running and use `cd frontend && npm run dev`, then open http://localhost:5173.

### 6. Evaluate search quality (optional)

With the server running:

```bash
backend/.venv/Scripts/python backend/scripts/evaluate.py
```

This writes `evaluation/results.md` and `evaluation/results.json`.

---

## Project structure

```
backend/
  app/
    main.py              REST API (+ serves the built frontend)
    config.py            settings from .env
    db.py                SQLite schema, connections, crash recovery
    ai/                  model wrappers: embeddings (CLIP, BGE), transcriber (Whisper), ocr, captioner (optional)
    indexing/            scanner (what to process), pipeline (run + failures), image/video/pdf processors
    search/              vector_store (Qdrant), keyword (BM25/FTS5), hybrid (fusion + filters)
  scripts/               download_dataset, download_models, evaluate, dataset_summary
  requirements.txt
frontend/                React (Vite) UI: SearchView, ResultCard, PreviewModal, IndexingView
evaluation/              queries.json (test searches), results, write-up
docs/                    architecture, dataset summary
data/  storage/          (git-ignored) media files and generated index
```

## Configuration

All settings are in [.env.example](.env.example): media folder, models, video frame sampling, OCR toggles,
optional captioning, retry limit. The most useful ones:

| Variable | Default | Effect |
|---|---|---|
| `MEDIA_DIR` | `./data/media` | Folder to index (recursively) |
| `VIDEO_FRAME_INTERVAL_SEC` / `VIDEO_MIN_FRAMES` / `VIDEO_MAX_FRAMES` | 5 / 4 / 12 | Keyframe sampling |
| `WHISPER_MODEL` / `WHISPER_MAX_SECONDS` | base / 180 | Transcription quality vs speed |
| `OCR_IMAGES` | false | OCR every image (slower; helps posters and flyers) |
| `CAPTION_PROVIDER` | none | `groq` + `GROQ_API_KEY` adds AI captions (rate-limited, capped per run) |
| `MAX_ATTEMPTS` | 3 | Retries for transient processing failures |

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/search?q=…&type=video&folder=…&min_size=…&mode=hybrid` | Search |
| GET | `/api/assets/{id}` · `/thumbnail` · `/content` | Details, thumbnail, original file (range requests) |
| POST | `/api/assets/{id}/reveal` | Open the file's folder in the OS file manager |
| POST | `/api/index` (`{"full": false}`) · `/api/index/cancel` | Start / cancel indexing |
| GET | `/api/index/status` · `/api/index/issues` · `/api/stats` | Progress, problems, library stats |

Interactive docs: http://localhost:8000/docs

---

## Known limitations

- **Dataset about 2.5 GB, not 5–10 GB.** The limit was CPU-only indexing time and a same-day deadline, not the design.
  Processing time grows with the number of files (about 0.3 s per image and a few seconds per video on this laptop), not with gigabytes.
- **Stock videos rarely contain speech**, so the transcript signal is only exercised on a few clips. "Testimonial"
  queries rely mostly on the visual match (a person talking to the camera).
- **CLIP ViT-B/32 is a small model.** It struggles with counting, fine-grained attributes ("a *red* car next to a *blue* one"),
  negation ("no people"), and text inside images unless `OCR_IMAGES` is on.
- **PDF understanding is text-first.** Only the first 3 pages are embedded visually, and only the first 60 pages are read.
- **Embedded Qdrant does exact (brute-force) search.** That's fine at this scale (tens of thousands of vectors) but not for millions.
- **Single indexing worker** inside the API process (embedded Qdrant allows only one process).
- **Evaluation labels are "silver standard"**, derived from the source query each file was downloaded with. See the evaluation write-up.

## Production improvements

1. **Scale-out ingestion:** a job queue (Redis + Celery/RQ, or Kafka) with GPU workers that batch embeddings, and a
   Qdrant server with HNSW. Metadata moves to Postgres. A file watcher or object-storage events trigger indexing instead of full scans.
2. **Better models:** SigLIP or larger CLIP for visuals, and a vision-language model (e.g. Florence-2, Qwen-VL) to caption
   every image and keyframe, so text search gets richer descriptions. Scene-change detection instead of a fixed interval.
3. **Better ranking:** a cross-encoder or VLM re-ranker on the top 50, relevance feedback, and query rewriting with an LLM
   (extracting filters such as type, date and people from the query).
4. **Operations:** metrics on throughput and failures, a dead-letter queue for failed files, and model versioning with
   blue/green re-indexing (the `PIPELINE_VERSION` hook already supports selective re-processing).
5. **Product:** auth and permissions, tagging and collections, near-duplicate detection (perceptual hash) as well as
   exact duplicates, and video preview strips.
