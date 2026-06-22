"""Skill: materialize key papers and build evidence."""

from __future__ import annotations


def analyze_key_papers(state, mcp, task):
    corpus = state["field_corpus"]
    by_id = {paper.work_id: paper for paper in corpus.papers}
    selected_ids = [item["work_id"] for item in state.get("key_papers", [])[: state["config"].max_pdfs]]
    materialized = []
    for work_id in selected_ids:
        paper = by_id.get(work_id)
        if paper:
            materialized.append(
                {
                    "work_id": work_id,
                    **mcp.call("evidence-rag", "ensure_fulltext_materialized", paper=paper),
                }
            )
    bundle = mcp.call("evidence-rag", "build_evidence_bundle", question=state["question"], work_ids=selected_ids, top_k=10)
    bundle = mcp.call("evidence-rag", "verify_claim_support", claim=state["question"], evidence_bundle=bundle)
    state["materialized_papers"] = materialized
    state["evidence_bundle"] = bundle
    return {"materialized": materialized, "evidence_bundle": bundle}
