"""Abstract base for MCP servers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class BaseMCPServer(ABC):
    """Abstract base class for every MCP server in the research agent.

    Each concrete MCP server lives in its own folder under ``mcp_servers/``
    and provides:

    * ``list_tools()`` — respond to ``tools/list`` with the tool catalog.
    * ``call_tool(name, arguments, context)`` — execute a single tool.
    * ``tool_names`` — quick set of available tool names.
    """

    tool_names: set[str] = set()

    @abstractmethod
    def list_tools(self) -> Dict[str, Any]:
        """Return ``{"tools": [...]}`` with full tool definitions."""
        ...

    @abstractmethod
    def call_tool(self, name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Execute the named tool and return its result."""
        ...

    def call(self, name: str, **kwargs: Any) -> Any:
        """Convenience wrapper matching the existing in-process facade signature.

        Forwards to ``call_tool`` with an empty context dict.
        """
        return self.call_tool(name, kwargs, {})
