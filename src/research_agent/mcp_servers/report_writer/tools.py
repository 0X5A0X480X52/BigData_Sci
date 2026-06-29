"""Report Writer MCP tool definitions."""

from __future__ import annotations

from typing import Any, Dict, List

TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "write_research_report": {
        "name": "write_research_report",
        "display_name": "Write Research Report",
        "description": "Write a final Markdown research report from corpus, graph key papers, and evidence bundle with citation markers.",
        "provider": "report-writer",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Original research question."},
                "corpus": {"type": "object", "description": "Corpus object or serialized corpus."},
                "field_structure": {"type": "object", "description": "Graph analytics result."},
                "key_papers": {"type": "array", "items": {"type": "object"}},
                "evidence_bundle": {"type": "object", "description": "EvidenceBundle object or serialized bundle."},
            },
            "required": ["question"],
        },
        "returns": {"type": "LLMReport", "description": "Markdown report with citations and source pack."},
        "idempotency": "Same inputs produce the same deterministic source pack; LLM wording may vary.",
    }
}


def get_tool_names() -> List[str]:
    return list(TOOL_DEFINITIONS.keys())


def get_tool_schema(tool_name: str) -> Dict[str, Any]:
    if tool_name not in TOOL_DEFINITIONS:
        raise ValueError(f"Unknown report-writer tool: {tool_name}")
    return TOOL_DEFINITIONS[tool_name]


def get_all_tool_schemas() -> List[Dict[str, Any]]:
    return list(TOOL_DEFINITIONS.values())


def get_openai_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"{tool['provider']}.{tool['name']}",
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
        for tool in TOOL_DEFINITIONS.values()
    ]


REPORT_WRITER_TOOLS = TOOL_DEFINITIONS
