"""Abstract repository interface for research agent persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from research_agent.core.models import Corpus, GraphSnapshot, MCPResult, ResearchRun, TaskResult


class ResearchRepository(ABC):
    """Abstract persistence interface for all research agent data.

    The default implementation is ``MySQLResearchRepository``.
    All write methods should be idempotent (INSERT ... ON DUPLICATE KEY UPDATE).
    """

    # ── Run lifecycle ────────────────────────────────────────

    @abstractmethod
    def create_run(self, run: ResearchRun) -> None:
        """Persist a new analysis run."""
        ...

    @abstractmethod
    def update_run_status(self, run_id: str, status: str, completed_at: Optional[str] = None) -> None:
        """Update run status and optional completion time."""
        ...

    @abstractmethod
    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a run by ID."""
        ...

    @abstractmethod
    def list_runs(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """List recent runs."""
        ...

    @abstractmethod
    def save_run_outputs(self, run: ResearchRun) -> None:
        """Persist final trace, artifacts and task results for a run."""
        ...
    # ── Task tracking ────────────────────────────────────────

    @abstractmethod
    def save_task(self, run_id: str, task: Any) -> None:
        """Persist a Task (from planner).  Uses ON DUPLICATE KEY UPDATE."""
        ...

    @abstractmethod
    def update_task_status(self, run_id: str, task_id: str, status: str,
                           error: Optional[str] = None) -> None:
        """Update a single task's status."""
        ...

    @abstractmethod
    def save_task_result(self, run_id: str, task_result: TaskResult) -> None:
        """Persist a completed/failed TaskResult."""
        ...

    # ── MCP tool calls ───────────────────────────────────────

    @abstractmethod
    def save_mcp_result(self, mcp_result: MCPResult) -> None:
        """Persist a single MCPResult."""
        ...

    @abstractmethod
    def get_mcp_results_for_run(self, run_id: str) -> List[Dict[str, Any]]:
        """Retrieve all MCPResults for a run."""
        ...

    # ── Corpus management ────────────────────────────────────

    @abstractmethod
    def create_corpus(self, corpus: Corpus, run_id: str) -> None:
        """Persist a corpus record."""
        ...

    @abstractmethod
    def get_corpus(self, corpus_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve a corpus record by ID."""
        ...

    @abstractmethod
    def find_corpus_by_hash(self, query_hash: str) -> Optional[str]:
        """Return corpus_id if a corpus with this query_hash exists."""
        ...

    @abstractmethod
    def upsert_corpus_membership(self, corpus_id: str, work_id: str, source: str) -> None:
        """Idempotently add a paper to a corpus."""
        ...

    @abstractmethod
    def get_corpus_members(self, corpus_id: str) -> List[str]:
        """Return all work_ids in a corpus."""
        ...

    # ── Crawl frontier ───────────────────────────────────────

    @abstractmethod
    def upsert_frontier(self, work_id: str, corpus_id: str, depth: int, source: str) -> None:
        """Add a work to the crawl frontier (pending)."""
        ...

    @abstractmethod
    def update_frontier_status(self, work_id: str, corpus_id: str, status: str,
                               error: Optional[str] = None) -> None:
        """Mark a frontier entry as completed or failed."""
        ...

    @abstractmethod
    def get_pending_frontier(self, corpus_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieve pending frontier entries ordered by depth ASC."""
        ...

    @abstractmethod
    def find_frontier_by_work(self, corpus_id: str, work_id: str) -> Optional[Dict[str, Any]]:
        """Check whether a specific work exists in the crawl frontier."""
        ...

    # ── Graph persistence ────────────────────────────────────

    @abstractmethod
    def save_graph_snapshot(self, snapshot: GraphSnapshot) -> None:
        """Persist a graph snapshot metadata record."""
        ...

    @abstractmethod
    def save_graph_algorithm_run(self, run_record: Dict[str, Any]) -> None:
        """Persist an algorithm execution record."""
        ...

    # ── Evidence / PDF ───────────────────────────────────────

    @abstractmethod
    def save_materialization_job(self, job: Dict[str, Any]) -> None:
        """Record a PDF materialization job."""
        ...

    @abstractmethod
    def update_materialization_job(self, job_id: str, status: str, **kwargs: Any) -> None:
        """Update a materialization job's status and optional fields."""
        ...

    @abstractmethod
    def save_paper_file(self, work_id: str, sha256: str, storage_key: str, file_size: int) -> None:
        """Record a downloaded paper file (SHA-256 dedup)."""
        ...

    @abstractmethod
    def get_paper_file(self, work_id: str) -> Optional[Dict[str, Any]]:
        """Get paper file record by work_id."""
        ...

    @abstractmethod
    def save_chunk_run(self, chunk_run: Dict[str, Any]) -> None:
        """Persist a chunk/embedding run record."""
        ...

    # ── Health ───────────────────────────────────────────────

    @abstractmethod
    def health_check(self) -> bool:
        """Return True if the storage backend is reachable."""
        ...

