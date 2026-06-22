"""Deterministic v1 planner with task-status support."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from research_agent.core.models import TaskStatus
from research_agent.core.utils import stable_hash


@dataclass
class Task:
    task_id: str
    skill: str
    title: str
    depends_on: List[str] = field(default_factory=list)
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[Any] = None
    retries: int = 0


def build_default_plan(question: str, seed_work_id: str | None = None, search_query: str | None = None) -> List[Task]:
    corpus_query = search_query or question
    tasks = [
        Task("T1", "scope_new_field", "Scope the unfamiliar research field",
             parameters={"question": question}),
        Task("T2", "discover_research_perspectives", "Generate STORM-style research perspectives",
             depends_on=["T1"]),
        Task("T3", "build_research_corpus", "Build OpenAlex field corpus",
             depends_on=["T1", "T2"], parameters={"query": corpus_query}),
        Task("T4", "map_field_structure", "Map topics, trends and graph communities",
             depends_on=["T3"]),
        Task("T5", "identify_key_papers", "Rank key papers and assign roles",
             depends_on=["T4"]),
        Task("T6", "analyze_key_papers", "Materialize and retrieve evidence for selected papers",
             depends_on=["T5"]),
        Task("T7", "generate_field_guide", "Write field guide with evidence and reading route",
             depends_on=["T1", "T4", "T5", "T6"]),
    ]
    if seed_work_id:
        tasks[2].parameters["seed_work_id"] = seed_work_id
    return tasks

