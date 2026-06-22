"""MCP JSON-RPC 2.0 protocol messages."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class MCPRequest:
    """Incoming MCP JSON-RPC request."""
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    method: str = ""
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResponse:
    """Outgoing MCP JSON-RPC response."""
    jsonrpc: str = "2.0"
    id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[Dict[str, Any]] = None


def parse_mcp_message(raw: str) -> MCPRequest:
    """Parse a raw JSON-RPC string into an MCPRequest."""
    data = json.loads(raw.strip())
    return MCPRequest(
        jsonrpc=data.get("jsonrpc", "2.0"),
        id=data.get("id"),
        method=data.get("method", ""),
        params=data.get("params", {}),
    )


def serialize_mcp_message(response: MCPResponse) -> str:
    """Serialize an MCPResponse to a JSON-RPC string."""
    payload: Dict[str, Any] = {"jsonrpc": response.jsonrpc}
    if response.id is not None:
        payload["id"] = response.id
    if response.error is not None:
        payload["error"] = response.error
    elif response.result is not None:
        payload["result"] = response.result
    return json.dumps(payload, ensure_ascii=False, default=str)
