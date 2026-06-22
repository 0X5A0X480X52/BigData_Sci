"""Benchmark domain definitions and metric schemas."""

from dataclasses import dataclass, field
from typing import Any, Dict, List

BENCHMARK_DOMAINS = {
    "robotics": {
        "question": "learning-based grasp planning for robotic manipulation",
        "seed_work_id": None,
        "expected_min_papers": 50,
        "expected_communities_min": 2,
    },
    "graph_learning": {
        "question": "graph neural networks for molecular property prediction",
        "seed_work_id": None,
        "expected_min_papers": 50,
        "expected_communities_min": 2,
    },
    "information_systems": {
        "question": "large language models for enterprise knowledge management",
        "seed_work_id": None,
        "expected_min_papers": 50,
        "expected_communities_min": 2,
    },
}

BENCHMARK_MODES = {
    "quick": {
        "max_field_corpus": 30,
        "max_pdfs": 2,
        "max_key_papers": 3,
    },
    "standard": {
        "max_field_corpus": 200,
        "max_pdfs": 5,
        "max_key_papers": 10,
    },
}

ABLATION_FLAGS = [
    {"name": "base", "storm": False, "paperqa2": False, "gpt_researcher": False},
    {"name": "storm", "storm": True, "paperqa2": False, "gpt_researcher": False},
    {"name": "paperqa2", "storm": False, "paperqa2": True, "gpt_researcher": False},
    {"name": "gpt_researcher", "storm": False, "paperqa2": False, "gpt_researcher": True},
]


@dataclass
class BenchmarkMetrics:
    domain: str = ""
    mode: str = ""
    status: str = "pending"

    # Corpus
    total_papers: int = 0
    papers_with_abstract: int = 0
    papers_with_pdf: int = 0

    # Graph
    graph_nodes: int = 0
    graph_edges: int = 0
    num_communities: int = 0

    # Evidence
    evidence_records: int = 0

    # Agent
    total_tool_calls: int = 0
    failed_tool_calls: int = 0
    trace_events: int = 0

    features_active: List[str] = field(default_factory=list)
