"""GPT Researcher facade — supplements scholarly evidence with web research.

Feature-flagged: enabled when ``FeatureFlags.gpt_researcher_mcp=True``.
All results are tagged ``evidence_type = web_research`` to prevent mixing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class GPTResearcherFacade:
    """Thin wrapper around gpt-researcher for web context.

    Only called when ``FeatureFlags.gpt_researcher_mcp=True``.
    All results isolated from scholarly evidence with ``evidence_type=web_research``.
    """

    def __init__(self) -> None:
        self._available = self._check_deps()

    @staticmethod
    def _check_deps() -> bool:
        try:
            import gpt_researcher
            return True
        except ImportError:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def research_technology_ecosystem(self, topic: str) -> List[Dict[str, Any]]:
        """Search for projects, tools, and datasets in the technology ecosystem."""
        if not self._available:
            return []
        return [{"topic": topic, "evidence_type": "web_research",
                 "summary": f"Web research for '{topic}' (GPT Researcher placeholder)"}]

    def find_official_project_resources(self, topic: str) -> List[Dict[str, Any]]:
        """Find official project pages, repos, and documentation."""
        if not self._available:
            return []
        return []

    def research_dataset_and_benchmark(self, topic: str) -> List[Dict[str, Any]]:
        """Find datasets and benchmarks related to the topic."""
        if not self._available:
            return []
        return []

    def supplement_non_paper_context(self, topic: str) -> List[Dict[str, Any]]:
        """Supplement with non-paper context (industry, policy, etc.)."""
        if not self._available:
            return []
        return []
