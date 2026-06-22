"""Configuration helpers — environment-driven with nested dataclass support."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Optional

from .models import FeatureFlags, RunConfig


# ── Nested config dataclasses ────────────────────────────────

@dataclass
class OpenAlexConfig:
    email: str = ""
    base_url: str = "https://api.openalex.org"
    polite_pool: bool = True
    max_retries: int = 3
    retry_delay: float = 1.0
    cache_dir: str = ".cache/openalex"
    rate_limit_per_second: float = 10.0


@dataclass
class MySQLConfig:
    host: str = "localhost"
    port: int = 3306
    user: str = "research_agent"
    password: str = ""
    database: str = "research_agent"
    charset: str = "utf8mb4"
    max_connections: int = 10
    pool_recycle: int = 3600


@dataclass
class PersistenceConfig:
    backend: Literal["mysql", "sqlite"] = "mysql"
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    sqlite_path: str = "artifacts/research_agent.db"


@dataclass
class Neo4jConfig:
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"
    password: str = ""
    database: str = "neo4j"
    connection_timeout: int = 5


@dataclass
class ESConfig:
    hosts: list = field(default_factory=lambda: ["localhost:9200"])
    connection_timeout: int = 5


@dataclass
class EmbeddingConfig:
    backend: Literal["hash", "sentence_transformer", "openai_api"] = "hash"
    local_model: str = "BAAI/bge-base-en-v1.5"
    api_model: str = "text-embedding-3-small"
    api_base_url: str = ""
    api_key: str = ""
    vector_dim: int = 64
    batch_size: int = 64


@dataclass
class VectorStoreConfig:
    backend: Literal["local_numpy", "qdrant"] = "local_numpy"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "research_evidence"
    storage_dir: str = "artifacts/vectors"


# ── Config loader ───────────────────────────────────────────

def load_run_config() -> RunConfig:
    """Build a RunConfig from environment variables (backward compatible).

    The nested config dataclasses defined above will be wired into the
    full ResearchConfig in Phase 2 (YAML config system).
    """
    return RunConfig(
        max_field_corpus=int(os.getenv("RA_MAX_FIELD_CORPUS", "3000")),
        max_seed_lineage=int(os.getenv("RA_MAX_SEED_LINEAGE", "2000")),
        max_bfs_depth=int(os.getenv("RA_MAX_BFS_DEPTH", "2")),
        max_key_papers=int(os.getenv("RA_MAX_KEY_PAPERS", "15")),
        max_pdfs=int(os.getenv("RA_MAX_PDFS", "8")),
        max_tool_calls=int(os.getenv("RA_MAX_TOOL_CALLS", "80")),
        max_retries=int(os.getenv("RA_MAX_RETRIES", "2")),
        max_iterations=int(os.getenv("RA_MAX_ITERATIONS", "15")),
        agent_mode=os.getenv("RA_AGENT_MODE", "react"),  # type: ignore[arg-type]
        artifact_root=os.getenv("RA_ARTIFACT_ROOT", "artifacts"),
        features=FeatureFlags(
            use_langgraph_runtime=os.getenv("RA_USE_LANGGRAPH", "1") != "0",
            llm_driven_react=os.getenv("RA_LLM_REACT", "0") == "1",
            llm_driven_plan=os.getenv("RA_LLM_PLAN", "0") == "1",
            storm_perspective_skill=os.getenv("RA_STORM_PERSPECTIVE", "1") != "0",
            paperqa2_synthesis=os.getenv("RA_PAPERQA2", "0") == "1",
            gpt_researcher_mcp=os.getenv("RA_GPT_RESEARCHER", "0") == "1",
            neo4j_sync=os.getenv("RA_NEO4J_SYNC", "0") == "1",
            es_sync=os.getenv("RA_ES_SYNC", "0") == "1",
            qdrant_sync=os.getenv("RA_QDRANT_SYNC", "0") == "1",
            auto_embed=os.getenv("RA_AUTO_EMBED", "1") != "0",
        ),
    )
