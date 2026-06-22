"""Configuration loader — YAML file + environment variable override."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


# ── Re-export config dataclasses from core ───────────────────

from research_agent.core.config import (
    EmbeddingConfig,
    ESConfig,
    MySQLConfig,
    Neo4jConfig,
    OpenAlexConfig,
    PersistenceConfig,
    VectorStoreConfig,
)

from research_agent.core.models import FeatureFlags, RunConfig


@dataclass
class SyncConfig:
    auto_neo4j: bool = False
    auto_es: bool = False
    auto_embed: bool = True
    neo4j_batch_size: int = 500
    es_batch_size: int = 1000


@dataclass
class GraphConfig:
    backend: str = "networkx"
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)


@dataclass
class ResearchConfig:
    """Top-level configuration aggregating all sub-configs."""
    openalex: OpenAlexConfig = field(default_factory=OpenAlexConfig)
    persistence: PersistenceConfig = field(default_factory=PersistenceConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    elasticsearch: ESConfig = field(default_factory=ESConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)

    def to_run_config(self, **overrides: Any) -> RunConfig:
        """Convert to a ``RunConfig`` for use by the agent runtime."""
        return RunConfig(
            max_field_corpus=int(overrides.get("max_field_corpus", 3000)),
            max_seed_lineage=int(overrides.get("max_seed_lineage", 2000)),
            max_bfs_depth=int(overrides.get("max_bfs_depth", 2)),
            max_key_papers=int(overrides.get("max_key_papers", 15)),
            max_pdfs=int(overrides.get("max_pdfs", 8)),
            max_tool_calls=int(overrides.get("max_tool_calls", 80)),
            max_retries=int(overrides.get("max_retries", 2)),
            max_iterations=int(overrides.get("max_iterations", 15)),
            agent_mode=overrides.get("agent_mode", "react"),
            artifact_root=overrides.get("artifact_root", "artifacts"),
            features=FeatureFlags(
                neo4j_sync=self.sync.auto_neo4j,
                es_sync=self.sync.auto_es,
                auto_embed=self.sync.auto_embed,
            ),
        )


class ConfigLoader:
    """Loads configuration from YAML file with environment variable override.

    Environment variables use the pattern ``RA_SECTION_KEY``, e.g.
    ``RA_MYSQL_HOST=db.example.com`` overrides ``persistence.mysql.host``.
    """

    def __init__(self, yaml_path: Optional[str | Path] = None) -> None:
        self._yaml_path = yaml_path
        self._data: Dict[str, Any] = {}

    def load(self) -> ResearchConfig:
        """Load and return a fully resolved ``ResearchConfig``."""
        self._data = self._load_yaml()
        self._apply_env_overrides()
        return self._build_config()

    def _load_yaml(self) -> Dict[str, Any]:
        if self._yaml_path:
            path = Path(self._yaml_path)
            if path.exists():
                with open(path, "r", encoding="utf-8") as fh:
                    return yaml.safe_load(fh) or {}
        # Fall back to bundled settings.yaml
        bundled = Path(__file__).parent / "settings.yaml"
        if bundled.exists():
            with open(bundled, "r", encoding="utf-8") as fh:
                return yaml.safe_load(fh) or {}
        return {}

    def _apply_env_overrides(self) -> None:
        """Walk ``RA_*`` env vars and inject into the config dict."""
        for key, value in os.environ.items():
            if not key.startswith("RA_"):
                continue
            # RA_MYSQL_HOST → persistence.mysql.host
            parts = key[3:].lower().split("_", 1)  # ["mysql", "host"]
            if len(parts) != 2:
                continue
            section, subkey = parts
            section_map = {
                "mysql": ("persistence", "mysql"),
                "openalex": ("openalex", None),
                "neo4j": ("graph", "neo4j"),
                "es": ("elasticsearch", None),
                "embedding": ("embedding", None),
                "qdrant": ("vector_store", None),
                "vector": ("vector_store", None),
            }
            mapping = section_map.get(section)
            if mapping is None:
                continue
            parent_key, child_key = mapping
            if child_key:
                self._data.setdefault(parent_key, {}).setdefault(child_key, {})[subkey] = self._coerce(value)
            else:
                self._data.setdefault(parent_key, {})[subkey] = self._coerce(value)

    @staticmethod
    def _coerce(value: str) -> Any:
        if value.lower() in ("true", "yes", "1"):
            return True
        if value.lower() in ("false", "no", "0"):
            return False
        try:
            return int(value)
        except ValueError:
            pass
        try:
            return float(value)
        except ValueError:
            pass
        return value

    def _build_config(self) -> ResearchConfig:
        oa = self._data.get("openalex", {})
        mysql_data = self._data.get("persistence", {}).get("mysql", {})
        neo4j_data = self._data.get("graph", {}).get("neo4j", {})
        es_data = self._data.get("elasticsearch", {})
        emb_data = self._data.get("embedding", {})
        vs_data = self._data.get("vector_store", {})
        sync_data = self._data.get("sync", {})

        return ResearchConfig(
            openalex=OpenAlexConfig(
                email=oa.get("email", ""),
                base_url=oa.get("base_url", "https://api.openalex.org"),
                polite_pool=oa.get("polite_pool", True),
                max_retries=oa.get("max_retries", 3),
                retry_delay=oa.get("retry_delay", 1.0),
                cache_dir=oa.get("cache_dir", ".cache/openalex"),
                rate_limit_per_second=oa.get("rate_limit_per_second", 10.0),
            ),
            persistence=PersistenceConfig(
                backend=self._data.get("persistence", {}).get("backend", "mysql"),
                mysql=MySQLConfig(
                    host=mysql_data.get("host", "localhost"),
                    port=mysql_data.get("port", 3306),
                    user=mysql_data.get("user", "research_agent"),
                    password=mysql_data.get("password", ""),
                    database=mysql_data.get("database", "research_agent"),
                    charset=mysql_data.get("charset", "utf8mb4"),
                    max_connections=mysql_data.get("max_connections", 10),
                    pool_recycle=mysql_data.get("pool_recycle", 3600),
                ),
                sqlite_path=self._data.get("persistence", {}).get("sqlite_path", "artifacts/research_agent.db"),
            ),
            graph=GraphConfig(
                backend=self._data.get("graph", {}).get("backend", "networkx"),
                neo4j=Neo4jConfig(
                    uri=neo4j_data.get("uri", "bolt://localhost:7687"),
                    user=neo4j_data.get("user", "neo4j"),
                    password=neo4j_data.get("password", ""),
                    database=neo4j_data.get("database", "neo4j"),
                    connection_timeout=neo4j_data.get("connection_timeout", 5),
                ),
            ),
            elasticsearch=ESConfig(
                hosts=es_data.get("hosts", ["localhost:9200"]),
                connection_timeout=es_data.get("connection_timeout", 5),
            ),
            embedding=EmbeddingConfig(
                backend=emb_data.get("backend", "hash"),
                local_model=emb_data.get("local_model", "BAAI/bge-base-en-v1.5"),
                api_model=emb_data.get("api_model", "text-embedding-3-small"),
                api_base_url=emb_data.get("api_base_url", ""),
                api_key=emb_data.get("api_key", ""),
                vector_dim=emb_data.get("vector_dim", 64),
                batch_size=emb_data.get("batch_size", 64),
            ),
            vector_store=VectorStoreConfig(
                backend=vs_data.get("backend", "local_numpy"),
                qdrant_url=vs_data.get("qdrant_url", "http://localhost:6333"),
                qdrant_collection=vs_data.get("qdrant_collection", "research_evidence"),
                storage_dir=vs_data.get("storage_dir", "artifacts/vectors"),
            ),
            sync=SyncConfig(
                auto_neo4j=sync_data.get("auto_neo4j", False),
                auto_es=sync_data.get("auto_es", False),
                auto_embed=sync_data.get("auto_embed", True),
                neo4j_batch_size=sync_data.get("neo4j_batch_size", 500),
                es_batch_size=sync_data.get("es_batch_size", 1000),
            ),
        )


def load_config(yaml_path: Optional[str | Path] = None) -> ResearchConfig:
    """Convenience: load config from YAML and return a ResearchConfig."""
    return ConfigLoader(yaml_path).load()
