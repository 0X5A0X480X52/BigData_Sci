"""Smoke-test the GraphAnalytics MCP server in-process."""

import pytest

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.config import load_run_config
from research_agent.services.graph_analytics import GraphAnalyticsService
from research_agent.services.scholarly_data import ScholarlyDataService
from research_agent.mcp_servers.graph_analytics.server import GraphAnalyticsMCPServer
from research_agent.mcp_servers.graph_analytics.service_bridge import GraphAnalyticsServiceBridge


@pytest.fixture
def corpus():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_graph")
    svc = ScholarlyDataService(store, config)
    return svc.create_field_corpus("graph neural networks", max_results=10)


def test_server_lists_tools():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_graph")
    service = GraphAnalyticsService(store, config)
    bridge = GraphAnalyticsServiceBridge(service)
    server = GraphAnalyticsMCPServer(bridge)

    tools_resp = server.list_tools()
    assert "tools" in tools_resp
    assert len(tools_resp["tools"]) == 8
    names = {t["name"] for t in tools_resp["tools"]}
    assert "map_field_structure" in names
    assert "run_pagerank" in names


def test_server_map_field_structure(corpus):
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_graph")
    service = GraphAnalyticsService(store, config)
    bridge = GraphAnalyticsServiceBridge(service)
    server = GraphAnalyticsMCPServer(bridge)

    result = server.call("map_field_structure", corpus=corpus)
    assert result is not None
    assert "key_papers" in result
    assert "communities" in result or "snapshot_id" in str(result)


def test_server_call_unknown_tool_raises():
    config = load_run_config()
    store = ArtifactStore(config.artifact_root, "test_mcp_graph")
    service = GraphAnalyticsService(store, config)
    bridge = GraphAnalyticsServiceBridge(service)
    server = GraphAnalyticsMCPServer(bridge)

    with pytest.raises(ValueError, match="Unknown graph-analytics tool"):
        server.call("nonexistent_tool")
