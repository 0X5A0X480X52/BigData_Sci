"""Evidence RAG MCP — tool definitions and JSON Schemas."""

from __future__ import annotations

from typing import Any, Dict, List

TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "ensure_fulltext_materialized": {
        "name": "ensure_fulltext_materialized",
        "display_name": "Ensure Full-text Materialized",
        "description": (
            "Download PDF (if available), parse, split into parent-child chunks. "
            "Falls back to title+abstract when PDF is unavailable."
        ),
        "provider": "evidence-rag",
        "parameters": {
            "type": "object",
            "properties": {
                "paper": {"type": "object", "description": "Paper object with work_id, title, open_access_pdf_url."},
                "text": {"type": "string", "description": "Optional pre-fetched text; overrides PDF."},
                "pdf_path": {"type": "string", "description": "Optional local PDF path."},
            },
            "required": ["paper"],
        },
        "returns": {"type": "Dict", "description": "Materialization status with child/parent counts."},
        "errors": [
            {"code": "PDF_DOWNLOAD_FAILED", "description": "PDF download failed; abstract fallback used."},
            {"code": "EMBEDDING_FAILED", "description": "Embedding failed; hash fallback used."},
        ],
    },
    "get_materialization_status": {
        "name": "get_materialization_status",
        "display_name": "Get Materialization Status",
        "description": "Check whether a paper has been materialized and how many children exist.",
        "provider": "evidence-rag",
        "parameters": {
            "type": "object",
            "properties": {
                "work_id": {"type": "string", "description": "OpenAlex Work ID."},
            },
            "required": ["work_id"],
        },
        "returns": {"type": "Dict", "description": "Status with child_count and parent_count."},
    },
    "search_paper_evidence": {
        "name": "search_paper_evidence",
        "display_name": "Search Paper Evidence",
        "description": "Semantic search over materialized paper chunks for evidence.",
        "provider": "evidence-rag",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural-language evidence query."},
                "work_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict search to these work IDs.",
                },
                "top_k": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
        },
        "returns": {"type": "List[EvidenceRecord]", "description": "Retrieved evidence records with scores."},
    },
    "get_parent_context": {
        "name": "get_parent_context",
        "display_name": "Get Parent Context",
        "description": "Retrieve the parent chunk that contains a given child chunk.",
        "provider": "evidence-rag",
        "parameters": {
            "type": "object",
            "properties": {
                "parent_id": {"type": "string", "description": "Parent chunk ID."},
            },
            "required": ["parent_id"],
        },
        "returns": {"type": "ParentChunk", "description": "Parent chunk with surrounding context."},
    },
    "build_evidence_bundle": {
        "name": "build_evidence_bundle",
        "display_name": "Build Evidence Bundle",
        "description": "Search evidence across papers and bundle results with metadata.",
        "provider": "evidence-rag",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Research question."},
                "work_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paper IDs to search within.",
                },
                "top_k": {"type": "integer", "default": 10, "minimum": 1, "maximum": 100},
            },
            "required": ["question"],
        },
        "returns": {"type": "EvidenceBundle", "description": "Bundle with evidence records and paper metadata."},
    },
    "verify_claim_support": {
        "name": "verify_claim_support",
        "display_name": "Verify Claim Support",
        "description": "Check whether evidence records support or contradict a claim.",
        "provider": "evidence-rag",
        "parameters": {
            "type": "object",
            "properties": {
                "claim": {"type": "string", "description": "The claim to verify."},
                "evidence_bundle": {"type": "object", "description": "EvidenceBundle to check against."},
                "min_score": {"type": "number", "default": 0.1, "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["claim", "evidence_bundle"],
        },
        "returns": {"type": "List[EvidenceRecord]", "description": "Records with support_status updated."},
    },
}


def get_tool_names() -> List[str]:
    return list(TOOL_DEFINITIONS.keys())


def get_tool_schema(tool_name: str) -> Dict[str, Any]:
    if tool_name not in TOOL_DEFINITIONS:
        raise ValueError(f"Unknown evidence-rag tool: {tool_name}")
    return TOOL_DEFINITIONS[tool_name]


def get_all_tool_schemas() -> List[Dict[str, Any]]:
    return list(TOOL_DEFINITIONS.values())


def get_openai_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": f"{t['provider']}.{t['name']}",
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in TOOL_DEFINITIONS.values()
    ]


EVIDENCE_RAG_TOOLS = TOOL_DEFINITIONS
