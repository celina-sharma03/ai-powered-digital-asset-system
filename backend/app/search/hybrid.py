"""Hybrid search: three retrievers fused with confidence-gated Reciprocal Rank Fusion.

(Summary: rank within each signal, scaled by that signal's confidence; an asset's score is its
best signal plus half of the others; results below 30% of the top score are hidden.)

  visual   CLIP text-embedding of the query vs image / keyframe / page-render vectors
           -> "a woman standing with a cat", "modern living room"
  text     BGE query-embedding vs OCR / PDF text / transcript / caption chunks
           -> "brochures related to residential projects", spoken testimonials
  keyword  BM25 over the same extracted text
           -> exact words and numbers

Why RRF: the three scores live on different scales (CLIP cosine ~0.2-0.35, BGE cosine
~0.5-0.9, BM25 unbounded), so adding raw scores would be meaningless. RRF uses each
result's RANK in each list: score = sum(w / (k + rank)).

Why "confidence-gated": plain RRF gives the #1 hit of every list the same credit, even
when that list has no good match. The evaluation showed the problem: for "a dog playing
outside", the best text hit was an unrelated PDF (cosine 0.64) and it tied with the best
dog photo (CLIP 0.31). Images carry no text, so the text list only held noise. Each
signal's RRF term is now multiplied by a 0..1 confidence from its raw score (a linear
ramp between a "noise" and a "clearly relevant" level measured on this data). BM25 is
supporting evidence only: it can raise assets that another signal found, but can't add
new ones, because shared words alone ("playing", "kitchen") are weak evidence.

Why "best + partial support": a plain sum favours PDFs, which can match through three
signals while a photo can only match visually. An asset's score is its strongest signal
plus SUPPORT_WEIGHT x the others, so one excellent match beats several mediocre ones.
The evaluation compares plain and gated fusion (evaluation/README.md).

Query intent: words like "videos", "photos" or "brochures" give a soft boost to that
file type. It's a boost rather than a filter, so a strong match of another type can
still appear. Explicit UI filters are hard filters.
"""

import re
from collections import defaultdict

from ..ai import embeddings
from ..db import connect
from . import vector_store
from .keyword import keyword_search

RRF_K = 60
WEIGHTS = {"visual": 1.0, "text": 1.0, "keyword": 0.6}
# (noise, clearly-relevant) cosine levels per embedding signal, read off the score
# distributions of relevant vs unrelated results. Below `noise` a hit is dropped;
# between the two, its RRF contribution ramps up linearly.
CONFIDENCE_RANGE = {"visual": (0.20, 0.27), "text": (0.60, 0.72)}
CANDIDATES = 300
INTENT_BOOST = 1.5
# An asset's score = its strongest signal + this share of its other signals. 1.0 would be a
# plain sum (favours PDFs, which can match on 3 signals while photos match on 1); 0 ignores
# supporting evidence (hurts documents). 0.5 gave the best overall evaluation results.
SUPPORT_WEIGHT = 0.5
# Hide results scoring below this share of the best result. Without it, a query with only a
# few true matches ("flowers": 2 photos in the library) lists dozens of weak look-alikes
# after them. 0.3 was the strictest level that lost no relevant result in the evaluation.
MIN_RELATIVE_SCORE = 0.3

INTENT_WORDS = {
    "video": r"\b(videos?|clips?|footage|films?|recordings?)\b",
    "image": r"\b(images?|photos?|pictures?|pics?|photographs?|shots?)\b",
    "pdf": r"\b(brochures?|pdfs?|documents?|docs?|reports?|catalog(ue)?s?|flyers?|leaflets?|menus?)\b",
}


def detect_intent(query: str) -> set[str]:
    q = query.lower()
    return {ftype for ftype, pattern in INTENT_WORDS.items() if re.search(pattern, q)}


def _group_best(points, min_score: float) -> list[dict]:
    """Collapse many points (frames, pages, chunks) to one entry per asset: its best hit."""
    best: dict[int, dict] = {}
    for p in points:
        if p.score < min_score:
            continue
        aid = p.payload["asset_id"]
        if aid not in best or p.score > best[aid]["score"]:
            best[aid] = {"asset_id": aid, "score": float(p.score), "kind": p.payload.get("kind"),
                         "locator": p.payload.get("locator"), "snippet": p.payload.get("text")}
    return sorted(best.values(), key=lambda x: x["score"], reverse=True)


def retrieve(query: str, mode: str, file_types: list[str] | None) -> dict[str, list[dict]]:
    lists: dict[str, list[dict]] = {}
    if mode in ("hybrid", "hybrid_plain", "visual"):
        vec = embeddings.embed_clip_text(query)
        lists["visual"] = _group_best(vector_store.search(vector_store.VISUAL, vec, CANDIDATES, file_types),
                                      CONFIDENCE_RANGE["visual"][0])
    if mode in ("hybrid", "hybrid_plain", "text"):
        vec = embeddings.embed_query_text(query)
        lists["text"] = _group_best(vector_store.search(vector_store.TEXT, vec, CANDIDATES, file_types),
                                    CONFIDENCE_RANGE["text"][0])
    if mode in ("hybrid", "hybrid_plain", "keyword"):
        seen, kw = set(), []
        for hit in keyword_search(query, CANDIDATES):
            if hit["asset_id"] not in seen:          # already sorted best-first
                seen.add(hit["asset_id"])
                kw.append({**hit, "kind": hit["source"]})
        lists["keyword"] = kw
    return lists


def confidence(signal: str, score: float) -> float:
    if signal not in CONFIDENCE_RANGE:
        return 1.0
    low, high = CONFIDENCE_RANGE[signal]
    return min(1.0, max(0.0, (score - low) / (high - low)))


def fuse(lists: dict[str, list[dict]], intent: set[str], types_by_asset: dict[int, str],
         gated: bool = True) -> list[dict]:
    fused: dict[int, dict] = defaultdict(lambda: {"score": 0.0, "parts": [], "matches": {}})
    primary = [s for s in lists if s != "keyword"]
    for signal in primary + (["keyword"] if "keyword" in lists else []):
        for rank, hit in enumerate(lists[signal], start=1):
            aid = hit["asset_id"]
            if gated and signal == "keyword" and primary and aid not in fused:
                continue   # keyword only supports assets another signal already found
            conf = confidence(signal, hit["score"]) if gated else 1.0
            entry = fused[aid]
            entry["parts"].append(conf * WEIGHTS[signal] / (RRF_K + rank))
            entry["matches"][signal] = {**hit, "rank": rank}
    # Best signal counts fully, the others as a partial bonus. A plain sum let a PDF with
    # several mediocre matches ("flowers" mentioned in its text) outrank a photo with one
    # excellent visual match, since photos have no text signals to add up.
    support = SUPPORT_WEIGHT if gated else 1.0      # hybrid_plain keeps the plain sum
    for aid, entry in fused.items():
        best = max(entry["parts"])
        entry["score"] = best + support * (sum(entry.pop("parts")) - best)
        if intent and types_by_asset.get(aid) in intent:
            entry["score"] *= INTENT_BOOST
    return sorted(({"asset_id": aid, **e} for aid, e in fused.items() if e["score"] > 0),
                  key=lambda x: x["score"], reverse=True)


def search(query: str, *, mode: str = "hybrid", file_types: list[str] | None = None,
           folder: str | None = None, min_size: int | None = None, max_size: int | None = None,
           min_duration: float | None = None, max_duration: float | None = None,
           limit: int = 24) -> dict:
    query = query.strip()
    intent = detect_intent(query) if not file_types else set()
    lists = retrieve(query, mode, file_types)

    candidate_ids = {h["asset_id"] for hits in lists.values() for h in hits}
    assets = _load_assets(candidate_ids)
    ranked = fuse(lists, intent, {aid: a["file_type"] for aid, a in assets.items()},
                  gated=(mode != "hybrid_plain"))   # hybrid_plain = ungated RRF, kept for evaluation

    results, top_score = [], None
    for r in ranked:
        a = assets.get(r["asset_id"])
        if a is None or not _passes_filters(a, file_types, folder, min_size, max_size, min_duration, max_duration):
            continue
        top_score = top_score or r["score"]           # best score among results that pass the filters
        if r["score"] < MIN_RELATIVE_SCORE * top_score:
            break                                      # ranked best-first, so everything after is weaker
        results.append(_to_result(a, r))
        if len(results) >= limit:
            break
    return {"query": query, "mode": mode, "intent": sorted(intent), "total_candidates": len(ranked),
            "results": results}


# ---------------------------------------------------------------------- helpers

def _load_assets(ids: set[int]) -> dict[int, dict]:
    if not ids:
        return {}
    conn = connect()
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM assets WHERE id IN ({marks}) AND status IN ('indexed','partial')",
                        tuple(ids)).fetchall()
    assets = {r["id"]: dict(r) for r in rows}
    for f in conn.execute(f"SELECT * FROM files WHERE asset_id IN ({marks}) AND status='ok' ORDER BY id",
                          tuple(ids)):
        if f["asset_id"] in assets:
            assets[f["asset_id"]].setdefault("files", []).append(dict(f))
    return {aid: a for aid, a in assets.items() if a.get("files")}   # drop assets with no file left on disk


def _passes_filters(a, file_types, folder, min_size, max_size, min_duration, max_duration) -> bool:
    if file_types and a["file_type"] not in file_types:
        return False
    if folder is not None and not any(f["folder"] == folder or f["folder"].startswith(folder + "/")
                                      for f in a["files"]):
        return False
    if min_size is not None and a["size_bytes"] < min_size:
        return False
    if max_size is not None and a["size_bytes"] > max_size:
        return False
    if min_duration is not None and (a["duration_sec"] or 0) < min_duration:
        return False
    if max_duration is not None and a["duration_sec"] is not None and a["duration_sec"] > max_duration:
        return False
    return True


def _to_result(a: dict, r: dict) -> dict:
    matches = r["matches"]
    # Where to open the preview: the locator of the strongest signal (timestamp or page).
    best_signal = min(matches, key=lambda s: matches[s]["rank"])
    primary = a["files"][0]
    return {
        "asset_id": a["id"],
        "score": round(r["score"], 5),
        "file_type": a["file_type"],
        "filename": primary["filename"],
        "path": primary["path"],
        "folder": primary["folder"],
        "duplicates": [f["path"] for f in a["files"][1:]],
        "size_bytes": a["size_bytes"],
        "width": a["width"], "height": a["height"],
        "duration_sec": a["duration_sec"], "page_count": a["page_count"],
        "title": a["title"], "summary": a["summary"],
        "status": a["status"],
        "thumbnail_url": f"/api/assets/{a['id']}/thumbnail" if a["thumbnail"] else None,
        "content_url": f"/api/assets/{a['id']}/content",
        "jump_to": {"kind": matches[best_signal].get("kind"), "locator": matches[best_signal].get("locator")},
        "matches": {
            s: {"rank": m["rank"], "score": round(m["score"], 4), "kind": m.get("kind"),
                "locator": m.get("locator"), "snippet": (m.get("snippet") or "")[:240] or None}
            for s, m in matches.items()
        },
    }
