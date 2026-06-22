from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import RunConfig
from research_agent.services.evidence_rag import EvidenceRAGService
from research_agent.services.graph_analytics import GraphAnalyticsService
from research_agent.services.scholarly_data import ScholarlyDataService


def workspace_tmp(name: str):
    from pathlib import Path

    path = Path("outputs") / "test_artifacts" / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_corpus_graph_and_rag_pipeline():
    tmp_path = workspace_tmp("scholarly_graph_rag")
    config = RunConfig(max_field_corpus=12, max_key_papers=5, max_pdfs=2, artifact_root=str(tmp_path))
    store = ArtifactStore(tmp_path, "AR_TEST")

    scholarly = ScholarlyDataService(store, config)
    corpus = scholarly.create_field_corpus("graph learning scientific discovery", max_results=12)
    assert len(corpus.papers) == 12
    assert len({p.work_id for p in corpus.papers}) == 12

    graph = GraphAnalyticsService(store, config)
    structure = graph.map_field_structure(corpus)
    assert structure["snapshot"].nodes
    assert len(structure["key_papers"]) <= 5

    rag = EvidenceRAGService(store, config)
    for item in structure["key_papers"][:2]:
        paper = next(p for p in corpus.papers if p.work_id == item["work_id"])
        status = rag.ensure_fulltext_materialized(paper)
        assert status["children"] > 0
    bundle = rag.build_evidence_bundle("What are the methods and limitations?", top_k=4)
    assert bundle.records
    verified = rag.verify_claim_support("methods limitations", bundle)
    assert verified.records[0].support_status in {"supports", "uncertain"}
