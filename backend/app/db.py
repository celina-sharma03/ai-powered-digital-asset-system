"""SQLite storage: asset metadata, file locations, indexing runs and a full-text index.

Data model
  assets   one row per unique *content* (SHA-256). Holds AI/processing results.
  files    one row per *path* on disk. Many files can point at one asset (duplicates).
  runs     one row per indexing run (progress + counters for the UI).
  text_fts FTS5 full-text index over extracted text (OCR, PDF text, transcripts,
           captions) used for BM25 keyword search.
"""

import sqlite3
import threading
import time
from contextlib import contextmanager

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id               INTEGER PRIMARY KEY,
    content_hash     TEXT NOT NULL UNIQUE,
    file_type        TEXT NOT NULL,              -- image | video | pdf
    size_bytes       INTEGER NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending',  -- pending|processing|indexed|partial|failed
    error            TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    stages           TEXT,                       -- JSON: {"clip": "ok", "ocr": "failed: ..."}
    width            INTEGER,
    height           INTEGER,
    duration_sec     REAL,
    page_count       INTEGER,
    has_audio        INTEGER,
    title            TEXT,                       -- PDF title / first line
    summary          TEXT,                       -- short text shown in results (caption, transcript, pdf text)
    thumbnail        TEXT,                       -- file name inside storage/thumbnails
    pipeline_version INTEGER NOT NULL DEFAULT 0,
    created_at       REAL NOT NULL,
    indexed_at       REAL
);

CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    path        TEXT NOT NULL UNIQUE,            -- absolute path
    rel_path    TEXT NOT NULL,                   -- relative to MEDIA_DIR
    folder      TEXT NOT NULL,                   -- relative folder ('' = root)
    filename    TEXT NOT NULL,
    ext         TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    mtime       REAL NOT NULL,
    asset_id    INTEGER REFERENCES assets(id),
    status      TEXT NOT NULL,                   -- ok | unsupported | error | missing
    error       TEXT,
    last_seen   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_asset ON files(asset_id);
CREATE INDEX IF NOT EXISTS idx_assets_status ON assets(status);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    status        TEXT NOT NULL,                 -- scanning | processing | completed | failed | cancelled
    phase_detail  TEXT,
    files_seen    INTEGER DEFAULT 0,
    unchanged     INTEGER DEFAULT 0,
    new_assets    INTEGER DEFAULT 0,
    duplicates    INTEGER DEFAULT 0,
    unsupported   INTEGER DEFAULT 0,
    removed       INTEGER DEFAULT 0,
    to_process    INTEGER DEFAULT 0,
    processed     INTEGER DEFAULT 0,
    failed        INTEGER DEFAULT 0,
    current_file  TEXT,
    error         TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(
    content,
    asset_id UNINDEXED,
    source   UNINDEXED,                          -- ocr | pdf_text | transcript | caption
    locator  UNINDEXED,                          -- page number / timestamp
    tokenize = 'porter unicode61'
);
"""

_local = threading.local()


def connect() -> sqlite3.Connection:
    """One connection per thread (the indexer runs in a background thread)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")    # readers don't block the indexer's writes
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    # Crash recovery: anything left mid-processing by a killed process goes back to the queue,
    # and a run that never finished is marked as interrupted.
    conn.execute("UPDATE assets SET status='pending' WHERE status='processing'")
    conn.execute(
        "UPDATE runs SET status='failed', error='interrupted (server stopped)', finished_at=? "
        "WHERE status IN ('scanning','processing')",
        (time.time(),),
    )
    conn.commit()
