"""Graph Analytics MCP stdio Server."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict

from research_agent.mcp_servers.common.base import BaseMCPServer
from research_agent.mcp_servers.common.protocol import (
    MCPRequest,
    MCPResponse,
    parse_mcp_message,
    serialize_mcp_message,
)
from .service_bridge import GraphAnalyticsServiceBridge
from .tools import TOOL_DEFINITIONS, get_all_tool_schemas


class GraphAnalyticsMCPServer(BaseMCPServer):
    """MCP server for graph-analytics tools."""

    tool_names = {
        "build_graph_snapshot",
        "run_pagerank",
        "detect_communities",
        "rank_key_papers",
        "find_bridge_papers",
        "compute_topic_statistics",
        "compute_yearly_trend",
        "map_field_structure",
    }

    def __init__(self, bridge: GraphAnalyticsServiceBridge) -> None:
        self.bridge = bridge

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self.tool_names:
            raise ValueError(f"Unknown graph-analytics tool: {name}")
        return self.bridge.dispatch(name, kwargs, {})

    def call_tool(self, name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        if name not in self.tool_names:
            raise ValueError(f"Unknown graph-analytics tool: {name}")
        return self.bridge.dispatch(name, arguments, context)

    def list_tools(self) -> Dict[str, Any]:
        return {"tools": get_all_tool_schemas()}

    async def run_stdio(self) -> None:
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            request = parse_mcp_message(line.decode("utf-8"))
            response = await self._handle_request(request)
            sys.stdout.write(serialize_mcp_message(response) + "\n")
            sys.stdout.flush()

    async def _handle_request(self, request: MCPRequest) -> MCPResponse:
        method = request.method
        if method == "tools/list":
            return MCPResponse(id=request.id, result=self.list_tools())
        elif method == "tools/call":
            params = request.params
            try:
                result = self.call_tool(
                    name=params.get("name", ""),
                    arguments=params.get("arguments", {}),
                    context=params.get("context", {}),
                )
                return MCPResponse(id=request.id, result={"content": [{"type": "text", "text": str(result)}]})
            except Exception as exc:
                return MCPResponse(id=request.id, error={"code": -32603, "message": str(exc)})
        else:
            return MCPResponse(id=request.id, error={"code": -32601, "message": f"Method not found: {method}"})


def main() -> None:
    from research_agent.services.graph_analytics import GraphAnalyticsService
    from research_agent.core.artifact_store import ArtifactStore
    from research_agent.core.config import load_run_config

    config = load_run_config()
    artifact_store = ArtifactStore(config.artifact_root, "mcp_stdio")
    service = GraphAnalyticsService(artifact_store, config)
    bridge = GraphAnalyticsServiceBridge(service)
    server = GraphAnalyticsMCPServer(bridge)

    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
