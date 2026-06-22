"""Scholarly Data MCP → ScholarlyDataService bridge."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Dict

from research_agent.services.scholarly_data import ScholarlyDataService
from research_agent.mcp_servers.common.errors import MCPError, MCPErrorCode


class ScholarlyDataServiceBridge:
    """Maps MCP tool calls to ``ScholarlyDataService`` methods.

    Responsibilities
    ----------------
    * Validate arguments against the tool schema (basic presence checks).
    * Dispatch to the correct ``ScholarlyDataService`` method.
    * Wrap the return value in a standardised MCP result dict.
    * Catch and standardise errors.
    """

    def __init__(self, service: ScholarlyDataService) -> None:
        self.service = service
        self._handlers: Dict[str, Callable[..., Any]] = {
            "create_field_corpus": self._handle_create_field_corpus,
            "create_seed_lineage_corpus": self._handle_create_seed_lineage_corpus,
            "expand_references": self._handle_expand_references,
            "expand_citing_works": self._handle_expand_citing_works,
            "get_corpus_summary": self._handle_get_corpus_summary,
            "get_work": self._handle_get_work,
            "list_candidate_papers": self._handle_list_candidate_papers,
        }

    # ── dispatch ──────────────────────────────────────────────

    def dispatch(self, tool_name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        """Route *tool_name* to its handler and return the raw result.

        The caller (MCPServer or MCPManager) is responsible for wrapping the
        return value into an ``MCPResult`` when needed.
        """
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown scholarly-data tool: {tool_name}")
        try:
            return handler(arguments)
        except Exception as exc:
            raise RuntimeError(f"[scholarly-data.{tool_name}] {exc}") from exc

    # ── per-tool handlers ────────────────────────────────────

    def _handle_create_field_corpus(self, args: Dict[str, Any]) -> Any:
        return self.service.create_field_corpus(
            query=args["query"],
            max_results=args.get("max_results"),
            alternate_queries=args.get("alternate_queries"),
        )

    def _handle_create_seed_lineage_corpus(self, args: Dict[str, Any]) -> Any:
        return self.service.create_seed_lineage_corpus(
            seed_work_id=args["seed_work_id"],
            max_depth=args.get("max_depth"),
            max_results=args.get("max_results"),
        )

    def _handle_expand_references(self, args: Dict[str, Any]) -> Any:
        return self.service.expand_references(
            work_id=args["work_id"],
            max_results=args.get("max_results", 50),
        )

    def _handle_expand_citing_works(self, args: Dict[str, Any]) -> Any:
        return self.service.expand_citing_works(
            work_id=args["work_id"],
            max_results=args.get("max_results", 50),
        )

    def _handle_get_corpus_summary(self, args: Dict[str, Any]) -> Any:
        return self.service.get_corpus_summary(corpus_id=args["corpus_id"])

    def _handle_get_work(self, args: Dict[str, Any]) -> Any:
        return self.service.get_work(work_id=args["work_id"])

    def _handle_list_candidate_papers(self, args: Dict[str, Any]) -> Any:
        return self.service.list_candidate_papers(
            corpus_id=args["corpus_id"],
            limit=args.get("limit", 20),
        )





