"""Benchmark runner — executes research runs across domains and modes."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from research_agent import ResearchAgent
from research_agent.core.models import FeatureFlags, RunConfig

from .config import (
    ABLATION_FLAGS,
    BENCHMARK_DOMAINS,
    BENCHMARK_MODES,
    BenchmarkMetrics,
)


class BenchmarkRunner:
    """Runs benchmarks across domains, modes, and ablation configurations."""

    def __init__(self, artifact_root: str = "outputs/benchmarks") -> None:
        self.artifact_root = artifact_root
        self.results: List[BenchmarkMetrics] = []

    def run_all(self, domains: Optional[List[str]] = None,
                modes: Optional[List[str]] = None) -> List[BenchmarkMetrics]:
        domains = domains or list(BENCHMARK_DOMAINS.keys())
        modes = modes or ["quick"]
        self.results = []

        for domain_key in domains:
            for mode_key in modes:
                domain = BENCHMARK_DOMAINS[domain_key]
                mode = BENCHMARK_MODES[mode_key]
                metrics = self.run_single(
                    domain_name=domain_key, question=domain["question"],
                    mode_name=mode_key, config_overrides=mode,
                )
                self.results.append(metrics)

        return self.results

    def run_ablation(self, domain_key: str = "graph_learning",
                     mode_key: str = "quick") -> List[BenchmarkMetrics]:
        domain = BENCHMARK_DOMAINS[domain_key]
        mode = BENCHMARK_MODES[mode_key]
        results = []

        for ablation in ABLATION_FLAGS:
            metrics = self.run_single(
                domain_name=domain_key, question=domain["question"],
                mode_name=f"{mode_key}_{ablation['name']}",
                config_overrides=mode,
                features=FeatureFlags(
                    storm_perspective_skill=ablation["storm"],
                    paperqa2_synthesis=ablation["paperqa2"],
                    gpt_researcher_mcp=ablation["gpt_researcher"],
                ),
            )
            results.append(metrics)

        return results

    def run_single(self, domain_name: str, question: str, mode_name: str,
                   config_overrides: Dict[str, Any],
                   features: Optional[FeatureFlags] = None) -> BenchmarkMetrics:
        config = RunConfig(
            max_field_corpus=config_overrides.get("max_field_corpus", 30),
            max_pdfs=config_overrides.get("max_pdfs", 2),
            max_key_papers=config_overrides.get("max_key_papers", 3),
            artifact_root=f"{self.artifact_root}/{domain_name}/{mode_name}",
            features=features or FeatureFlags(),
        )
        agent = ResearchAgent(config=config)
        run = agent.run(question)

        tool_events = [e for e in run.trace if e.get("type") == "tool_call"]
        failed_tools = [e for e in run.trace
                        if e.get("type") == "tool_result" and e.get("status") == "failed"]

        return BenchmarkMetrics(
            domain=domain_name, mode=mode_name,
            status=run.status,
            total_papers=len(run.artifacts),
            total_tool_calls=len(tool_events),
            failed_tool_calls=len(failed_tools),
            trace_events=len(run.trace),
        )

    def compare(self) -> str:
        """Generate a comparison markdown report."""
        if not self.results:
            return "# Benchmark Results\n\nNo results."

        lines = ["# Benchmark Results\n"]
        for r in self.results:
            lines.append(f"## {r.domain} / {r.mode}")
            lines.append(f"- Status: **{r.status}**")
            lines.append(f"- Tool calls: {r.total_tool_calls} (failed: {r.failed_tool_calls})")
            lines.append(f"- Trace events: {r.trace_events}")
            lines.append("")

        return "\n".join(lines)
