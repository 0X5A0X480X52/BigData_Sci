"""Data layer — OpenAlex source, entity cleaners, embedding, vector store, PDF, parsing."""

from .cleaners import (
    AuthorCleaner, BaseCleaner, BatchCleaner, BatchCleanedResult,
    ConceptCleaner, InstitutionCleaner, VenueCleaner, WorkCleaner,
)
from .embedding_adapter import Embedder, HashEmbedder, OpenAIEmbedder, SentenceTransformerEmbedder, build_embedder
from .embedding_pipeline import EmbeddingPipeline, EmbeddingJob, EmbeddingJobResult
from .openalex_source import OpenAlexSource
from .parser_adapter import ParserAdapter, ParsedDocument, PageContent
from .pdf_manager import PDFManager, PDFAsset, LocalObjectStorage
from .vector_store import VectorStore, LocalNumpyStore, QdrantStore, SearchResult, build_vector_store

__all__ = [
    "AuthorCleaner", "BaseCleaner", "BatchCleaner", "BatchCleanedResult",
    "ConceptCleaner", "InstitutionCleaner", "VenueCleaner", "WorkCleaner",
    "Embedder", "HashEmbedder", "OpenAIEmbedder", "SentenceTransformerEmbedder", "build_embedder",
    "EmbeddingPipeline", "EmbeddingJob", "EmbeddingJobResult",
    "LocalNumpyStore", "LocalObjectStorage",
    "OpenAlexSource",
    "PageContent", "ParsedDocument", "ParserAdapter",
    "PDFAsset", "PDFManager",
    "QdrantStore",
    "SearchResult",
    "VectorStore", "build_vector_store",
]
