"""Qdrant vector store in embedded ("local") mode: persisted to disk, no server needed.

Two collections, because the two embedding models produce different vector spaces:
  visual (512-d, CLIP)  points = images, video keyframes, PDF page renders
  text   (384-d, BGE)   points = chunks of OCR, PDF text, transcripts, captions

Every point's payload carries `asset_id` and `file_type`, so results can be filtered
by type and grouped back to their asset. Point IDs are deterministic (uuid5 of
content hash + part), so re-processing an asset overwrites instead of duplicating.

Production note: the same code works against a Qdrant server (QdrantClient(url=...)),
which adds HNSW indexing and horizontal scaling. Embedded mode does exact search,
which is fine up to tens of thousands of points.
"""

import threading
import uuid

from qdrant_client import QdrantClient, models

from ..ai.embeddings import clip_dim, text_dim
from ..config import settings

VISUAL = "visual"
TEXT = "text"

_lock = threading.RLock()     # embedded Qdrant is not thread-safe; serialize access
_client: QdrantClient | None = None


def client() -> QdrantClient:
    global _client
    with _lock:
        if _client is None:
            settings.qdrant_path.mkdir(parents=True, exist_ok=True)
            _client = QdrantClient(path=str(settings.qdrant_path))
            for name, dim in ((VISUAL, clip_dim()), (TEXT, text_dim())):
                if not _client.collection_exists(name):
                    _client.create_collection(
                        name, vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE))
        return _client


def close() -> None:
    global _client
    with _lock:
        if _client is not None:
            _client.close()
            _client = None


def point_id(content_hash: str, part: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{content_hash}:{part}"))


def upsert(collection: str, points: list[models.PointStruct]) -> None:
    if not points:
        return
    with _lock:
        client().upsert(collection, points=points, wait=True)


def delete_asset(asset_id: int) -> None:
    flt = models.FilterSelector(filter=models.Filter(must=[
        models.FieldCondition(key="asset_id", match=models.MatchValue(value=asset_id))]))
    with _lock:
        for name in (VISUAL, TEXT):
            client().delete(name, points_selector=flt, wait=True)


def search(collection: str, vector, limit: int, file_types: list[str] | None = None):
    query_filter = None
    if file_types:
        query_filter = models.Filter(must=[
            models.FieldCondition(key="file_type", match=models.MatchAny(any=file_types))])
    with _lock:
        return client().query_points(
            collection, query=list(map(float, vector)), limit=limit,
            query_filter=query_filter, with_payload=True,
        ).points


def count(collection: str) -> int:
    with _lock:
        return client().count(collection).count
