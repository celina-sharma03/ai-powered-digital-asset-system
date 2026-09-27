"""Print a summary of the media folder (file counts and sizes by type) as Markdown.

Usage (from repo root):  backend/.venv/Scripts/python backend/scripts/dataset_summary.py
"""

import csv
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend"))
from app.indexing.types import file_type_for  # noqa: E402  (same type rules as the indexer)


def main() -> None:
    media_dir = Path(os.getenv("MEDIA_DIR", REPO_ROOT / "data" / "media"))
    counts, sizes = Counter(), defaultdict(int)
    for p in media_dir.rglob("*"):
        if p.is_file():
            t = file_type_for(p.suffix) or "unsupported"
            counts[t] += 1
            sizes[t] += p.stat().st_size

    total_n, total_b = sum(counts.values()), sum(sizes.values())
    print("| Type | Files | Size |\n|---|---|---|")
    for t in ("image", "video", "pdf", "unsupported"):
        print(f"| {t} | {counts[t]} | {sizes[t] / 1e9:.2f} GB |")
    print(f"| **total** | **{total_n}** | **{total_b / 1e9:.2f} GB** |\n")

    manifest = media_dir.parent / "manifest.csv"
    if manifest.exists():
        by_query = Counter()
        with open(manifest, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                by_query[(row["media_type"], row["source"], row["source_query"])] += 1
        print("| Type | Source | Source query | Files |\n|---|---|---|---|")
        for (mt, src, q), n in sorted(by_query.items()):
            print(f"| {mt} | {src} | {q} | {n} |")


if __name__ == "__main__":
    main()
