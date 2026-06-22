"""Skill: scope an unfamiliar field."""

from __future__ import annotations

from research_agent.core.utils import simple_tokenize


def scope_new_field(state, mcp, task):
    question = task.parameters.get("question") or state["question"]
    terms = [t for t in simple_tokenize(question) if len(t) > 2][:12]
    query_plan = state.get("openalex_query_plan", {"primary_query": question, "keywords": terms})
    scope = {
        "field_definition": f"Working scope for: {question}",
        "included": ["core methods", "benchmark datasets", "representative applications", "limitations"],
        "excluded": ["unverified web claims", "non-scholarly commentary unless explicitly marked"],
        "year_range": "last 10 years plus foundational papers",
        "search_keywords": query_plan.get("keywords") or terms or [question],
        "openalex_query": query_plan.get("primary_query") or question,
        "candidate_openalex_topics": (query_plan.get("keywords") or terms)[:6],
    }
    state["scope"] = scope
    return scope

