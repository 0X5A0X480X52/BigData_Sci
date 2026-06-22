"""Standardised MCP error codes for the research agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class MCPErrorCode:
    """JSON-RPC and research-agent-specific error codes."""

    # ── JSON-RPC standard errors ──
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    # ── Research-agent custom errors (-32000 – -32099) ──
    SERVICE_UNAVAILABLE = -32000    # Backend service unavailable (degraded automatically)
    RATE_LIMITED = -32001           # Rate limited (auto-backoff applied)
    RESULT_TOO_LARGE = -32002       # Result too large, artifactised
    BUDGET_EXCEEDED = -32003        # Tool-call or iteration budget exhausted

    # ── Data-layer errors ──
    PDF_DOWNLOAD_FAILED = -32010    # PDF download failed (degraded to abstract)
    EMBEDDING_FAILED = -32011       # Embedding generation failed (degraded to hash)
    OPENALEX_UNAVAILABLE = -32012   # OpenAlex API unreachable (degraded to fixture)
    SEED_NOT_FOUND = -32013         # Seed work not found in OpenAlex
    CORPUS_EMPTY = -32014           # Search returned zero results

    # ── Storage errors ──
    MYSQL_UNAVAILABLE = -32020      # MySQL unreachable
    NEO4J_UNAVAILABLE = -32021      # Neo4j unreachable
    ES_UNAVAILABLE = -32022         # Elasticsearch unreachable
    QDRANT_UNAVAILABLE = -32023     # Qdrant unreachable


@dataclass
class MCPError:
    """A structured MCP error suitable for JSON-RPC error responses."""
    code: int
    message: str
    data: Optional[Dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data:
            result["data"] = self.data
        return result
