"""FastAPI application: search, asset preview/download, indexing control and status."""

import json
import logging
import platform
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import REPO_ROOT, settings
from .db import connect, init_db
from .indexing.pipeline import indexer
from .search import hybrid, vector_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    init_db()
    vector_store.client()
    yield
    indexer.cancel()
    vector_store.close()


app = FastAPI(title="AI Digital Asset Manager", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])


# ------------------------------------------------------------------ search

@app.get("/api/search")
def search(
    q: str = Query(..., min_length=1, max_length=300),
    type: list[str] | None = Query(None, description="image | video | pdf (repeatable)"),
    folder: str | None = None,
    min_size: int | None = None,
    max_size: int | None = None,
    min_duration: float | None = None,
    max_duration: float | None = None,
    mode: str = Query("hybrid", pattern="^(hybrid|visual|text|keyword)$"),
    limit: int = Query(24, ge=1, le=100),
):
    return hybrid.search(q, mode=mode, file_types=type, folder=folder, min_size=min_size, max_size=max_size,
                         min_duration=min_duration, max_duration=max_duration, limit=limit)


# ------------------------------------------------------------------ assets

def _asset_or_404(asset_id: int) -> dict:
    row = connect().execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "asset not found")
    return dict(row)


def _live_file(asset_id: int) -> dict:
    row = connect().execute("SELECT * FROM files WHERE asset_id=? AND status='ok' ORDER BY id LIMIT 1",
                            (asset_id,)).fetchone()
    if row is None or not Path(row["path"]).exists():
        raise HTTPException(404, "original file is no longer on disk")
    return dict(row)


@app.get("/api/assets/{asset_id}")
def asset_detail(asset_id: int):
    asset = _asset_or_404(asset_id)
    asset["stages"] = json.loads(asset["stages"]) if asset["stages"] else {}
    asset["files"] = [dict(r) for r in connect().execute(
        "SELECT path, rel_path, folder, filename, status, mtime FROM files WHERE asset_id=?", (asset_id,))]
    return asset


@app.get("/api/assets/{asset_id}/thumbnail")
def asset_thumbnail(asset_id: int):
    asset = _asset_or_404(asset_id)
    if not asset["thumbnail"]:
        raise HTTPException(404, "no thumbnail")
    return FileResponse(settings.thumbs_dir / asset["thumbnail"], media_type="image/jpeg")


@app.get("/api/assets/{asset_id}/content")
def asset_content(asset_id: int):
    """Streams the original file (supports HTTP range requests, so videos can seek)."""
    f = _live_file(asset_id)
    return FileResponse(f["path"], filename=f["filename"], content_disposition_type="inline")


@app.post("/api/assets/{asset_id}/reveal")
def reveal_in_folder(asset_id: int):
    """Open the OS file manager with the original file selected (the app runs locally)."""
    path = _live_file(asset_id)["path"]
    system = platform.system()
    if system == "Windows":
        subprocess.Popen(["explorer", "/select,", path])
    elif system == "Darwin":
        subprocess.Popen(["open", "-R", path])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent)])
    return {"opened": path}


# ------------------------------------------------------------------ indexing

class IndexRequest(BaseModel):
    full: bool = False   # True = ignore "unchanged" shortcuts and reprocess everything


@app.post("/api/index")
def start_index(req: IndexRequest | None = None):
    try:
        run_id = indexer.start(full=bool(req and req.full))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))
    return {"run_id": run_id}


@app.post("/api/index/cancel")
def cancel_index():
    indexer.cancel()
    return {"cancelling": indexer.is_running()}


@app.get("/api/index/status")
def index_status():
    conn = connect()
    run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    by_status = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) n FROM assets WHERE status<>'removed' GROUP BY status")}
    file_status = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM files GROUP BY status")}
    return {"running": indexer.is_running(), "run": dict(run) if run else None,
            "assets_by_status": by_status, "files_by_status": file_status}


@app.get("/api/index/issues")
def index_issues(limit: int = Query(300, le=2000)):
    """Everything that didn't index cleanly, with the reason."""
    conn = connect()
    files = [dict(r) for r in conn.execute(
        "SELECT rel_path, status, error FROM files WHERE status IN ('unsupported','error') ORDER BY rel_path LIMIT ?",
        (limit,))]
    assets = [dict(r) for r in conn.execute(
        """SELECT a.id, a.file_type, a.status, a.error, a.attempts,
                  (SELECT rel_path FROM files f WHERE f.asset_id=a.id ORDER BY id LIMIT 1) AS rel_path
           FROM assets a WHERE a.status IN ('failed','partial') ORDER BY a.status, rel_path LIMIT ?""", (limit,))]
    return {"files": files, "assets": assets}


# ------------------------------------------------------------------ stats

@app.get("/api/stats")
def stats():
    conn = connect()
    by_type = [dict(r) for r in conn.execute(
        """SELECT file_type, COUNT(*) AS count, SUM(size_bytes) AS bytes FROM assets
           WHERE status IN ('indexed','partial') GROUP BY file_type""")]
    folders = [r["folder"] for r in conn.execute(
        "SELECT DISTINCT folder FROM files WHERE status='ok' ORDER BY folder")]
    dupes = conn.execute(
        "SELECT COUNT(*) FROM (SELECT asset_id FROM files WHERE status='ok' AND asset_id IS NOT NULL "
        "GROUP BY asset_id HAVING COUNT(*) > 1)").fetchone()[0]
    return {
        "media_dir": str(settings.media_dir),
        "by_type": by_type,
        "folders": folders,
        "assets_with_duplicates": dupes,
        "vectors": {"visual": vector_store.count(vector_store.VISUAL), "text": vector_store.count(vector_store.TEXT)},
    }


# ------------------------------------------------------------------ frontend (production build)

if FRONTEND_DIST.exists():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
