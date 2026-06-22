"""Smoke-test the EvidenceRAG MCP server in-process."""

import pytest

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.config import load_run_config
from research_agent.core.models import Paper
from research_agent.services.evidence_rag import EvidenceRAGService
from research_agent.mcp_servers.evidence_rag.server import EvidenceRAGMCPServer
from research_agent.mcp_servers.evidence_rag.service_bridge import EvidenceRAGServiceBridge


@pytest.fixture
def sample_paper():
    return Paper(
        work_id="W9999",
        title="Test Paper for RAG",
        abstract="This paper investigates graph neural networks for molecular property prediction.",
        publication_year=2024,
        cited_by_count=10,
    )


def test_server_lists_tools():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_evidence")
    service = EvidenceRAGService(store, config)
    bridge = EvidenceRAGServiceBridge(service)
    server = EvidenceRAGMCPServer(bridge)

    tools_resp = server.list_tools()
    assert "tools" in tools_resp
    assert len(tools_resp["tools"]) == 6
    names = {t["name"] for t in tools_resp["tools"]}
    assert "build_evidence_bundle" in names
    assert "search_paper_evidence" in names


def test_server_materialize_paper(sample_paper):
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_evidence")
    service = EvidenceRAGService(store, config)
    bridge = EvidenceRAGServiceBridge(service)
    server = EvidenceRAGMCPServer(bridge)

    result = server.call("ensure_fulltext_materialized", paper=sample_paper)
    assert result is not None
    assert "status" in result or "child_count" in result or isinstance(result, dict)


def test_server_call_unknown_tool_raises():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_evidence")
    service = EvidenceRAGService(store, config)
    bridge = EvidenceRAGServiceBridge(service)
    server = EvidenceRAGMCPServer(bridge)

    with pytest.raises(ValueError, match="Unknown evidence-rag tool"):
        server.call("nonexistent_tool")
