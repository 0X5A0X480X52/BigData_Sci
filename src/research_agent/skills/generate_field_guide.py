"""Skill: synthesize the final field guide."""

from __future__ import annotations


def generate_field_guide(state, mcp, task):
    structure = state["field_structure"]
    topics = structure["topic_statistics"][:8]
    key_papers = state.get("key_papers", [])[:10]
    bundle = state.get("evidence_bundle")
    evidence_lines = []
    if bundle:
        for record in bundle.records[:6]:
            evidence_lines.append(
                f"- {record.work_id} [{record.section or 'body'}]: {record.child_text[:220]} "
                f"(score={record.retrieval_score}, status={record.support_status})"
            )
    guide = "\n".join(
        [
            f"# Field Guide: {state['question']}",
            "",
            "## Boundary",
            state.get("scope", {}).get("field_definition", ""),
            "",
            "## Main Topics",
            *[f"- {item['topic']}: {item['count']} papers" for item in topics],
            "",
            "## Key Papers",
            *[
                f"- {item['title']} ({item.get('publication_year')}) - {item['role']} - score {item['score']}"
                for item in key_papers
            ],
            "",
            "## Evidence",
            *(evidence_lines or ["- No full-text evidence available; use abstract-level findings only."]),
            "",
            "## Reading Route",
            "1. Start with the highest-cited or community-representative papers.",
            "2. Compare bridge papers to understand how subfields connect.",
            "3. Read recent representatives to map current methods and benchmarks.",
            "",
            "## Next Questions",
            "- Which datasets and metrics dominate this field?",
            "- Which assumptions are debated across communities?",
            "- Which methods appear to be gaining momentum recently?",
        ]
    )
    ref = state["artifact_store"].write_text("reports", "field_guide.md", guide, "field_guide")
    state.setdefault("artifacts", []).append(ref)
    state["field_guide"] = guide
    return guide
