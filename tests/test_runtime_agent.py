from research_agent import ResearchAgent
from research_agent.core.models import RunConfig


def workspace_tmp(name: str):
    from pathlib import Path

    path = Path("outputs") / "test_artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_runtime_executes_offline_closed_loop():
    tmp_path = workspace_tmp("runtime_agent")
    config = RunConfig(max_field_corpus=15, max_key_papers=5, max_pdfs=2, artifact_root=str(tmp_path))
    run = ResearchAgent(config=config).run("retrieval augmented generation for scientific discovery")
    assert run.status == "completed"
    run_root = tmp_path / run.run_id
    # Core artifacts present in both ReAct and Planner-Executor modes
    assert (run_root / "reports" / "trace.json").exists()
    assert (run_root / "reports" / "run.json").exists()
    assert (run_root / "reports" / "field_guide.md").exists()
    # At least one tool_call event was recorded
    assert any(event["type"] == "tool_call" for event in run.trace)
