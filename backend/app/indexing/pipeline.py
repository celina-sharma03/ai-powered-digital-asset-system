"""Indexing pipeline: scan -> process each asset -> embed -> store.

Reliability decisions:
  * Runs in ONE background thread inside the API process (embedded Qdrant allows a single
    process). The API stays responsive, and the UI polls progress from the `runs` table.
  * Models are loaded before processing starts. If a model can't load (e.g. no internet on
    the first download), the run fails fast with a clear error instead of marking
    thousands of files as failed.
  * Each asset is committed on its own. A crash loses at most the file in progress, which
    goes back to 'pending' on restart (db.init_db).
  * A failing file is isolated: the error is stored on the asset, the run continues, and
    the file is retried on later runs up to MAX_ATTEMPTS.
  * Optional AI stages (OCR, transcript, caption) can fail without failing the asset:
    it becomes 'partial', stays searchable through what did work, and is retried later.
  * Vector point ids are deterministic, so re-processing is idempotent.
"""

import json
import logging
import threading
import time
import traceback
from pathlib import Path

import numpy as np
from qdrant_client import models

from ..ai import embeddings
from ..config import PIPELINE_VERSION, settings
from ..db import connect, transaction
from ..search import vector_store
from .image import process_image
from .pdf import process_pdf
from .scanner import scan
from .types import CaptionBudget, Extracted, ProcessingError
from .video import process_video

log = logging.getLogger("dam.indexer")

PROCESSORS = {"image": process_image, "video": process_video, "pdf": process_pdf}
FRAME_DEDUPE_THRESHOLD = 0.95   # cosine similarity above which two keyframes count as the same shot


class Indexer:
    def __init__(self):
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, full: bool = False) -> int:
        with self._lock:
            if self.is_running():
                raise RuntimeError("an indexing run is already in progress")
            self._cancel.clear()
            with transaction() as conn:
                run_id = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'scanning')",
                                      (time.time(),)).lastrowid
            self._thread = threading.Thread(target=self._run, args=(run_id, full), daemon=True, name="indexer")
            self._thread.start()
            return run_id

    def cancel(self) -> None:
        self._cancel.set()

    # ------------------------------------------------------------------ run
    def _update_run(self, run_id: int, **fields) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with transaction() as conn:
            conn.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))

    def _run(self, run_id: int, full: bool) -> None:
        try:
            asset_ids = scan(run_id, full, on_progress=lambda **kw: self._update_run(run_id, **kw))

            if asset_ids:
                self._update_run(run_id, status="processing", phase_detail="loading AI models")
                self._warmup_models()

            captions = CaptionBudget(settings.caption_max_per_run)
            processed = failed = 0
            for asset_id in asset_ids:
                if self._cancel.is_set():
                    self._update_run(run_id, status="cancelled", finished_at=time.time(), current_file=None)
                    return
                ok = self._process_asset(run_id, asset_id, captions)
                processed += 1
                failed += 0 if ok else 1
                self._update_run(run_id, processed=processed, failed=failed)

            self._update_run(run_id, status="completed", finished_at=time.time(),
                             current_file=None, phase_detail="done")
        except Exception as exc:
            log.exception("indexing run failed")
            self._update_run(run_id, status="failed", finished_at=time.time(), error=str(exc), current_file=None)

    @staticmethod
    def _warmup_models() -> None:
        try:
            embeddings.embed_clip_text("warmup")
            embeddings.embed_passages(["warmup"])
            vector_store.client()
        except Exception as exc:
            raise RuntimeError(f"AI model failed to load: {exc}") from exc

    # ------------------------------------------------------------------ one asset
    def _process_asset(self, run_id: int, asset_id: int, captions: CaptionBudget) -> bool:
        conn = connect()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        file_row = conn.execute("SELECT * FROM files WHERE asset_id=? AND status='ok' ORDER BY id LIMIT 1",
                                (asset_id,)).fetchone()
        if asset is None or file_row is None:
            return True

        path = Path(file_row["path"])
        self._update_run(run_id, current_file=file_row["rel_path"], phase_detail=f"processing {asset['file_type']}")
        with transaction() as c:
            c.execute("UPDATE assets SET status='processing', attempts=attempts+1 WHERE id=?", (asset_id,))

        started = time.time()
        try:
            extracted = PROCESSORS[asset["file_type"]](path, captions)
            self._store(asset, extracted)
            failed_stages = [k for k, v in extracted.stages.items() if str(v).startswith("failed")]
            status = "partial" if failed_stages else "indexed"
            error = "; ".join(f"{k}: {extracted.stages[k]}" for k in failed_stages) or None
            with transaction() as c:
                c.execute(
                    """UPDATE assets SET status=?, error=?, stages=?, width=?, height=?, duration_sec=?,
                       page_count=?, has_audio=?, title=?, summary=?, pipeline_version=?, indexed_at=?
                       WHERE id=?""",
                    (status, error, json.dumps(extracted.stages),
                     extracted.metadata.get("width"), extracted.metadata.get("height"),
                     extracted.metadata.get("duration_sec"), extracted.metadata.get("page_count"),
                     int(extracted.metadata["has_audio"]) if "has_audio" in extracted.metadata else None,
                     extracted.metadata.get("title"), extracted.summary or None,
                     PIPELINE_VERSION, time.time(), asset_id))
            log.info("indexed %s (%s) in %.1fs", file_row["rel_path"], status, time.time() - started)
            return True
        except ProcessingError as exc:
            # The file itself is bad (corrupted/encrypted). Retrying can't help, so use up the
            # attempts. If the file is replaced, it gets a new hash and is processed again.
            log.warning("failed %s: %s", file_row["rel_path"], exc)
            with transaction() as c:
                c.execute("UPDATE assets SET status='failed', error=?, attempts=? WHERE id=?",
                          (str(exc)[:500], settings.max_attempts, asset_id))
            return False
        except Exception as exc:
            # Unexpected/transient (model error, out of memory, ...): retried on later runs.
            log.warning("error %s: %s\n%s", file_row["rel_path"], exc, traceback.format_exc(limit=3))
            with transaction() as c:
                c.execute("UPDATE assets SET status='failed', error=? WHERE id=?",
                          (f"{type(exc).__name__}: {exc}"[:500], asset_id))
            return False

    def _store(self, asset, ex: Extracted) -> None:
        asset_id, digest, ftype = asset["id"], asset["content_hash"], asset["file_type"]
        base_payload = {"asset_id": asset_id, "file_type": ftype}

        # Visual vectors (CLIP)
        visual_points = []
        if ex.visuals:
            vecs = embeddings.embed_images([v.image for v in ex.visuals])
            keep = _dedupe(vecs) if ex.dedupe_visuals else range(len(vecs))
            for i in keep:
                v = ex.visuals[i]
                visual_points.append(models.PointStruct(
                    id=vector_store.point_id(digest, f"{v.kind}:{v.locator}"), vector=vecs[i].tolist(),
                    payload={**base_payload, "kind": v.kind, "locator": v.locator}))

        # Text vectors (BGE)
        texts = [t for t in ex.texts if t.text.strip()]
        text_points = []
        if texts:
            vecs = embeddings.embed_passages([t.text for t in texts])
            for i, t in enumerate(texts):
                text_points.append(models.PointStruct(
                    id=vector_store.point_id(digest, f"{t.source}:{t.locator}:{i}"), vector=vecs[i].tolist(),
                    payload={**base_payload, "kind": t.source, "locator": t.locator, "text": t.text[:600]}))

        # Replace old data for this asset (frame counts etc. may have changed), then write.
        vector_store.delete_asset(asset_id)
        vector_store.upsert(vector_store.VISUAL, visual_points)
        vector_store.upsert(vector_store.TEXT, text_points)
        with transaction() as c:
            c.execute("DELETE FROM text_fts WHERE asset_id=?", (asset_id,))
            c.executemany("INSERT INTO text_fts (content, asset_id, source, locator) VALUES (?,?,?,?)",
                          [(t.text, asset_id, t.source, t.locator) for t in texts])

        if ex.thumbnail is not None:
            settings.thumbs_dir.mkdir(parents=True, exist_ok=True)
            name = f"{digest[:24]}.jpg"
            thumb = ex.thumbnail.copy()
            thumb.thumbnail((480, 480))
            thumb.convert("RGB").save(settings.thumbs_dir / name, "JPEG", quality=82)
            with transaction() as c:
                c.execute("UPDATE assets SET thumbnail=? WHERE id=?", (name, asset_id))


def _dedupe(vecs: np.ndarray) -> list[int]:
    """Keep a frame only if it differs enough from every frame kept so far."""
    kept: list[int] = []
    for i, v in enumerate(vecs):
        if all(float(v @ vecs[j]) < FRAME_DEDUPE_THRESHOLD for j in kept):
            kept.append(i)
    return kept


indexer = Indexer()
