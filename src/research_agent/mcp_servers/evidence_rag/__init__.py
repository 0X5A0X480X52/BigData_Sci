"""Evidence RAG MCP Server.

Tools: ensure_fulltext_materialized, get_materialization_status,
       search_paper_evidence, get_parent_context, build_evidence_bundle,
       verify_claim_support.
"""

from .server import EvidenceRAGMCPServer
from .tools import EVIDENCE_RAG_TOOLS, get_all_tool_schemas

__all__ = ["EvidenceRAGMCPServer", "EVIDENCE_RAG_TOOLS", "get_all_tool_schemas"]
