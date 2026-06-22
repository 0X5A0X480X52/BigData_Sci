#!/usr/bin/env python
"""Run the research-agent MVP demo: offline fixture, real OpenAlex, optional LLM."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research_agent.runtime.runner import ResearchRunOptions, run_research_workflow


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Research Agent MVP demo")
    parser.add_argument("question", nargs="?", default="retrieval augmented generation for scientific discovery")
    parser.add_argument("--seed-work-id", default="")
    parser.add_argument("--artifact-root", default="artifacts")
    parser.add_argument("--max-field-corpus", type=int, default=30)
    parser.add_argument("--max-pdfs", type=int, default=5)
    parser.add_argument("--max-key-papers", type=int, default=10)
    parser.add_argument("--provider", choices=["fixture", "openalex"], default="fixture")
    parser.add_argument("--email", default="")
    parser.add_argument("--cache-dir", default=".cache/openalex")
    parser.add_argument("--mode", choices=["react", "planner_executor"], default="react")
    parser.add_argument("--config", default="", help="Reserved for YAML config compatibility")
    parser.add_argument("--sync-neo4j", action="store_true")
    parser.add_argument("--sync-es", action="store_true")
    parser.add_argument("--llm-react", action="store_true")
    parser.add_argument("--llm-plan", action="store_true")
    parser.add_argument("--llm-query", action="store_true", help="Use the configured LLM to rewrite the user question into an OpenAlex search query")
    parser.add_argument("--llm-base-url", default="")
    parser.add_argument("--llm-model", default="")
    parser.add_argument("--llm-api-key", default="")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    options = ResearchRunOptions(
        provider=args.provider,
        openalex_email=args.email,
        openalex_cache_dir=args.cache_dir,
        agent_mode=args.mode,
        artifact_root=args.artifact_root,
        max_field_corpus=args.max_field_corpus,
        max_pdfs=args.max_pdfs,
        max_key_papers=args.max_key_papers,
        seed_work_id=args.seed_work_id,
        llm_react=args.llm_react,
        llm_plan=args.llm_plan,
        llm_query=args.llm_query,
        llm_base_url=args.llm_base_url,
        llm_model=args.llm_model,
        llm_api_key=args.llm_api_key,
        neo4j_sync=args.sync_neo4j,
        es_sync=args.sync_es,
    )
    result = run_research_workflow(options, args.question, seed_work_id=args.seed_work_id or None)
    run = result.run

    provider_status = result.service_status.get("openalex", {})
    provider_label = provider_status.get("provider", args.provider)
    print(f"[provider] {provider_label} ({provider_status.get('status', 'configured')})")
    query_plan = result.service_status.get("openalex_query", {})
    if query_plan:
        print(f"[openalex_query] {query_plan.get('primary_query', '')} method={query_plan.get('method', '')}")
    if args.llm_react or args.llm_plan or args.llm_query:
        print(f"[llm] enabled model={args.llm_model or 'deepseek-chat'} fallback={'yes' if result.warnings else 'no'}")
    for warning in result.warnings:
        print(f"[warning] {warning}")

    print(f"run_id={run.run_id}")
    print(f"status={run.status}")
    print(f"agent_mode={run.agent_mode}")
    print(f"artifacts={args.artifact_root}/{run.run_id}")
    print(f"trace_events={len(run.trace)}")
    tool_events = [e for e in run.trace if e.get("type") == "tool_call"]
    print(f"tool_calls={len(tool_events)}")


if __name__ == "__main__":
    main()
