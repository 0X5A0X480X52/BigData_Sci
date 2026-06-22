from pathlib import Path

from research_agent.runtime.query_planner import plan_openalex_query
from research_agent.runtime.runner import ResearchRunOptions, run_research_workflow


def workspace_tmp(name: str) -> Path:
    path = Path("outputs") / "test_artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_rule_based_query_planner_extracts_english_terms_from_chinese_request():
    plan = plan_openalex_query('帮我写一篇“transformer”相关综述', use_llm=False)

    assert plan.primary_query == "transformer"
    assert "transformer" in plan.keywords
    assert "帮" not in plan.primary_query


def test_runner_streams_query_plan_and_uses_rewritten_query():
    tmp_path = workspace_tmp("runner_query_plan")
    events = []
    options = ResearchRunOptions(
        provider="fixture",
        artifact_root=str(tmp_path),
        max_field_corpus=8,
        max_key_papers=4,
        max_pdfs=2,
    )

    result = run_research_workflow(
        options,
        '帮我写一篇“transformer”相关综述',
        event_callback=events.append,
    )

    assert result.run.status == "completed"
    assert result.service_status["openalex_query"]["primary_query"] == "transformer"
    assert any(event.get("type") == "openalex_query_planned" for event in events)
    corpus_files = list((tmp_path / result.run.run_id / "corpora").glob("*.json"))
    assert corpus_files
