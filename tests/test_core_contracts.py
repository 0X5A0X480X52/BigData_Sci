from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import EvidenceBundle, EvidenceRecord, MCPResult
from research_agent.core.utils import abstract_from_inverted_index, normalize_openalex_id


from pathlib import Path


def workspace_tmp(name: str):
    path = Path("outputs") / "test_artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_openalex_id_and_abstract_helpers():
    assert normalize_openalex_id("https://openalex.org/W123") == "W123"
    assert abstract_from_inverted_index({"hello": [0], "world": [1]}) == "hello world"


def test_artifact_store_and_contracts():
    tmp_path = workspace_tmp("core_contracts")
    store = ArtifactStore(tmp_path, "AR_TEST")
    ref = store.write_json("reports", "x.json", {"ok": True}, "unit")
    assert Path(ref.path).exists()

    result = MCPResult(
        tool_call_id="TC_1",
        analysis_run_id="AR_TEST",
        task_id="T1",
        provider="unit",
        status="completed",
        result_type="demo",
    )
    assert result.status == "completed"

    bundle = EvidenceBundle(
        evidence_bundle_id="EB_1",
        question="q",
        records=[
            EvidenceRecord(
                evidence_id="E1",
                work_id="W1",
                child_id="C1",
                parent_id="P1",
                query="q",
                child_text="evidence",
                parent_text="parent evidence",
                page=1,
                section="abstract",
                retrieval_score=0.5,
            )
        ],
    )
    assert bundle.records[0].support_status == "uncertain"
