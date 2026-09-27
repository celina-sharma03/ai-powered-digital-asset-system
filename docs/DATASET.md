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

## Size limitation

The assignment suggests 5–10 GB; this dataset is 3.1 GB. The limit was processing time on a CPU-only laptop
(i3-1315U, 8 GB RAM) with a same-day deadline, not the design:

| Run | Files | Work | Time |
|---|---|---|---|
| First full index | 1,830 | 1,812 new assets | 39 min |
| Incremental, after adding PDFs (resumed after a cancel) | 2,026 | 125 new, 1,898 unchanged skipped | 17 min |
| Re-run with nothing changed | 2,026 | 0 | **1 s** |

Cost grows with the **number** of files and video minutes, not gigabytes: about 0.2 s per image, 3–25 s per
video, and 2–20 s per PDF on this CPU. A 10 GB collection of the same mix would take about 2–3 hours for the first
pass on this laptop, and seconds for later passes. See the README's *Production improvements* for scaling out
(GPU workers, a job queue, a Qdrant server).
