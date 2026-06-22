"""MCP server facades for the research agent.

Each MCP server lives in its own sub-package (folder), making it
self-contained with tool definitions, service bridging, and an optional
stdio entry point.  The old flat ``.py`` files (ScholarlyDataMCP,
GraphAnalyticsMCP, EvidenceRAGMCP) have been replaced by this structure.
"""

from .evidence_rag.server import EvidenceRAGMCPServer
from .graph_analytics.server import GraphAnalyticsMCPServer
from .scholarly_data.server import ScholarlyDataMCPServer

# Backward-compatible aliases
ScholarlyDataMCP = ScholarlyDataMCPServer
GraphAnalyticsMCP = GraphAnalyticsMCPServer
EvidenceRAGMCP = EvidenceRAGMCPServer

__all__ = [
    "EvidenceRAGMCP",
    "EvidenceRAGMCPServer",
    "GraphAnalyticsMCP",
    "GraphAnalyticsMCPServer",
    "ScholarlyDataMCP",
    "ScholarlyDataMCPServer",
]
