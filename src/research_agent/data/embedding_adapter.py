"""Embedding adapter — abstract interface with three backends.

- HashEmbedder: deterministic 64-dim hash (default, always available)
- SentenceTransformerEmbedder: local BGE model
- OpenAIEmbedder: OpenAI-compatible API
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import List

import numpy as np

from research_agent.core.config import EmbeddingConfig


class Embedder(ABC):
    """Abstract embedding interface."""

    @abstractmethod
    def embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        """Embed a list of texts, returning a (N, dim) array."""
        ...

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimensionality of the embedding vectors."""
        ...


class HashEmbedder(Embedder):
    """Deterministic hash-based embedding.

    Uses SHA-256 of each text → 64-dim float vector.
    Always available, zero dependencies beyond stdlib.
    """

    def __init__(self, dim: int = 64) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        vectors = np.zeros((len(texts), self._dim), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i] = self._hash_to_vec(text)
        return vectors

    def _hash_to_vec(self, text: str) -> np.ndarray:
        h = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(h, dtype=np.uint8).astype(np.float32) / 255.0
        # Repeat or truncate to target dim
        if len(vec) >= self._dim:
            return vec[:self._dim]
        repeats = (self._dim + len(vec) - 1) // len(vec)
        return np.tile(vec, repeats)[:self._dim]


class SentenceTransformerEmbedder(Embedder):
    """Local SentenceTransformer (BGE) embedder."""

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", device: str = "auto") -> None:
        self._model_name = model_name
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(model_name, device=device)
        except ImportError:
            raise ImportError(
                "sentence-transformers is required for SentenceTransformerEmbedder. "
                "Install with: pip install sentence-transformers"
            )

    @property
    def dim(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        return self._model.encode(texts, batch_size=batch_size, show_progress_bar=False)


class OpenAIEmbedder(Embedder):
    """OpenAI-compatible embedding API."""

    def __init__(self, config: EmbeddingConfig) -> None:
        self._config = config
        self._dim_cache: int | None = None
        try:
            from openai import OpenAI
            self._client = OpenAI(base_url=config.api_base_url or None, api_key=config.api_key)
        except ImportError:
            raise ImportError(
                "openai is required for OpenAIEmbedder. Install with: pip install openai"
            )

    @property
    def dim(self) -> int:
        if self._dim_cache is None:
            vec = self.embed_texts(["test"])
            self._dim_cache = vec.shape[1]
        return self._dim_cache

    def embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        all_vectors: List[np.ndarray] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            resp = self._client.embeddings.create(model=self._config.api_model, input=batch)
            all_vectors.append(np.array([d.embedding for d in resp.data], dtype=np.float32))
        return np.concatenate(all_vectors, axis=0)


def build_embedder(config: EmbeddingConfig) -> Embedder:
    """Factory: return the appropriate Embedder for the given config."""
    backend = config.backend
    if backend == "sentence_transformer":
        return SentenceTransformerEmbedder(model_name=config.local_model)
    elif backend == "openai_api":
        return OpenAIEmbedder(config)
    else:
        return HashEmbedder(dim=config.vector_dim)
