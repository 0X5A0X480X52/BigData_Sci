"""Skill: build field and optional seed lineage corpora."""

from __future__ import annotations


def build_research_corpus(state, mcp, task):
    query_plan = state.get("openalex_query_plan", {})
    query = task.parameters.get("query") or query_plan.get("primary_query") or state.get("openalex_query") or state["question"]
    alternate_queries = task.parameters.get("alternate_queries") or query_plan.get("alternate_queries") or []
    corpus = mcp.call(
        "scholarly-data",
        "create_field_corpus",
        query=query,
        max_results=state["config"].max_field_corpus,
        alternate_queries=alternate_queries,
    )
    state["field_corpus"] = corpus
    seed_work_id = task.parameters.get("seed_work_id")
    if seed_work_id:
        state["seed_lineage_corpus"] = mcp.call("scholarly-data", "create_seed_lineage_corpus", seed_work_id=seed_work_id)
    return corpus


