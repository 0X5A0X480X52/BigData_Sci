"""Scholarly Data MCP stdio Server.

Supports two modes:
* **in-process** — directly call ``call_tool()`` (default for Agent usage).
* **stdio** — standalone process speaking JSON-RPC over stdin/stdout.
"""

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
from .service_bridge import ScholarlyDataServiceBridge
from .tools import TOOL_DEFINITIONS, get_all_tool_schemas


class ScholarlyDataMCPServer(BaseMCPServer):
    """MCP server for scholarly-data tools."""

    tool_names = {
        "create_field_corpus",
        "create_seed_lineage_corpus",
        "expand_references",
        "expand_citing_works",
        "get_corpus_summary",
        "get_work",
        "list_candidate_papers",
    }

    def __init__(self, bridge: ScholarlyDataServiceBridge) -> None:
        self.bridge = bridge

    # ── In-Process API ───────────────────────────────────────

    def call(self, name: str, **kwargs: Any) -> Any:
        """Execute a tool in-process (matches the old facade signature)."""
        if name not in self.tool_names:
            raise ValueError(f"Unknown scholarly-data tool: {name}")
        return self.bridge.dispatch(name, kwargs, {})

    def call_tool(self, name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Execute a tool with explicit arguments and context dict."""
        if name not in self.tool_names:
            raise ValueError(f"Unknown scholarly-data tool: {name}")
        return self.bridge.dispatch(name, arguments, context)

    def list_tools(self) -> Dict[str, Any]:
        """Return the tools/list payload."""
        return {"tools": get_all_tool_schemas()}

    # ── Stdio MCP Server ─────────────────────────────────────

    async def run_stdio(self) -> None:
        """Start a stdio MCP server loop (stdin → process → stdout)."""
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


# ── standalone entry point ───────────────────────────────────

def main() -> None:
    """CLI entry point for running the MCP server over stdio."""
    from research_agent.services.scholarly_data import ScholarlyDataService
    from research_agent.core.artifact_store import ArtifactStore
    from research_agent.core.config import load_run_config

    config = load_run_config()
    artifact_store = ArtifactStore(config.artifact_root, "mcp_stdio")
    service = ScholarlyDataService(artifact_store, config)
    bridge = ScholarlyDataServiceBridge(service)
    server = ScholarlyDataMCPServer(bridge)

    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
