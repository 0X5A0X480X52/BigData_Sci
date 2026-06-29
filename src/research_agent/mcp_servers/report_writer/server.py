"""Report Writer MCP server."""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Dict

from research_agent.mcp_servers.common.base import BaseMCPServer
from research_agent.mcp_servers.common.protocol import MCPRequest, MCPResponse, parse_mcp_message, serialize_mcp_message

from .service_bridge import ReportWriterServiceBridge
from .tools import get_all_tool_schemas


class ReportWriterMCPServer(BaseMCPServer):
    tool_names = {"write_research_report"}

    def __init__(self, bridge: ReportWriterServiceBridge) -> None:
        self.bridge = bridge

    def call(self, name: str, **kwargs: Any) -> Any:
        if name not in self.tool_names:
            raise ValueError(f"Unknown report-writer tool: {name}")
        return self.bridge.dispatch(name, kwargs, {})

    def call_tool(self, name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        if name not in self.tool_names:
            raise ValueError(f"Unknown report-writer tool: {name}")
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
        if request.method == "tools/list":
            return MCPResponse(id=request.id, result=self.list_tools())
        if request.method == "tools/call":
            params = request.params
            try:
                result = self.call_tool(params.get("name", ""), params.get("arguments", {}), params.get("context", {}))
                return MCPResponse(id=request.id, result={"content": [{"type": "text", "text": str(result)}]})
            except Exception as exc:
                return MCPResponse(id=request.id, error={"code": -32603, "message": str(exc)})
        return MCPResponse(id=request.id, error={"code": -32601, "message": f"Method not found: {request.method}"})


def main() -> None:
    from research_agent.core.artifact_store import ArtifactStore
    from research_agent.core.config import load_run_config
    from research_agent.services.report_writer import ReportWriterService

    config = load_run_config()
    service = ReportWriterService(ArtifactStore(config.artifact_root, "mcp_stdio"))
    server = ReportWriterMCPServer(ReportWriterServiceBridge(service))
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
