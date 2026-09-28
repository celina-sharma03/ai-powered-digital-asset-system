# Architecture and data flow

## Overview

```
                        ┌──────────────────────────── FastAPI process ────────────────────────────┐
 MEDIA_DIR ──scan──►    │  Scanner ──► Asset queue ──► Processor (image / video / pdf)             │
 (images, videos,       │     │                              │  extract: frames, text, OCR, audio  │
  PDFs, anything else)  │     ▼                              ▼                                     │
                        │  SQLite ◄──── metadata, status ── Embedder (CLIP + BGE, CPU)             │
                        │  (assets, files, runs, FTS5)       │                                     │
                        │                                    ▼                                     │
                        │                              Qdrant (embedded): visual + text vectors    │
                        │                                                                          │
 Browser (React) ──────►│  /api/search ─► visual + text + keyword retrievers ─► RRF fusion ─► filters
                        │  /api/index*  ─► start / cancel / progress / problems                    │
                        │  /api/assets/* ─► thumbnail, original file (range requests), reveal     │
                        └──────────────────────────────────────────────────────────────────────────┘
```

## Components

| Module | Responsibility |
|---|---|
| `app/config.py` | All settings from `.env`; `PIPELINE_VERSION` for forced reprocessing |
| `app/db.py` | SQLite schema, per-thread connections (WAL mode), crash recovery on startup |
| `app/indexing/scanner.py` | Walks the folder, detects type, hashes, deduplicates, decides what to process |
| `app/indexing/pipeline.py` | Background run: model warm-up, per-asset processing, failure isolation, progress |
| `app/indexing/{image,video,pdf}.py` | Type-specific content extraction (no storage logic) |
| `app/ai/*` | Model wrappers: CLIP/BGE embeddings, Whisper, OCR, optional captions |
| `app/search/vector_store.py` | Qdrant collections, deterministic point ids, filtered search |
| `app/search/keyword.py` | BM25 over extracted text (SQLite FTS5) |
| `app/search/hybrid.py` | Query intent, three retrievers, Reciprocal Rank Fusion, filters, result shaping |
| `app/main.py` | REST API + serves the built frontend |
| `frontend/` | React UI: search, filters, preview modal, indexing dashboard |

## Data model

- **assets**: one row per unique content (SHA-256). Holds status, attempts, per-stage results, AI summary,
  thumbnail, technical metadata (resolution, duration, pages) and the pipeline version it was built with.
- **files**: one row per path on disk (filename, folder, extension, size, mtime, status). Several files can
  point at one asset. That's how duplicates in different folders are handled.
- **runs**: one row per indexing run with live counters (this is what the progress bar reads).
- **text_fts**: FTS5 index of every extracted text chunk, for BM25.
- **Qdrant `visual`** (512-d CLIP): one point per image, per kept video keyframe, and per rendered PDF page.
- **Qdrant `text`** (384-d BGE): one point per text chunk (OCR, PDF text, transcript window, caption).

Every vector carries `asset_id`, `file_type`, `kind` and a `locator` (video timestamp or PDF page), so
search can group hits back to assets and open the preview at the matching moment or page.

## Ingestion flow

1. **Scan.** Walk `MEDIA_DIR`. Unsupported extensions are recorded (not silently ignored).
2. **Change detection.** Same path, size and mtime as before, with the asset already done, means *unchanged*:
   skipped without re-hashing. That's why a re-run over thousands of files takes seconds.
3. **Hash.** SHA-256 of new or changed files. A known hash is a *duplicate*: the path is linked to the existing
   asset and no AI work is repeated.
4. **Removed files.** Paths not seen are marked missing. Assets with no remaining path have their vectors
   and text deleted, so they drop out of search.
5. **Process** each queued asset (one DB commit per asset):
   - **Image:** decode and verify, EXIF-orient, CLIP embedding, optional OCR and caption.
   - **Video:** ffprobe, evenly spaced keyframes (every 5 s, 4–12 per video, mid-segment), near-duplicate
     frames dropped (cosine > 0.95), Whisper transcript of the first 3 minutes of audio in 30-second windows.
   - **PDF:** text layer per page (first 60 pages), OCR for pages without text (at most 4 per document), CLIP
     embedding of the first 3 page renders.
6. **Store.** Old vectors for the asset are deleted, then new ones written (idempotent). Status becomes `indexed`,
   or `partial` if an optional stage failed, or `failed` with the reason.

## Search flow

1. **Intent.** Words like "videos", "photos" or "brochures" give that type a soft boost (×1.5). Explicit UI filters
   are hard filters.
2. **Three retrievers** (top 300 candidates each, grouped to the best hit per asset):
   - *visual*: CLIP text embedding against image, frame and page vectors (hits below cosine 0.20 dropped)
   - *text*: BGE query embedding against text chunks (hits below cosine 0.60 dropped)
   - *keyword*: BM25 over the same text (stop-words and type words removed)
3. **Confidence-gated Reciprocal Rank Fusion:** `score = Σ confidence × weight / (60 + rank)`.
   - Rank handles the different score scales across models.
   - The confidence (0–1, a ramp over the raw score: CLIP 0.20→0.27, BGE 0.60→0.72) stops a weak #1 in one list
     from counting as much as a strong #1 in another.
   - BM25 only boosts assets that a semantic signal already found.
   - The evaluation motivated this: plain RRF gave P@5 0.41, gated fusion gives 0.89. See `evaluation/README.md`.
4. **Filters** (type, folder, size, duration) and removal of assets with no file left on disk.
5. **Relative cut-off:** results scoring below 30% of the best remaining result are dropped, so a query with few
   true matches doesn't trail off into dozens of weak look-alikes.
6. **Response** per asset: why it matched (each signal's rank, score and snippet), where to jump
   (timestamp or page), all duplicate paths.

## Reliability

| Concern | How it's handled |
|---|---|
| Large folders | Background thread, per-asset commits, live progress, cancel button, unchanged files skipped without hashing |
| Crash or restart mid-run | On startup, `processing` assets go back to `pending` and the unfinished run is marked interrupted. The next run continues |
| Corrupted files | Each processor validates first (PIL verify, ffprobe, PyMuPDF open). The failure reason is stored and shown in the UI |
| Unsupported files | Recorded with status `unsupported` and a reason, never processed |
| Duplicates | Content hash. One asset, many paths, processed once |
| AI or model failure | Models load before processing (fail fast, clear message). Optional stages fail softly (`partial`). Failed assets are retried on later runs up to `MAX_ATTEMPTS` |
| Changed processing logic | Bump `PIPELINE_VERSION` and only outdated assets are reprocessed |

## How to modify common things

| Change | Where |
|---|---|
| Support a new file type (e.g. `.docx`) | Add a processor that returns `Extracted` (see `indexing/pdf.py`), register its extensions in `indexing/types.py` and the function in `PROCESSORS` in `indexing/pipeline.py` |
| Swap the visual model (e.g. SigLIP) | `CLIP_MODEL` in `.env` + `clip_dim()` in `ai/embeddings.py`. Delete `storage/` (vector size changes) or bump `PIPELINE_VERSION` |
| Sample video frames differently | `.env` `VIDEO_*` settings, or `frame_timestamps()` in `indexing/video.py` |
| Tune ranking | `WEIGHTS`, `CONFIDENCE_RANGE`, `INTENT_BOOST` at the top of `search/hybrid.py`, then re-run `scripts/evaluate.py` |
| Add a filter (e.g. date) | Query param in `main.py` → `_passes_filters()` in `search/hybrid.py` → a control in `SearchView.jsx` |
| Re-process everything after a logic change | Bump `PIPELINE_VERSION` in `config.py`; the next run reprocesses only outdated assets |
| Enable AI captions | `.env`: `CAPTION_PROVIDER=groq`, `GROQ_API_KEY=…` |

## Key design decisions

- **CLIP for visual content.** Text and images share one vector space, so no labels or captions are needed to
  match "a woman with a cat". It runs on CPU at roughly 10 images per second.
- **A second, text-only embedding model (BGE).** CLIP's text encoder is limited to 77 tokens and was trained on
  short captions. BGE handles paragraphs of PDF text and transcripts much better.
- **Keyframes instead of every frame.** Cost per video is bounded (≤ 12 embeddings), and frames are spread
  evenly so long videos are still covered. Timestamps are kept for jump-to-moment previews.
- **Whisper for audio.** Some queries (testimonials, interviews) are about what is *said*, not what is shown.
- **Qdrant embedded.** Real vector DB features (payload filters, deletes, persistence) with zero infrastructure.
  Switching to a Qdrant server is a one-line change.
- **SQLite as the source of truth.** Transactions, crash safety and FTS5 in the standard library, with a single file.
- **Gated RRF fusion.** Scores from different models aren't comparable, but ranks are. Each rank is then weighted
  by how confident that signal is, measured rather than assumed.
- **Captions optional.** Free API tiers are rate-limited, and local vision-language models are too slow for a
  CPU-only laptop. The system is fully functional without them.
