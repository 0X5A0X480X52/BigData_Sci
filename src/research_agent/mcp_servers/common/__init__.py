"""MCP common infrastructure — protocol, errors, result handling, and base server."""

from .base import BaseMCPServer
from .errors import MCPErrorCode, MCPError
from .protocol import MCPRequest, MCPResponse, parse_mcp_message, serialize_mcp_message
from .result_handler import ArtifactResultHandler, maybe_artifactize

__all__ = [
    "BaseMCPServer",
    "MCPErrorCode",
    "MCPError",
    "MCPRequest",
    "MCPResponse",
    "parse_mcp_message",
    "serialize_mcp_message",
    "ArtifactResultHandler",
    "maybe_artifactize",
]
