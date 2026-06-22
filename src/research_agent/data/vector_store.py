"""Vector store — abstract interface with two backends.

- LocalNumpyStore: flat .npy file + JSONL index (default)
- QdrantStore: Qdrant vector database (optional, feature-flagged)
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from research_agent.core.config import VectorStoreConfig


@dataclass
class SearchResult:
    id: str
    score: float
    metadata: Dict[str, Any]


class VectorStore(ABC):
    """Abstract vector store interface."""

    @abstractmethod
    def upsert(self, collection: str, vectors: np.ndarray,
               metadata: List[Dict[str, Any]]) -> int:
        """Batch-upsert vectors with metadata. Returns count written."""
        ...

    @abstractmethod
    def search(self, collection: str, query_vector: np.ndarray,
               top_k: int = 10, filters: Optional[Dict[str, Any]] = None) -> List[SearchResult]:
        """Semantic search returning top_k results."""
        ...

    @abstractmethod
    def delete_by_filter(self, collection: str, filters: Dict[str, Any]) -> int:
        """Delete vectors matching filter criteria. Returns count deleted."""
        ...

    @abstractmethod
    def collection_size(self, collection: str) -> int:
        """Return the number of vectors in a collection."""
        ...


# ── Local numpy store ───────────────────────────────────────

class LocalNumpyStore(VectorStore):
    """Flat .npy file + JSONL index vector store.

    Storage layout::

        {storage_dir}/
          {collection}/
            vectors.npy       # shape (N, dim)
            index.jsonl       # one JSON metadata object per line
    """

    def __init__(self, config: VectorStoreConfig) -> None:
        self._root = Path(config.storage_dir)

    def _collection_dir(self, collection: str) -> Path:
        path = self._root / collection
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _vectors_path(self, collection: str) -> Path:
        return self._collection_dir(collection) / "vectors.npy"

    def _index_path(self, collection: str) -> Path:
        return self._collection_dir(collection) / "index.jsonl"

    def upsert(self, collection: str, vectors: np.ndarray,
               metadata: List[Dict[str, Any]]) -> int:
        vec_path = self._vectors_path(collection)
        idx_path = self._index_path(collection)

        # Append vectors
        if vec_path.exists():
            existing = np.load(vec_path)
            combined = np.concatenate([existing, vectors], axis=0)
        else:
            combined = vectors
        np.save(vec_path, combined)

        # Append metadata lines
        with open(idx_path, "a", encoding="utf-8") as fh:
            for meta in metadata:
                fh.write(json.dumps(meta, ensure_ascii=False, default=str) + "\n")

        return len(metadata)

    def search(self, collection: str, query_vector: np.ndarray,
               top_k: int = 10, filters: Optional[Dict[str, Any]] = None) -> List[SearchResult]:
        vec_path = self._vectors_path(collection)
        idx_path = self._index_path(collection)
        if not vec_path.exists():
            return []

        vectors = np.load(vec_path)
        metadata = self._load_metadata(idx_path)

        # Cosine similarity
        query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-8)
        vecs_norm = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-8)
        scores = np.dot(vecs_norm, query_norm)

        top_indices = np.argsort(scores)[-top_k:][::-1]

        results = []
        for idx in top_indices:
            score = float(scores[idx])
            meta = metadata[idx] if idx < len(metadata) else {}
            # Optional filter
            if filters:
                match = all(meta.get(k) == v for k, v in filters.items())
                if not match:
                    continue
            results.append(SearchResult(id=meta.get("id", str(idx)), score=score, metadata=meta))
        return results[:top_k]

    def delete_by_filter(self, collection: str, filters: Dict[str, Any]) -> int:
        vec_path = self._vectors_path(collection)
        idx_path = self._index_path(collection)
        if not vec_path.exists():
            return 0

        vectors = np.load(vec_path)
        metadata = self._load_metadata(idx_path)

        keep_mask = np.ones(len(vectors), dtype=bool)
        deleted = 0
        for i, meta in enumerate(metadata):
            if all(meta.get(k) == v for k, v in filters.items()):
                keep_mask[i] = False
                deleted += 1

        if deleted > 0:
            new_vecs = vectors[keep_mask]
            new_meta = [m for i, m in enumerate(metadata) if keep_mask[i]]
            np.save(vec_path, new_vecs)
            with open(idx_path, "w", encoding="utf-8") as fh:
                for meta in new_meta:
                    fh.write(json.dumps(meta, ensure_ascii=False, default=str) + "\n")

        return deleted

    def collection_size(self, collection: str) -> int:
        vec_path = self._vectors_path(collection)
        if not vec_path.exists():
            return 0
        return np.load(vec_path).shape[0]

    @staticmethod
    def _load_metadata(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        results = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return results


# ── Qdrant store ────────────────────────────────────────────

class QdrantStore(VectorStore):
    """Qdrant vector database store.  Optional, feature-flagged."""

    def __init__(self, config: VectorStoreConfig, vector_dim: int = 64) -> None:
        self._config = config
        self._vector_dim = vector_dim
        self._client = None

    def _ensure_client(self) -> bool:
        if self._client is not None:
            return True
        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=self._config.qdrant_url)
            return True
        except Exception:
            self._client = None
            return False

    def _ensure_collection(self, collection: str) -> None:
        if not self._client:
            return
        from qdrant_client.models import Distance, VectorParams
        collections = [c.name for c in self._client.get_collections().collections]
        if collection not in collections:
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=self._vector_dim, distance=Distance.COSINE),
            )

    def upsert(self, collection: str, vectors: np.ndarray,
               metadata: List[Dict[str, Any]]) -> int:
        if not self._ensure_client():
            return 0
        self._ensure_collection(collection)

        from qdrant_client.models import PointStruct
        points = []
        for i, (vec, meta) in enumerate(zip(vectors, metadata)):
            point_id = hash(meta.get("id", f"vec_{i}")) % (2**63)
            points.append(PointStruct(
                id=point_id,
                vector=vec.tolist(),
                payload=meta,
            ))

        self._client.upsert(collection_name=collection, points=points)
        return len(points)

    def search(self, collection: str, query_vector: np.ndarray,
               top_k: int = 10, filters: Optional[Dict[str, Any]] = None) -> List[SearchResult]:
        if not self._ensure_client():
            return []

        from qdrant_client.models import Filter, FieldCondition, MatchValue
        qdrant_filter = None
        if filters:
            conditions = [
                FieldCondition(key=k, match=MatchValue(value=v))
                for k, v in filters.items()
            ]
            qdrant_filter = Filter(must=conditions)

        hits = self._client.search(
            collection_name=collection,
            query_vector=query_vector.tolist(),
            limit=top_k,
            query_filter=qdrant_filter,
        )
        return [SearchResult(id=str(hit.id), score=hit.score, metadata=hit.payload or {}) for hit in hits]

    def delete_by_filter(self, collection: str, filters: Dict[str, Any]) -> int:
        if not self._ensure_client():
            return 0

        from qdrant_client.models import Filter, FieldCondition, MatchValue
        conditions = [
            FieldCondition(key=k, match=MatchValue(value=v))
            for k, v in filters.items()
        ]
        result = self._client.delete(
            collection_name=collection,
            points_selector=Filter(must=conditions),
        )
        return result.status.value if hasattr(result, 'status') else 0

    def collection_size(self, collection: str) -> int:
        if not self._ensure_client():
            return 0
        info = self._client.get_collection(collection_name=collection)
        return info.points_count


def build_vector_store(config: VectorStoreConfig, vector_dim: int = 64) -> VectorStore:
    """Factory: return the appropriate VectorStore for the given config."""
    if config.backend == "qdrant":
        return QdrantStore(config, vector_dim=vector_dim)
    return LocalNumpyStore(config)
