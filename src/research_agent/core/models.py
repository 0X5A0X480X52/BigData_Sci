"""Shared data contracts for the first-stage research agent MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from .utils import stable_hash, utc_now_iso


# ── Agent mode ───────────────────────────────────────────────

AgentMode = Literal["react", "planner_executor"]


# ── Task status ──────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── New runtime data classes ─────────────────────────────────

@dataclass
class TaskResult:
    """Outcome of a single Task execution."""
    task_id: str
    skill: str
    status: TaskStatus = TaskStatus.PENDING
    mcp_results: List[MCPResult] = field(default_factory=list)
    artifacts: List[ArtifactRef] = field(default_factory=list)
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retries: int = 0


@dataclass
class Observation:
    """A structured observation produced by the ReAct observe node."""
    observation_id: str
    timestamp: str = field(default_factory=utc_now_iso)
    source_tool_call_id: Optional[str] = None
    summary: str = ""
    key_findings: List[str] = field(default_factory=list)
    data_refs: List[ArtifactRef] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def from_mcp_result(cls, mcp_result: MCPResult, summary: str = "") -> Observation:
        return cls(
            observation_id=f"OBS_{stable_hash({'tool_call_id': mcp_result.tool_call_id, 'ts': utc_now_iso()}, 12)}",
            source_tool_call_id=mcp_result.tool_call_id,
            summary=summary or mcp_result.summary.get("preview", str(mcp_result.preview)[:200]),
            warnings=list(mcp_result.warnings),
        )


@dataclass
class ToolCall:
    """Describes a pending or completed tool invocation."""
    provider: str
    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    tool_call_id: Optional[str] = None


def to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return {k: to_dict(v) for k, v in asdict(value).items()}
    if isinstance(value, list):
        return [to_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: to_dict(v) for k, v in value.items()}
    return value


@dataclass
class FeatureFlags:
    # ── Agent runtime ──
    use_langgraph_runtime: bool = True
    llm_driven_react: bool = False       # LLM drives ReAct think (else deterministic)
    llm_driven_plan: bool = False        # LLM generates plan (else default template)

    # ── Skills ──
    storm_perspective_skill: bool = True
    paperqa2_synthesis: bool = False
    gpt_researcher_mcp: bool = False

    # ── Storage backends (optional) ──
    neo4j_sync: bool = False
    es_sync: bool = False
    qdrant_sync: bool = False
    auto_embed: bool = True              # Auto-trigger embedding after corpus creation

    # ── Baseline / Sidecar ──
    litstudy_baseline: bool = False
    bibliometrix_sidecar: bool = False


@dataclass
class RunConfig:
    # ── Corpus ──
    max_field_corpus: int = 3000
    max_seed_lineage: int = 2000
    max_bfs_depth: int = 2

    # ── Graph ──
    max_graph_nodes: int = 10000
    max_graph_edges: int = 50000
    max_key_papers: int = 15

    # ── PDF / Evidence ──
    max_pdfs: int = 8
    max_chunks: int = 10000

    # ── Budget ──
    max_tool_calls: int = 80
    max_retries: int = 2
    max_iterations: int = 15          # Max ReAct loops

    # ── Agent mode ──
    agent_mode: AgentMode = "react"    # "react" | "planner_executor"

    # ── Storage ──
    artifact_root: str = "artifacts"

    features: FeatureFlags = field(default_factory=FeatureFlags)


@dataclass
class ArtifactRef:
    artifact_id: str
    path: str
    result_type: str
    media_type: str
    created_at: str = field(default_factory=utc_now_iso)
    summary: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPResult:
    tool_call_id: str
    analysis_run_id: str
    task_id: str
    provider: str
    status: Literal["completed", "failed", "skipped"]
    result_type: str
    scope: Dict[str, Any] = field(default_factory=dict)
    method: Dict[str, Any] = field(default_factory=dict)
    summary: Dict[str, Any] = field(default_factory=dict)
    preview: List[Dict[str, Any]] = field(default_factory=list)
    artifact_id: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    provenance: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class Paper:
    work_id: str
    title: str
    abstract: str = ""
    publication_year: Optional[int] = None
    cited_by_count: int = 0
    authors: List[str] = field(default_factory=list)
    topics: List[str] = field(default_factory=list)
    referenced_works: List[str] = field(default_factory=list)
    citing_works: List[str] = field(default_factory=list)
    doi: Optional[str] = None
    open_access_pdf_url: Optional[str] = None
    source: str = "fixture"
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Corpus:
    corpus_id: str
    query: str
    papers: List[Paper]
    member_sources: Dict[str, List[str]] = field(default_factory=dict)
    data_cutoff: str = field(default_factory=utc_now_iso)
    warnings: List[str] = field(default_factory=list)


@dataclass
class GraphSnapshot:
    graph_snapshot_id: str
    corpus_id: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    algorithm_version: str = "graph-snapshot-v1"
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParentChunk:
    parent_id: str
    work_id: str
    page: Optional[int]
    section: str
    start_child_index: int
    end_child_index: int
    text: str
    content_hash: str


@dataclass
class ChildChunk:
    child_id: str
    parent_id: str
    work_id: str
    page: Optional[int]
    section: str
    child_index: int
    char_start: int
    char_end: int
    token_start: int
    token_end: int
    text: str
    content_hash: str
    embedding: List[float] = field(default_factory=list)


@dataclass
class EvidenceRecord:
    evidence_id: str
    work_id: str
    child_id: str
    parent_id: str
    query: str
    child_text: str
    parent_text: str
    page: Optional[int]
    section: str
    retrieval_score: float
    support_status: Literal["supports", "contradicts", "uncertain"] = "uncertain"


@dataclass
class EvidenceBundle:
    evidence_bundle_id: str
    question: str
    records: List[EvidenceRecord]
    paper_metadata: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)


# ── Graph entity node types ─────────────────────────────────

@dataclass
class AuthorNode:
    author_id: str
    display_name: str
    orcid: Optional[str] = None
    paper_count: int = 0

@dataclass
class TopicNode:
    topic_id: str
    display_name: str
    subfield: Optional[str] = None
    paper_count: int = 0

@dataclass
class InstitutionNode:
    institution_id: str
    display_name: str
    country_code: Optional[str] = None
    type: Optional[str] = None

@dataclass
class VenueNode:
    venue_id: str
    display_name: str
    issn: Optional[str] = None
    type: str = "journal"


@dataclass
class ResearchRun:
    run_id: str
    question: str
    config: RunConfig
    status: Literal["created", "running", "completed", "failed"] = "created"
    agent_mode: AgentMode = "react"
    trace: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[ArtifactRef] = field(default_factory=list)
    results: List[MCPResult] = field(default_factory=list)
    task_results: List[TaskResult] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: Optional[str] = None
