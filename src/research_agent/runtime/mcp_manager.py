"""MCP manager with budget, trace hooks, and structured MCPResult collection."""

from __future__ import annotations

from typing import Any, Dict, List

from research_agent.core.models import MCPResult
from research_agent.core.utils import stable_hash, utc_now_iso
from research_agent.mcp_servers.common.base import BaseMCPServer

from .budget import BudgetTracker, BudgetExceededError
from .trace import TraceRecorder


class MCPManager:
    """Routes tool calls to the appropriate MCP server, enforces budget, records trace.

    Two call modes:

    * ``call()`` — returns the **raw** result (backward compat with skills).
    * ``call_with_result()`` — returns ``(raw_result, MCPResult)`` (for graph agent).

    All MCPResults are accumulated in ``self.results`` for final run reporting.
    """

    def __init__(
        self,
        servers: Dict[str, BaseMCPServer],
        budget: BudgetTracker,
        trace: TraceRecorder,
    ) -> None:
        self.servers = servers
        self.budget = budget
        self.trace = trace
        self.results: List[MCPResult] = []   # accumulated for run reporting

    # ── Primary call interface (backward compat) ─────────────

    def call(self, provider: str, tool: str, run_id: str = "", task_id: str = "",
             **kwargs: Any) -> Any:
        """Execute a tool call and return the **raw** result.

        Raises ``BudgetExceededError`` if the budget is exhausted.
        If the tool fails the exception is re-raised so callers can handle it.
        The MCPResult is still recorded in ``self.results``.
        """
        self.budget.consume_tool_call()
        tool_call_id = f"TC_{stable_hash({'p': provider, 't': tool, 'a': kwargs, 'ts': utc_now_iso()}, 12)}"

        self.trace.tool_call(provider=provider, tool=tool, args=self._preview_args(kwargs),
                             tool_call_id=tool_call_id, run_id=run_id, task_id=task_id)

        server = self.servers.get(provider)
        if server is None:
            error_result = MCPResult(
                tool_call_id=tool_call_id, analysis_run_id=run_id, task_id=task_id,
                provider=provider, status="failed", result_type="error",
                method={"name": tool}, error=f"Unknown MCP provider: {provider}",
            )
            self.trace.tool_result(provider=provider, tool=tool, status="failed",
                                   error=error_result.error)
            self.results.append(error_result)
            raise RuntimeError(f"Unknown MCP provider: {provider}")

        try:
            raw_result = server.call(tool, **kwargs)
            mcp_result = self._build_mcp_result(tool_call_id, run_id, task_id, provider, tool, raw_result)
            self.trace.tool_result(provider=provider, tool=tool, status="completed",
                                   preview=self._preview_value(raw_result))
            self.results.append(mcp_result)
            return raw_result
        except Exception as exc:
            error_result = MCPResult(
                tool_call_id=tool_call_id, analysis_run_id=run_id, task_id=task_id,
                provider=provider, status="failed", result_type="error",
                method={"name": tool}, error=str(exc),
            )
            self.trace.tool_result(provider=provider, tool=tool, status="failed", error=str(exc))
            self.results.append(error_result)
            raise

    # ── Call with structured result (for graph agent) ───────

    def call_with_result(self, provider: str, tool: str, run_id: str = "",
                         task_id: str = "", **kwargs: Any) -> tuple[Any, MCPResult]:
        """Execute a tool call and return ``(raw_result, MCPResult)``.

        On tool failure, returns ``(None, error_MCPResult)`` instead of raising.
        """
        try:
            raw = self.call(provider, tool, run_id=run_id, task_id=task_id, **kwargs)
            mcp = self.results[-1] if self.results else MCPResult(
                tool_call_id="", analysis_run_id=run_id, task_id=task_id,
                provider=provider, status="completed", result_type=tool,
                method={"name": tool},
            )
            return raw, mcp
        except Exception as exc:
            mcp = self.results[-1] if self.results else MCPResult(
                tool_call_id="", analysis_run_id=run_id, task_id=task_id,
                provider=provider, status="failed", result_type="error",
                method={"name": tool}, error=str(exc),
            )
            return None, mcp

    # ── Tool schema aggregation (for LLM function calling) ───

    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """Aggregate OpenAI-format tool schemas from all registered MCP servers."""
        schemas: List[Dict[str, Any]] = []
        for provider, server in self.servers.items():
            tools_list = server.list_tools().get("tools", [])
            for t in tools_list:
                schemas.append({
                    "type": "function",
                    "function": {
                        "name": f"{t['provider']}.{t['name']}",
                        "description": t.get("description", ""),
                        "parameters": t.get("parameters", {}),
                    },
                })
        return schemas

    def list_all_tools(self) -> Dict[str, List[str]]:
        """Return a simple {provider: [tool_names]} mapping."""
        return {provider: sorted(server.tool_names) for provider, server in self.servers.items()}

    # ── Internal helpers ─────────────────────────────────────

    def _build_mcp_result(self, tool_call_id: str, run_id: str, task_id: str,
                          provider: str, tool: str, raw_result: Any) -> MCPResult:
        """Try to use the service's ``result()`` method, falling back to a generic wrapper."""
        server = self.servers.get(provider)
        if server is not None and hasattr(server, 'bridge') and hasattr(server.bridge, 'service'):
            service = server.bridge.service
            if hasattr(service, 'result'):
                try:
                    return service.result(run_id, task_id, tool_call_id, tool, raw_result)
                except Exception:
                    pass
        return MCPResult(
            tool_call_id=tool_call_id, analysis_run_id=run_id, task_id=task_id,
            provider=provider, status="completed", result_type=tool,
            method={"name": tool}, summary={"preview": str(self._preview_value(raw_result))},
        )

    @staticmethod
    def _preview_args(kwargs: Dict[str, Any]) -> Dict[str, Any]:
        return {key: MCPManager._preview_value(value) for key, value in kwargs.items()}

    @staticmethod
    def _preview_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value if not isinstance(value, str) or len(value) <= 160 else value[:157] + "..."
        if isinstance(value, list):
            return {"type": "list", "len": len(value)}
        if isinstance(value, dict):
            return {"type": "dict", "keys": list(value.keys())[:10]}
        if hasattr(value, "corpus_id"):
            return {"type": value.__class__.__name__, "corpus_id": value.corpus_id}
        if hasattr(value, "graph_snapshot_id"):
            return {"type": value.__class__.__name__, "snapshot_id": value.graph_snapshot_id}
        if hasattr(value, "evidence_bundle_id"):
            return {"type": value.__class__.__name__, "evidence_bundle_id": value.evidence_bundle_id}
        return {"type": value.__class__.__name__}
