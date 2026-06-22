"""Skill: generate STORM-style research perspectives."""

from __future__ import annotations


def discover_research_perspectives(state, mcp, task):
    question = state["question"]
    base_keywords = state.get("scope", {}).get("search_keywords", [question])
    names = [
        "theoretical foundations",
        "main methods",
        "datasets and metrics",
        "applications",
        "limitations and controversies",
        "recent trends",
    ]
    perspectives = [
        {
            "name": name,
            "retrieval_question": f"{question} {name}",
            "keywords": base_keywords[:6] + [name],
            "exclude_terms": ["news", "blog"] if name != "applications" else [],
            "time_range": "recent 5 years" if name == "recent trends" else "all years",
        }
        for name in names
    ]
    state["perspectives"] = perspectives
    return perspectives
