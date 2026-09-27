# Dataset summary

Collected with `backend/scripts/download_dataset.py` (reproducible, resumable). The media itself is **not**
in the repository (`data/` is git-ignored).

## Totals

| Type | Files | Size |
|---|---|---|
| Images (JPEG) | 1,699 | 0.52 GB |
| Videos (MP4, ≤ 720p, ≤ 60 s) | 198 | 2.09 GB |
| PDFs | 126 | 0.48 GB |
| Unsupported (planted: `.txt`, `.zip`, `.pptx`) | 3 | < 1 MB |
| **Total** | **2,026 files** | **3.10 GB** |

After indexing: **2,005 unique assets** (1,685 images, 195 videos, 125 PDFs; 8,478 PDF pages), 2,571 visual
vectors and 6,416 text vectors. The counts above include the planted duplicates and corrupted files described below.

## Sources and licences

| Source | Content | Licence |
|---|---|---|
| [Pexels](https://www.pexels.com/api/) | Photos and videos | Pexels License (free to use) |
| [Internet Archive](https://archive.org) | Brochures, annual reports, catalogues, menus, house-plan books, tourist guides | Per item (see `source_url` in the manifest); used here only for non-commercial evaluation |
| [Wikimedia Commons](https://commons.wikimedia.org) | Real-estate brochures (historic Palos Verdes collection) | Per file (recorded in the manifest) |

`data/manifest.csv` records every file's source, source id, source URL, licence, title and the **source query**
it was found with. The indexer never reads the manifest; the evaluation uses it as a relevance label.

## Composition (by source query)

| Type | Queries (files each) |
|---|---|
| Images | woman with cat (120), cat (128), dog playing (116), modern living room (128), kitchen interior (126), bedroom interior (122), construction site (118), business meeting (127), handshake (119), city skyline night (115), beach (113), food (128), mountain landscape (110), apartment building exterior (115) |
| Videos | construction site (18), construction workers (15), person talking to camera (18), testimonial interview (18), modern living room (18), cat (18), city traffic (18), ocean waves (18), cooking (18), office work (18), aerial view houses (18) |
| PDFs | real estate brochure (26), housing project (15), residential apartments (13), tourism brochure (14), annual report (12), product catalogue (15), restaurant menu (15), architecture plan (15) |

The categories were chosen to cover the example queries in the assignment (woman with a cat, testimonials,
residential brochures, modern living room, construction), plus distractors (food, mountains, traffic, menus).
Several are close on purpose (cat vs woman with cat, office work vs business meeting vs testimonial interview), so ranking has to separate them.

## Built-in test cases (`misc_uploads/`)

| File(s) | Purpose | Result after indexing |
|---|---|---|
| 13 `copy_of_*.jpg` + 2 `copy_of_*.mp4` | Exact duplicates in another folder, with different names | Linked to the existing assets; not processed again |
| `broken_photo.jpg` | Truncated JPEG | `failed`: "cannot decode image: image file is truncated" |
| `corrupted_clip.mp4` | Random bytes with a video extension | `failed`: "not a readable video" |
| `not_really.pdf` | Plain text with a `.pdf` extension | `failed`: "cannot open PDF" |
| `meeting_notes.txt`, `slides.pptx`, `archive.zip` | Unsupported types | `unsupported`, listed in the UI |

## Observations that shaped the design

- **Videos are short stock clips** (18 s average, 60 s max). Evenly spaced keyframes (every 5 s, at least 4) give
  about 4.5 frames per video, and near-duplicate frames of static shots are dropped before storage.
- **Only 3 of 195 videos contain speech.** 49 have an audio track, mostly music. Whisper plus voice-activity
  filtering correctly produces no transcript for music. "Testimonial" search therefore relies on visual cues.
- **80 of 125 PDFs are scanned** (no text layer on some pages) and needed OCR. OCR is the slowest step on CPU,
  so it's capped at 4 pages per document.

## Size limitation and handling a larger collection

### The limitation

The assignment suggests 5–10 GB; this dataset is **3.1 GB**. The limit was **processing time**, not storage,
internet or the design:
- The laptop is CPU-only: Intel i3-1315U, 8 GB RAM, no GPU.
- The deadline was the same day.
- The first full index of 3.1 GB took about an hour (measured below).

### Measured on this dataset

| Run | Files | Work | Time |
|---|---|---|---|
| First full index | 1,830 | 1,812 new assets | 39 min |
| Incremental, after adding PDFs (resumed after a cancel) | 2,026 | 125 new, 1,898 unchanged skipped | 17 min |
| Re-run with nothing changed | 2,026 | 0 | **1 s** |

- **Cost per file on this CPU:** about 0.2 s per image, 3–25 s per video, 2–20 s per PDF.
  Cost depends on the *number* of files and video minutes, not on gigabytes.
- **Index size:** 109 MB for 3.1 GB of media (about 3.5%): SQLite, vectors and thumbnails.
- **Estimate for 10 GB of the same mix** (about 6,500 assets): roughly 3 hours for the first pass on this laptop,
  then seconds for re-runs. Nothing in the code has to change.

### What already handles a large collection (implemented)

| Concern | How the current code handles it | Where |
|---|---|---|
| Re-runs over a big folder | Unchanged files (same path, size and modified time) are skipped **without re-reading them**. Only new or changed files cost anything | `indexing/scanner.py` (fast path in `scan()`) |
| Huge files | Hashing streams 1 MB chunks, so a 5 GB video never has to fit in memory | `indexing/scanner.py` (`sha256_of`) |
| Very long videos | Cost is capped: at most 12 keyframes and 180 s of audio per video, whatever its length | `indexing/video.py`, `.env` `VIDEO_*`, `WHISPER_MAX_SECONDS` |
| Very long PDFs | Text from the first 60 pages, visuals from 3 pages, OCR on at most 4 pages | `indexing/pdf.py` (constants at the top) |
| Large images | Downscaled to 1024 px before any AI step | `indexing/image.py` |
| Memory | Models load once; files are processed one at a time, so memory doesn't grow with collection size | `indexing/pipeline.py` |
| Long-running jobs | Runs in the background with a live progress bar and a cancel button; search keeps working meanwhile | `indexing/pipeline.py`, Indexing tab |
| Crash or restart mid-run | Each file is committed on its own; on restart, unfinished files return to the queue and the next run continues (tested: run 2 was cancelled, run 3 resumed) | `db.py` (`init_db`), `indexing/pipeline.py` |
| Duplicates | Found by content hash, so a duplicate costs one hash and no AI work | `indexing/scanner.py` |
| Bad files | One corrupted file fails alone with a reason; the run continues | `indexing/pipeline.py` |
| Upgrading the pipeline | Bumping `PIPELINE_VERSION` reprocesses only outdated assets, never the whole library | `config.py` |

### What would change as the collection grows

| Scale | What changes |
|---|---|
| **About 10 GB** (thousands of assets) | Nothing. It works as-is; only the first pass is slow on a CPU |
| **About 100 GB** (tens to hundreds of thousands of assets) | Switch embedded Qdrant to a **Qdrant server** (a one-line change in `search/vector_store.py`) for approximate (HNSW) search and to allow **several worker processes**. Add a **GPU** to batch embeddings (roughly 10–50× faster than this CPU) |
| **1 TB and up** (millions of assets) | A **job queue** (Redis + Celery/RQ) feeding many workers; **Postgres** instead of SQLite; files in object storage, with upload events triggering indexing instead of folder scans |

More production changes (better models, re-ranking, operations) are in the README under *Production improvements*.
