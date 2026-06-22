"""Graph Analytics MCP → GraphAnalyticsService bridge."""

from __future__ import annotations

from typing import Any, Callable, Dict

from research_agent.services.graph_analytics import GraphAnalyticsService


class GraphAnalyticsServiceBridge:
    """Maps MCP tool calls to ``GraphAnalyticsService`` methods."""

    def __init__(self, service: GraphAnalyticsService) -> None:
        self.service = service
        self._handlers: Dict[str, Callable[..., Any]] = {
            "build_graph_snapshot": self._handle_build_graph_snapshot,
            "run_pagerank": self._handle_run_pagerank,
            "detect_communities": self._handle_detect_communities,
            "rank_key_papers": self._handle_rank_key_papers,
            "find_bridge_papers": self._handle_find_bridge_papers,
            "compute_topic_statistics": self._handle_compute_topic_statistics,
            "compute_yearly_trend": self._handle_compute_yearly_trend,
            "map_field_structure": self._handle_map_field_structure,
        }

    def dispatch(self, tool_name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown graph-analytics tool: {tool_name}")
        try:
            return handler(arguments)
        except Exception as exc:
            raise RuntimeError(f"[graph-analytics.{tool_name}] {exc}") from exc

    def _handle_build_graph_snapshot(self, args: Dict[str, Any]) -> Any:
        return self.service.build_graph_snapshot(
            corpus=args["corpus"],
            parameters=args.get("parameters"),
        )

    def _handle_run_pagerank(self, args: Dict[str, Any]) -> Any:
        return self.service.run_pagerank(
            snapshot=args["snapshot"],
            iterations=args.get("iterations", 30),
            damping=args.get("damping", 0.85),
        )

    def _handle_detect_communities(self, args: Dict[str, Any]) -> Any:
        return self.service.detect_communities(snapshot=args["snapshot"])

    def _handle_rank_key_papers(self, args: Dict[str, Any]) -> Any:
        return self.service.rank_key_papers(
            corpus=args["corpus"],
            snapshot=args["snapshot"],
            limit=args.get("limit", 15),
        )

    def _handle_find_bridge_papers(self, args: Dict[str, Any]) -> Any:
        return self.service.find_bridge_papers(
            snapshot=args["snapshot"],
            communities=args["communities"],
        )

    def _handle_compute_topic_statistics(self, args: Dict[str, Any]) -> Any:
        return self.service.compute_topic_statistics(
            corpus=args["corpus"],
            top_k=args.get("top_k", 20),
        )

    def _handle_compute_yearly_trend(self, args: Dict[str, Any]) -> Any:
        return self.service.compute_yearly_trend(corpus=args["corpus"])

    def _handle_map_field_structure(self, args: Dict[str, Any]) -> Any:
        return self.service.map_field_structure(corpus=args["corpus"])
