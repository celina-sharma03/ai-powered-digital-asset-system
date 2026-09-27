"""Print the contents of the index database in a readable form (for learning/debugging).

Shows the two core tables side by side:
  files   one row per PATH on disk     -> which asset it points to
  assets  one row per unique CONTENT   -> AI processing status

Usage (from repo root):  backend/.venv/Scripts/python backend/scripts/inspect_db.py
Uses the same STORAGE_DIR as the app (.env or environment variable).
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings  # noqa: E402

LIMIT = 30


def main() -> None:
    if not settings.db_path.exists():
        sys.exit(f"No database yet at {settings.db_path} (run an indexing pass first).")
    conn = sqlite3.connect(settings.db_path)

    print(f"Database: {settings.db_path}\n")
    print("FILES  (one row per path on disk)")
    print(f"  {'id':>4}  {'path':<52} {'status':<12} -> asset_id")
    for fid, rel, status, aid in conn.execute(
            "SELECT id, rel_path, status, asset_id FROM files ORDER BY id LIMIT ?", (LIMIT,)):
        print(f"  {fid:>4}  {rel[:52]:<52} {status:<12} -> {aid if aid is not None else '-'}")

    print("\nASSETS (one row per unique content)")
    print(f"  {'id':>4}  {'type':<6} {'content hash':<14} {'status':<9} {'files':>5}  details")
    for aid, ftype, digest, status, error, nfiles in conn.execute(
            """SELECT a.id, a.file_type, a.content_hash, a.status, a.error,
                      (SELECT COUNT(*) FROM files f WHERE f.asset_id = a.id AND f.status = 'ok')
               FROM assets a ORDER BY a.id LIMIT ?""", (LIMIT,)):
        print(f"  {aid:>4}  {ftype:<6} {digest[:12]}.. {status:<9} {nfiles:>5}  {error or ''}")

    run = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    if run:
        cols = [d[0] for d in conn.execute("SELECT * FROM runs LIMIT 0").description]
        r = dict(zip(cols, run))
        print(f"\nLAST RUN #{r['id']} ({r['status']}): files seen {r['files_seen']}, unchanged {r['unchanged']}, "
              f"new {r['new_assets']}, duplicates {r['duplicates']}, unsupported {r['unsupported']}, "
              f"removed {r['removed']}, processed {r['processed']}/{r['to_process']}, failed {r['failed']}")


if __name__ == "__main__":
    main()
