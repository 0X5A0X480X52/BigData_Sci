"""Evidence RAG MCP → EvidenceRAGService bridge."""

from __future__ import annotations

from typing import Any, Callable, Dict

from research_agent.services.evidence_rag import EvidenceRAGService


class EvidenceRAGServiceBridge:
    """Maps MCP tool calls to ``EvidenceRAGService`` methods."""

    def __init__(self, service: EvidenceRAGService) -> None:
        self.service = service
        self._handlers: Dict[str, Callable[..., Any]] = {
            "ensure_fulltext_materialized": self._handle_ensure_fulltext_materialized,
            "get_materialization_status": self._handle_get_materialization_status,
            "search_paper_evidence": self._handle_search_paper_evidence,
            "get_parent_context": self._handle_get_parent_context,
            "build_evidence_bundle": self._handle_build_evidence_bundle,
            "verify_claim_support": self._handle_verify_claim_support,
        }

    def dispatch(self, tool_name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown evidence-rag tool: {tool_name}")
        try:
            return handler(arguments)
        except Exception as exc:
            raise RuntimeError(f"[evidence-rag.{tool_name}] {exc}") from exc

    def _handle_ensure_fulltext_materialized(self, args: Dict[str, Any]) -> Any:
        return self.service.ensure_fulltext_materialized(
            paper=args["paper"],
            text=args.get("text"),
            pdf_path=args.get("pdf_path"),
        )

    def _handle_get_materialization_status(self, args: Dict[str, Any]) -> Any:
        return self.service.get_materialization_status(work_id=args["work_id"])

    def _handle_search_paper_evidence(self, args: Dict[str, Any]) -> Any:
        return self.service.search_paper_evidence(
            query=args["query"],
            work_ids=args.get("work_ids"),
            top_k=args.get("top_k", 10),
        )

    def _handle_get_parent_context(self, args: Dict[str, Any]) -> Any:
        return self.service.get_parent_context(parent_id=args["parent_id"])

    def _handle_build_evidence_bundle(self, args: Dict[str, Any]) -> Any:
        return self.service.build_evidence_bundle(
            question=args["question"],
            work_ids=args.get("work_ids"),
            top_k=args.get("top_k", 10),
        )

    def _handle_verify_claim_support(self, args: Dict[str, Any]) -> Any:
        return self.service.verify_claim_support(
            claim=args["claim"],
            evidence_bundle=args["evidence_bundle"],
            min_score=args.get("min_score", 0.1),
        )
