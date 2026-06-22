"""Shared workflow runner for CLI and Streamlit UI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional

from research_agent.core.config import EmbeddingConfig, MySQLConfig, Neo4jConfig, OpenAlexConfig, VectorStoreConfig
from research_agent.core.models import FeatureFlags, ResearchRun, RunConfig
from research_agent.data.embedding_adapter import build_embedder
from research_agent.data.openalex_source import OpenAlexSource
from research_agent.data.parser_adapter import ParserAdapter
from research_agent.data.pdf_manager import PDFManager
from research_agent.data.vector_store import build_vector_store
from research_agent.persistence.mysql_repository import MySQLResearchRepository
from research_agent.runtime.agent import ResearchAgent
from research_agent.runtime.query_planner import plan_openalex_query


ProviderName = Literal["fixture", "openalex"]
AgentModeName = Literal["react", "planner_executor"]
PersistenceBackend = Literal["local", "mysql"]
VectorBackend = Literal["local_numpy", "qdrant"]


@dataclass
class ResearchRunOptions:
    provider: ProviderName = "fixture"
    openalex_email: str = ""
    openalex_cache_dir: str = ".cache/openalex"
    agent_mode: AgentModeName = "react"
    artifact_root: str = "artifacts"
    max_field_corpus: int = 30
    max_key_papers: int = 10
    max_pdfs: int = 5
    seed_work_id: str = ""

    llm_react: bool = False
    llm_plan: bool = False
    llm_query: bool = False
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str = ""

    persistence_backend: PersistenceBackend = "local"
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "research_agent"
    mysql_password: str = ""
    mysql_database: str = "research_agent"
    mysql_init_schema: bool = False

    enable_pdf: bool = False
    parser_preference: str = "pymupdf"
    vector_backend: VectorBackend = "local_numpy"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "research_evidence"
    vector_storage_dir: str = "artifacts/vectors"

    neo4j_sync: bool = False
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    es_sync: bool = False


@dataclass
class ResearchWorkflowResult:
    run: ResearchRun
    warnings: List[str] = field(default_factory=list)
    service_status: Dict[str, Any] = field(default_factory=dict)
    sync_results: Dict[str, Any] = field(default_factory=dict)


def run_research_workflow(
    options: ResearchRunOptions,
    question: str,
    seed_work_id: Optional[str] = None,
    event_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> ResearchWorkflowResult:
    """Run a research workflow with optional external-service integrations."""
    warnings: List[str] = []
    service_status: Dict[str, Any] = {}
    sync_results: Dict[str, Any] = {}

    _apply_llm_environment(options)
    _emit(event_callback, "workflow_started", question=question, provider=options.provider, mode=options.agent_mode)

    query_plan = plan_openalex_query(question, use_llm=options.llm_query)
    warnings.extend(query_plan.warnings)
    service_status["openalex_query"] = query_plan.as_dict()
    _emit(event_callback, "openalex_query_planned", **query_plan.as_dict())

    features = FeatureFlags(
        neo4j_sync=options.neo4j_sync,
        es_sync=options.es_sync,
        qdrant_sync=options.vector_backend == "qdrant",
        llm_driven_react=options.llm_react,
        llm_driven_plan=options.llm_plan,
    )
    config = RunConfig(
        max_field_corpus=options.max_field_corpus,
        max_pdfs=options.max_pdfs,
        max_key_papers=options.max_key_papers,
        artifact_root=options.artifact_root,
        agent_mode=options.agent_mode,
        features=features,
    )

    repository = _build_repository(options, warnings, service_status)
    _emit(event_callback, "service_checked", service="mysql", status=service_status.get("mysql", {}))
    openalex_source = _build_scholarly_provider(options, warnings, service_status)
    _emit(event_callback, "service_checked", service="openalex", status=service_status.get("openalex", {}))
    pdf_manager, parser = _build_pdf_stack(options, repository, warnings, service_status)
    _emit(event_callback, "service_checked", service="pdf", status=service_status.get("pdf", {}))
    embedder, vector_store = _build_vector_stack(options, warnings, service_status)
    _emit(event_callback, "service_checked", service="vector_store", status=service_status.get("vector_store", {}))

    agent = ResearchAgent(
        config=config,
        openalex_source=openalex_source,
        repository=repository,
        pdf_manager=pdf_manager,
        parser=parser,
        embedder=embedder,
        vector_store=vector_store,
        openalex_query_plan=query_plan.as_dict(),
        trace_callback=event_callback,
    )
    run = agent.run(question, seed_work_id=seed_work_id or options.seed_work_id or None)
    for mcp_result in run.results:
        for warning in getattr(mcp_result, "warnings", []) or []:
            if warning not in warnings:
                warnings.append(warning)

    if options.neo4j_sync:
        sync_results["neo4j"] = _sync_latest_graph_to_neo4j(run, options, warnings)
    if options.es_sync:
        warnings.append("Elasticsearch sync is configured but not yet wired to a production indexer in the UI runner.")
        sync_results["elasticsearch"] = {"status": "skipped", "reason": "not_wired"}

    if repository is not None and hasattr(repository, "save_run_outputs"):
        try:
            repository.save_run_outputs(run)
        except Exception as exc:
            warnings.append(f"MySQL final run output update failed: {exc}")

    _emit(event_callback, "workflow_completed", run_id=run.run_id, status=run.status, warnings=warnings)
    return ResearchWorkflowResult(run=run, warnings=warnings, service_status=service_status, sync_results=sync_results)


def _emit(callback: Optional[Callable[[Dict[str, Any]], None]], event_type: str, **payload: Any) -> None:
    if callback is None:
        return
    try:
        from research_agent.core.utils import utc_now_iso
        event = {"time": utc_now_iso(), "type": event_type}
        event.update(payload)
        callback(event)
    except Exception:
        pass


def _apply_llm_environment(options: ResearchRunOptions) -> None:
    if options.llm_base_url:
        os.environ["RA_LLM_BASE_URL"] = options.llm_base_url
    if options.llm_model:
        os.environ["RA_LLM_MODEL"] = options.llm_model
    if options.llm_api_key:
        os.environ["RA_LLM_API_KEY"] = options.llm_api_key


def _build_repository(options: ResearchRunOptions, warnings: List[str], status: Dict[str, Any]) -> Any:
    if options.persistence_backend != "mysql":
        status["mysql"] = {"enabled": False, "status": "local_artifacts_only"}
        return None
    repo = MySQLResearchRepository(MySQLConfig(
        host=options.mysql_host,
        port=options.mysql_port,
        user=options.mysql_user,
        password=options.mysql_password,
        database=options.mysql_database,
    ))
    try:
        if options.mysql_init_schema:
            repo.init_schema()
        health = repo.health_check()
        if not health:
            raise RuntimeError("health_check returned False")
        status["mysql"] = {"enabled": True, "status": "connected"}
        return repo
    except Exception as exc:
        warnings.append(f"MySQL unavailable; using local artifacts only: {exc}")
        status["mysql"] = {"enabled": True, "status": "degraded", "error": str(exc)}
        return None


def _build_scholarly_provider(options: ResearchRunOptions, warnings: List[str], status: Dict[str, Any]) -> Any:
    if options.provider != "openalex":
        status["openalex"] = {"provider": "fixture", "status": "offline_fixture"}
        return None
    try:
        client = OpenAlexSource(OpenAlexConfig(email=options.openalex_email, cache_dir=options.openalex_cache_dir))
        status["openalex"] = {"provider": "openalex", "status": "configured", "cache_dir": options.openalex_cache_dir}
        return client
    except Exception as exc:
        warnings.append(f"OpenAlex provider unavailable; falling back to fixture: {exc}")
        status["openalex"] = {"provider": "openalex", "status": "degraded", "error": str(exc)}
        return None


def _build_pdf_stack(options: ResearchRunOptions, repository: Any, warnings: List[str], status: Dict[str, Any]) -> tuple[Any, Any]:
    if not options.enable_pdf:
        status["pdf"] = {"enabled": False, "status": "abstract_fallback"}
        return None, None
    try:
        storage_root = Path(options.artifact_root) / "object_storage"
        status["pdf"] = {"enabled": True, "status": "configured", "storage_root": str(storage_root)}
        return PDFManager(storage_root, repository=repository), ParserAdapter(preferred=options.parser_preference)
    except Exception as exc:
        warnings.append(f"PDF stack unavailable; using abstract fallback: {exc}")
        status["pdf"] = {"enabled": True, "status": "degraded", "error": str(exc)}
        return None, None


def _build_vector_stack(options: ResearchRunOptions, warnings: List[str], status: Dict[str, Any]) -> tuple[Any, Any]:
    try:
        emb_config = EmbeddingConfig(backend="hash", vector_dim=64)
        embedder = build_embedder(emb_config)
        vector_store = build_vector_store(VectorStoreConfig(
            backend=options.vector_backend,
            qdrant_url=options.qdrant_url,
            qdrant_collection=options.qdrant_collection,
            storage_dir=options.vector_storage_dir,
        ), vector_dim=embedder.dim)
        if options.vector_backend == "qdrant" and not getattr(vector_store, "_ensure_client", lambda: True)():
            warnings.append("Qdrant unavailable; Evidence RAG will continue with local in-memory hash retrieval.")
            status["vector_store"] = {"backend": "qdrant", "status": "degraded"}
        else:
            status["vector_store"] = {"backend": options.vector_backend, "status": "configured"}
        return embedder, vector_store
    except Exception as exc:
        warnings.append(f"Vector stack unavailable; Evidence RAG will use built-in hash retrieval: {exc}")
        status["vector_store"] = {"backend": options.vector_backend, "status": "degraded", "error": str(exc)}
        return None, None


def _sync_latest_graph_to_neo4j(run: ResearchRun, options: ResearchRunOptions, warnings: List[str]) -> Dict[str, Any]:
    graph_refs = [ref for ref in run.artifacts if ref.result_type == "graph_snapshot"]
    if not graph_refs:
        return {"status": "skipped", "reason": "no_graph_snapshot"}
    graph_path = Path(graph_refs[-1].path)
    if not graph_path.exists():
        return {"status": "skipped", "reason": "graph_snapshot_missing", "path": str(graph_path)}
    try:
        from neo4j import GraphDatabase
    except Exception as exc:
        warnings.append(f"Neo4j driver unavailable; sync skipped: {exc}")
        return {"status": "degraded", "error": str(exc)}

    try:
        graph_data = json.loads(graph_path.read_text(encoding="utf-8"))
        snapshot_id = graph_data.get("graph_snapshot_id", graph_path.stem)
        nodes = _neo4j_safe_records(graph_data.get("nodes", []), required_keys=("id", "type"))
        edges = _neo4j_safe_records(graph_data.get("edges", []), required_keys=("source", "target", "type"))
        driver = GraphDatabase.driver(options.neo4j_uri, auth=(options.neo4j_user, options.neo4j_password))
        with driver.session(database=options.neo4j_database) as session:
            session.run(
                """
                UNWIND $nodes AS node
                MERGE (n:ResearchNode {snapshot_id: $snapshot_id, node_id: node.id})
                SET n.node_type = node.type,
                    n.title = coalesce(node.props.title, node.props.display_name, node.id),
                    n.properties_json = node.properties_json,
                    n += node.props
                """,
                snapshot_id=snapshot_id,
                nodes=nodes,
            ).consume()
            session.run(
                """
                UNWIND $edges AS edge
                MATCH (s:ResearchNode {snapshot_id: $snapshot_id, node_id: edge.source})
                MATCH (t:ResearchNode {snapshot_id: $snapshot_id, node_id: edge.target})
                MERGE (s)-[r:RESEARCH_EDGE {snapshot_id: $snapshot_id, source: edge.source, target: edge.target, edge_type: edge.type}]->(t)
                SET r.properties_json = edge.properties_json,
                    r += edge.props
                """,
                snapshot_id=snapshot_id,
                edges=edges,
            ).consume()
        driver.close()
        return {"status": "completed", "snapshot_id": snapshot_id, "nodes": len(nodes), "edges": len(edges)}
    except Exception as exc:
        warnings.append(f"Neo4j sync failed: {exc}")
        return {"status": "failed", "error": str(exc)}





def _neo4j_safe_records(records: Any, required_keys: tuple[str, ...] = ()) -> List[Dict[str, Any]]:
    """Convert graph snapshot records into Neo4j-safe parameter maps.

    Neo4j properties cannot be nested maps. Each returned row keeps top-level
    routing fields, a ``props`` map containing only primitive/primitive-array
    values, and a JSON copy of the original record for auditability.
    """
    safe_records: List[Dict[str, Any]] = []
    if not isinstance(records, list):
        return safe_records
    for record in records:
        if not isinstance(record, dict):
            continue
        safe: Dict[str, Any] = {
            key: _neo4j_scalar(record.get(key, ""))
            for key in required_keys
        }
        props: Dict[str, Any] = {}
        for key, value in record.items():
            prop = _neo4j_property_value(value)
            if prop is not None:
                props[str(key)] = prop
        safe["props"] = props
        safe["properties_json"] = json.dumps(record, ensure_ascii=False, default=str)
        safe_records.append(safe)
    return safe_records


def _neo4j_property_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        converted = [_neo4j_scalar(item) for item in value]
        return [item for item in converted if item is not None]
    return None


def _neo4j_scalar(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)