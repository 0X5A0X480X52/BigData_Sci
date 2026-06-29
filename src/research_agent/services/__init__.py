"""Local service implementations used behind MCP facades."""

from .evidence_rag import EvidenceRAGService
from .graph_analytics import GraphAnalyticsService
from .report_writer import ReportWriterService
from .scholarly_data import ScholarlyDataService

__all__ = ["EvidenceRAGService", "GraphAnalyticsService", "ReportWriterService", "ScholarlyDataService"]
