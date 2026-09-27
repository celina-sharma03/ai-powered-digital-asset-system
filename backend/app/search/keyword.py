"""BM25 keyword search over extracted text, using SQLite FTS5 (built into Python's sqlite3).

This catches exact words that embeddings can blur, such as names, numbers or
brochure wording ("3 BHK", "Phase II"). It only searches text found inside the
files (OCR, PDF text, transcripts, captions), never filenames or folder names.
"""

import re

from ..db import connect

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "for", "with", "and", "or", "is", "are",
    "showing", "show", "containing", "related", "find", "me", "some", "any", "that", "this",
    "images", "image", "photos", "photo", "pictures", "picture", "videos", "video", "clips",
    "documents", "document", "files", "file",
}


def build_fts_query(query: str) -> str | None:
    """Turn free text into a safe FTS5 query: quoted terms OR-ed together (BM25 ranks
    documents matching more terms higher)."""
    terms = [t for t in re.findall(r"[\w]+", query.lower()) if t not in STOPWORDS and len(t) > 1]
    if not terms:
        return None
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))


def keyword_search(query: str, limit: int = 200) -> list[dict]:
    fts = build_fts_query(query)
    if not fts:
        return []
    rows = connect().execute(
        """
        SELECT asset_id, source, locator, bm25(text_fts) AS score,
               snippet(text_fts, 0, '[', ']', '…', 16) AS snippet
        FROM text_fts WHERE text_fts MATCH ?
        ORDER BY score LIMIT ?
        """,
        (fts, limit),
    ).fetchall()
    # bm25() is "lower is better"; flip the sign so higher = better like the other signals.
    return [{"asset_id": int(r["asset_id"]), "score": -r["score"], "source": r["source"],
             "locator": r["locator"], "snippet": r["snippet"]} for r in rows]
