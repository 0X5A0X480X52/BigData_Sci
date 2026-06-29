from pathlib import Path


def workspace_tmp(name: str) -> Path:
    path = Path("outputs") / "test_artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    return path

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import Corpus, EvidenceBundle, EvidenceRecord, FeatureFlags, Paper, RunConfig
from research_agent.mcp_servers.report_writer.server import ReportWriterMCPServer
from research_agent.mcp_servers.report_writer.service_bridge import ReportWriterServiceBridge
from research_agent.runtime.planner import Task
from research_agent.runtime.runner import ResearchRunOptions, run_research_workflow
from research_agent.services.report_writer import ReportWriterService
from research_agent.skills.write_llm_report import write_llm_report


def _sample_inputs():
    paper = Paper(
        work_id="W1",
        title="Retrieval Augmented Generation for Science",
        abstract="This paper studies corpus construction and evidence retrieval for scientific discovery.",
        publication_year=2024,
        cited_by_count=42,
    )
    corpus = Corpus(corpus_id="C1", query="retrieval augmented generation", papers=[paper])
    field_structure = {
        "snapshot_id": "G1",
        "node_count": 3,
        "edge_count": 2,
        "communities_count": 1,
        "topic_statistics": [{"topic": "retrieval augmented generation", "count": 1}],
        "key_papers": [
            {
                "work_id": "W1",
                "title": paper.title,
                "publication_year": 2024,
                "role": "central representative",
                "score": 0.91,
            }
        ],
    }
    evidence = EvidenceBundle(
        evidence_bundle_id="EB_TEST",
        question="retrieval augmented generation for scientific discovery",
        records=[
            EvidenceRecord(
                evidence_id="E_TEST",
                work_id="W1",
                child_id="CH1",
                parent_id="P1",
                query="retrieval augmented generation for scientific discovery",
                child_text="Evidence retrieval grounds scientific discovery claims in source snippets.",
                parent_text="Longer parent evidence context.",
                page=None,
                section="abstract",
                retrieval_score=0.8,
                support_status="supports",
            )
        ],
    )
    return corpus, field_structure, evidence


def test_report_writer_service_fallback_writes_artifacts(monkeypatch):
    tmp_path = workspace_tmp("report_writer_service")
    monkeypatch.delenv("RA_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    corpus, field_structure, evidence = _sample_inputs()
    service = ReportWriterService(ArtifactStore(tmp_path, "run"))

    report = service.write_research_report(
        "retrieval augmented generation for scientific discovery",
        corpus=corpus,
        field_structure=field_structure,
        evidence_bundle=evidence,
    )

    assert "[E1]" in report.markdown
    assert report.citations[0]["citation_id"] == "E1"
    assert report.warnings
    assert (tmp_path / "run" / "reports" / "field_guide.md").exists()
    assert (tmp_path / "run" / "reports" / "report_citations.json").exists()
    assert (tmp_path / "run" / "reports" / "report_source_pack.json").exists()


def test_report_writer_mcp_in_process(monkeypatch):
    tmp_path = workspace_tmp("report_writer_mcp")
    monkeypatch.delenv("RA_LLM_API_KEY", raising=False)
    corpus, field_structure, evidence = _sample_inputs()
    service = ReportWriterService(ArtifactStore(tmp_path, "mcp"))
    server = ReportWriterMCPServer(ReportWriterServiceBridge(service))

    report = server.call(
        "write_research_report",
        question="retrieval augmented generation for scientific discovery",
        corpus=corpus,
        field_structure=field_structure,
        evidence_bundle=evidence,
    )

    assert report.markdown.startswith("# Research Field Guide")
    assert report.citations


def test_write_llm_report_skill_updates_state(monkeypatch):
    tmp_path = workspace_tmp("report_writer_skill")
    monkeypatch.delenv("RA_LLM_API_KEY", raising=False)
    corpus, field_structure, evidence = _sample_inputs()
    service = ReportWriterService(ArtifactStore(tmp_path, "skill"))
    server = ReportWriterMCPServer(ReportWriterServiceBridge(service))

    class MiniMCP:
        def call(self, provider, tool, run_id="", task_id="", **kwargs):
            assert provider == "report-writer"
            return server.call(tool, **kwargs)

    state = {
        "run_id": "AR_TEST",
        "question": "retrieval augmented generation for scientific discovery",
        "field_corpus": corpus,
        "field_structure": field_structure,
        "evidence_bundle": evidence,
    }
    result = write_llm_report(state, MiniMCP(), Task("T7", "write_llm_report", "Write report"))

    assert result.markdown == state["field_guide"]
    assert state["report_citations"]


def test_workflow_planner_executor_llm_report_writer_generates_citations(monkeypatch):
    tmp_path = workspace_tmp("report_writer_workflow")
    monkeypatch.delenv("RA_LLM_API_KEY", raising=False)
    options = ResearchRunOptions(
        provider="fixture",
        agent_mode="planner_executor",
        artifact_root=str(tmp_path),
        max_field_corpus=12,
        max_key_papers=5,
        max_pdfs=2,
        llm_report_writer=True,
    )

    result = run_research_workflow(options, "retrieval augmented generation for scientific discovery")
    run_root = tmp_path / result.run.run_id

    assert result.run.status == "completed"
    assert (run_root / "reports" / "field_guide.md").exists()
    assert (run_root / "reports" / "report_citations.json").exists()
