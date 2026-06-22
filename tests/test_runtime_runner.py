from pathlib import Path

from research_agent.runtime.runner import ResearchRunOptions, run_research_workflow


def workspace_tmp(name: str) -> Path:
    path = Path("outputs") / "test_artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_runner_fixture_completes_with_artifacts():
    tmp_path = workspace_tmp("runner_fixture")
    options = ResearchRunOptions(
        provider="fixture",
        artifact_root=str(tmp_path),
        max_field_corpus=12,
        max_key_papers=5,
        max_pdfs=2,
    )
    result = run_research_workflow(options, "retrieval augmented generation for scientific discovery")

    assert result.run.status == "completed"
    run_root = tmp_path / result.run.run_id
    assert (run_root / "reports" / "field_guide.md").exists()
    assert (run_root / "reports" / "trace.json").exists()
    assert (run_root / "reports" / "run.json").exists()
    assert any(event["type"] == "tool_call" for event in result.run.trace)
    assert result.service_status["openalex"]["status"] == "offline_fixture"


def test_runner_degrades_missing_optional_services():
    tmp_path = workspace_tmp("runner_degraded")
    options = ResearchRunOptions(
        provider="fixture",
        artifact_root=str(tmp_path),
        max_field_corpus=10,
        max_key_papers=5,
        max_pdfs=2,
        persistence_backend="mysql",
        mysql_host="127.0.0.1",
        mysql_port=1,
        vector_backend="qdrant",
        qdrant_url="http://127.0.0.1:1",
        neo4j_sync=True,
        neo4j_uri="bolt://127.0.0.1:1",
    )
    result = run_research_workflow(options, "graph learning for scientific discovery")

    assert result.run.status == "completed"
    assert result.warnings
    assert (tmp_path / result.run.run_id / "reports" / "field_guide.md").exists()
    assert result.service_status["mysql"]["status"] == "degraded"
    assert result.sync_results["neo4j"]["status"] in {"degraded", "failed"}
