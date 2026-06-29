"""Skill: write an LLM-backed final report with evidence citations."""

from __future__ import annotations


def write_llm_report(state, mcp, task):
    structure = state.get("field_structure", {})
    report = mcp.call(
        "report-writer",
        "write_research_report",
        question=state["question"],
        corpus=state.get("field_corpus"),
        field_structure=structure,
        key_papers=state.get("key_papers", structure.get("key_papers", [])),
        evidence_bundle=state.get("evidence_bundle"),
        run_id=state.get("run_id", ""),
        task_id=getattr(task, "task_id", "write_llm_report"),
    )
    state["field_guide"] = report.markdown
    state["llm_report"] = report
    state["report_citations"] = report.citations
    warnings = report.warnings or []
    if warnings:
        state.setdefault("warnings", []).extend(warnings)
    return report
