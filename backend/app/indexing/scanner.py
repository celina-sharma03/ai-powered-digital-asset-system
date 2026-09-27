"""Folder scanner: decides WHAT needs processing. It doesn't run any AI.

For every file under MEDIA_DIR:
  1. Unsupported extension         -> recorded as 'unsupported' (shown in the UI), never processed.
  2. Same path + size + mtime as last run and its asset is already done
                                   -> 'unchanged', skipped without even re-hashing (fast re-runs).
  3. Otherwise compute SHA-256 of the content:
       - hash already known        -> DUPLICATE: the path is linked to the existing asset, no AI work.
       - hash unknown              -> new asset, queued for processing.
After the walk:
  4. Paths not seen this run       -> 'missing'; assets with no remaining files get their
                                      vectors and text removed so they vanish from search.
"""

import hashlib
import os
import time
from pathlib import Path

from ..config import PIPELINE_VERSION, settings
from ..db import connect
from ..search import vector_store
from .types import file_type_for

DONE_STATES = ("indexed", "partial")


def sha256_of(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def needs_processing(asset, full: bool) -> bool:
    if full:
        return True
    if asset["status"] in ("pending", "removed"):
        return True
    if asset["status"] in ("failed", "partial"):
        return asset["attempts"] < settings.max_attempts   # retry AI/processing failures, but not forever
    return asset["pipeline_version"] < PIPELINE_VERSION    # logic changed -> reprocess


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            if not name.startswith(".") and not name.endswith(".part"):
                yield Path(dirpath) / name


def scan(run_id: int, full: bool, on_progress) -> list[int]:
    """Walk the media folder, update `files`/`assets`, return asset ids to process."""
    conn = connect()
    root = settings.media_dir
    if not root.is_dir():
        raise FileNotFoundError(f"MEDIA_DIR does not exist: {root}")

    started = time.time()
    counters = {"files_seen": 0, "unchanged": 0, "new_assets": 0, "duplicates": 0, "unsupported": 0}
    to_process: set[int] = set()
    if full:
        conn.execute("UPDATE assets SET attempts=0")

    for path in iter_files(root):
        counters["files_seen"] += 1
        rel = path.relative_to(root)
        try:
            st = path.stat()
        except OSError:
            continue
        common = {
            "path": str(path), "rel_path": rel.as_posix(),
            "folder": "" if rel.parent == Path(".") else rel.parent.as_posix(),
            "filename": path.name, "ext": path.suffix.lower(), "size_bytes": st.st_size,
            "mtime": st.st_mtime, "last_seen": started,
        }
        existing = conn.execute("SELECT * FROM files WHERE path=?", (str(path),)).fetchone()
        ftype = file_type_for(path.suffix)

        if ftype is None:
            counters["unsupported"] += 1
            _upsert_file(conn, existing, common, asset_id=None, status="unsupported",
                         error=f"unsupported file type '{path.suffix or '(none)'}'")
            continue

        # Fast path: unchanged file whose asset needs nothing -> skip hashing entirely.
        if existing and existing["asset_id"] and existing["size_bytes"] == st.st_size \
                and existing["mtime"] == st.st_mtime and existing["status"] == "ok":
            asset = conn.execute("SELECT * FROM assets WHERE id=?", (existing["asset_id"],)).fetchone()
            if asset and not needs_processing(asset, full):
                counters["unchanged"] += 1
                conn.execute("UPDATE files SET last_seen=? WHERE id=?", (started, existing["id"]))
                continue

        on_progress(phase_detail=f"hashing {rel.as_posix()}", **counters)
        try:
            digest = sha256_of(path)
        except OSError as exc:
            _upsert_file(conn, existing, common, asset_id=None, status="error", error=f"cannot read file: {exc}")
            continue

        asset = conn.execute("SELECT * FROM assets WHERE content_hash=?", (digest,)).fetchone()
        if asset is None:
            cur = conn.execute(
                "INSERT INTO assets (content_hash, file_type, size_bytes, status, created_at) VALUES (?,?,?,?,?)",
                (digest, ftype, st.st_size, "pending", started))
            asset_id = cur.lastrowid
            counters["new_assets"] += 1
            to_process.add(asset_id)
        else:
            asset_id = asset["id"]
            newly_linked = existing is None or existing["asset_id"] != asset_id
            if newly_linked and conn.execute(
                    "SELECT 1 FROM files WHERE asset_id=? AND path<>? AND status='ok'",
                    (asset_id, str(path))).fetchone():
                counters["duplicates"] += 1
            if needs_processing(asset, full):
                to_process.add(asset_id)

        _upsert_file(conn, existing, common, asset_id=asset_id, status="ok", error=None)
        if counters["files_seen"] % 200 == 0:
            conn.commit()

    conn.commit()
    counters["removed"] = _handle_missing(conn, started)

    # Assets still pending from an interrupted earlier run are picked up too.
    for row in conn.execute("SELECT id FROM assets WHERE status='pending'"):
        if conn.execute("SELECT 1 FROM files WHERE asset_id=? AND status='ok'", (row["id"],)).fetchone():
            to_process.add(row["id"])

    on_progress(phase_detail="scan complete", to_process=len(to_process), **counters)
    return sorted(to_process)


def _upsert_file(conn, existing, data: dict, asset_id, status: str, error) -> None:
    if existing:
        conn.execute(
            "UPDATE files SET rel_path=:rel_path, folder=:folder, filename=:filename, ext=:ext, "
            "size_bytes=:size_bytes, mtime=:mtime, asset_id=:asset_id, status=:status, error=:error, "
            "last_seen=:last_seen WHERE id=:id",
            {**data, "asset_id": asset_id, "status": status, "error": error, "id": existing["id"]})
    else:
        conn.execute(
            "INSERT INTO files (path, rel_path, folder, filename, ext, size_bytes, mtime, asset_id, status, error, last_seen) "
            "VALUES (:path, :rel_path, :folder, :filename, :ext, :size_bytes, :mtime, :asset_id, :status, :error, :last_seen)",
            {**data, "asset_id": asset_id, "status": status, "error": error})


def _handle_missing(conn, started: float) -> int:
    conn.execute("UPDATE files SET status='missing' WHERE last_seen < ? AND status <> 'missing'", (started,))
    orphans = conn.execute(
        "SELECT id FROM assets WHERE status <> 'removed' AND id NOT IN "
        "(SELECT asset_id FROM files WHERE status='ok' AND asset_id IS NOT NULL)").fetchall()
    for row in orphans:
        vector_store.delete_asset(row["id"])
        conn.execute("DELETE FROM text_fts WHERE asset_id=?", (row["id"],))
        conn.execute("UPDATE assets SET status='removed' WHERE id=?", (row["id"],))
    conn.commit()
    return len(orphans)
