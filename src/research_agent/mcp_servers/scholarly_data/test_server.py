"""Smoke-test the ScholarlyData MCP server in-process."""

import pytest

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.config import load_run_config
from research_agent.services.scholarly_data import ScholarlyDataService
from research_agent.mcp_servers.scholarly_data.server import ScholarlyDataMCPServer
from research_agent.mcp_servers.scholarly_data.service_bridge import ScholarlyDataServiceBridge


def test_server_lists_tools():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_scholarly")
    service = ScholarlyDataService(store, config)
    bridge = ScholarlyDataServiceBridge(service)
    server = ScholarlyDataMCPServer(bridge)

    tools_resp = server.list_tools()
    assert "tools" in tools_resp
    assert len(tools_resp["tools"]) == 7
    names = {t["name"] for t in tools_resp["tools"]}
    assert "create_field_corpus" in names
    assert "get_work" in names


def test_server_call_creates_corpus():
    config = load_run_config()
    config.max_field_corpus = 10
    store = ArtifactStore(config.artifact_root, "test_mcp_scholarly")
    service = ScholarlyDataService(store, config)
    bridge = ScholarlyDataServiceBridge(service)
    server = ScholarlyDataMCPServer(bridge)

    result = server.call("create_field_corpus", query="graph neural networks", max_results=5)
    assert result is not None
    assert hasattr(result, "corpus_id")
    assert len(result.papers) > 0


def test_server_call_unknown_tool_raises():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_scholarly")
    service = ScholarlyDataService(store, config)
    bridge = ScholarlyDataServiceBridge(service)
    server = ScholarlyDataMCPServer(bridge)

    with pytest.raises(ValueError, match="Unknown scholarly-data tool"):
        server.call("nonexistent_tool")
