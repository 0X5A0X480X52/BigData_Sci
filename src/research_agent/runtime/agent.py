"""Research Agent top-level entry point."""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.config import load_run_config
from research_agent.core.models import ResearchRun, RunConfig
from research_agent.mcp_servers.evidence_rag.server import EvidenceRAGMCPServer
from research_agent.mcp_servers.evidence_rag.service_bridge import EvidenceRAGServiceBridge
from research_agent.mcp_servers.graph_analytics.server import GraphAnalyticsMCPServer
from research_agent.mcp_servers.graph_analytics.service_bridge import GraphAnalyticsServiceBridge
from research_agent.mcp_servers.scholarly_data.server import ScholarlyDataMCPServer
from research_agent.mcp_servers.scholarly_data.service_bridge import ScholarlyDataServiceBridge
from research_agent.services.evidence_rag import EvidenceRAGService
from research_agent.services.graph_analytics import GraphAnalyticsService
from research_agent.services.scholarly_data import ScholarlyDataService

from .budget import BudgetTracker
from .graph_agent import ResearchGraphAgent
from .mcp_manager import MCPManager
from .trace import TraceRecorder


class ResearchAgent:
    """Top-level research agent entry point with injectable services."""

    def __init__(
        self,
        config: Optional[RunConfig] = None,
        scholarly_client: Any = None,
        repository: Any = None,
        openalex_source: Any = None,
        pdf_manager: Any = None,
        parser: Any = None,
        embedder: Any = None,
        vector_store: Any = None,
        openalex_query_plan: Optional[Dict[str, Any]] = None,
        trace_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config or load_run_config()
        self.scholarly_client = scholarly_client
        self.repository = repository
        self.openalex_source = openalex_source
        self.pdf_manager = pdf_manager
        self.parser = parser
        self.embedder = embedder
        self.vector_store = vector_store
        self.openalex_query_plan = openalex_query_plan or {}
        self.trace_callback = trace_callback

    def run(self, question: str, seed_work_id: Optional[str] = None) -> ResearchRun:
        trace = TraceRecorder(on_event=self.trace_callback)
        budget = BudgetTracker(self.config)
        placeholder_store = ArtifactStore(self.config.artifact_root, "pending")

        scholarly_service = ScholarlyDataService(
            placeholder_store,
            self.config,
            client=self.scholarly_client,
            repository=self.repository,
            openalex_source=self.openalex_source,
        )
        graph_service = GraphAnalyticsService(placeholder_store, self.config, repository=self.repository)
        evidence_service = EvidenceRAGService(
            placeholder_store,
            self.config,
            pdf_manager=self.pdf_manager,
            parser=self.parser,
            embedder=self.embedder,
            vector_store=self.vector_store,
            repository=self.repository,
        )

        mcp_servers = {
            "scholarly-data": ScholarlyDataMCPServer(ScholarlyDataServiceBridge(scholarly_service)),
            "graph-analytics": GraphAnalyticsMCPServer(GraphAnalyticsServiceBridge(graph_service)),
            "evidence-rag": EvidenceRAGMCPServer(EvidenceRAGServiceBridge(evidence_service)),
        }
        mcp = MCPManager(mcp_servers, budget, trace)

        graph_agent = ResearchGraphAgent(
            config=self.config,
            mcp=mcp,
            repository=self.repository,
            artifact_root=self.config.artifact_root,
            trace=trace,
            budget=budget,
            services=(scholarly_service, graph_service, evidence_service),
        )
        return graph_agent.run(question, seed_work_id, openalex_query_plan=self.openalex_query_plan)

