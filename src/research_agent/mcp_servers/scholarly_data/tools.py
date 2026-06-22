"""Scholarly Data MCP — tool definitions and JSON Schemas."""

from __future__ import annotations

from typing import Any, Dict, List

# ── Tool schema definitions ──
# Each entry carries name, display_name, description, provider, parameter JSON
# Schema, return-type info, idempotency note, and possible error codes.

TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "create_field_corpus": {
        "name": "create_field_corpus",
        "display_name": "Create Field Corpus",
        "description": (
            "Search OpenAlex by query and build a deduplicated paper corpus. "
            "Supports boolean operators and phrase search.  Idempotent: same "
            "query+max_results will hit the database cache and skip API calls."
        ),
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "OpenAlex search query.  Supports boolean operators, phrase search, and field filters.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 100,
                    "minimum": 1,
                    "maximum": 3000,
                    "description": "Maximum number of papers to return.",
                },
                "alternate_queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional fallback OpenAlex search queries to try when the primary query returns no papers.",
                },
            },
            "required": ["query"],
        },
        "returns": {"type": "Corpus", "description": "Deduplicated corpus with paper-level metadata."},
        "idempotency": "Same query+max_results can be safely retried — hits DB cache.",
        "errors": [
            {"code": "OPENALEX_UNAVAILABLE", "description": "OpenAlex unreachable; degraded to fixture data."},
            {"code": "CORPUS_EMPTY", "description": "Search returned zero results."},
            {"code": "RATE_LIMITED", "description": "Rate-limited; auto-backoff applied."},
        ],
    },
    "create_seed_lineage_corpus": {
        "name": "create_seed_lineage_corpus",
        "display_name": "Create Seed Lineage Corpus",
        "description": (
            "Starting from a seed paper, BFS-expand through references and "
            "citing works up to max_depth.  Uses crawl_frontier for resume-on-interrupt."
        ),
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "seed_work_id": {
                    "type": "string",
                    "description": "OpenAlex Work ID, e.g. W123456789.",
                },
                "max_depth": {
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 3,
                    "description": "Maximum BFS depth.",
                },
                "max_results": {
                    "type": "integer",
                    "default": 500,
                    "minimum": 10,
                    "maximum": 2000,
                    "description": "Maximum total papers in the lineage corpus.",
                },
            },
            "required": ["seed_work_id"],
        },
        "returns": {"type": "Corpus", "description": "Lineage corpus built via BFS expansion."},
        "idempotency": "Same seed+depth can be retried; crawl_frontier enables resume.",
        "errors": [
            {"code": "SEED_NOT_FOUND", "description": "Seed work not found in OpenAlex."},
            {"code": "BFS_EXHAUSTED", "description": "BFS exhausted reachable papers before hitting max_results."},
        ],
    },
    "expand_references": {
        "name": "expand_references",
        "display_name": "Expand References",
        "description": "Fetch the works referenced by a given paper.",
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "work_id": {"type": "string", "description": "OpenAlex Work ID."},
                "max_results": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            },
            "required": ["work_id"],
        },
        "returns": {"type": "List[Paper]", "description": "List of referenced papers."},
    },
    "expand_citing_works": {
        "name": "expand_citing_works",
        "display_name": "Expand Citing Works",
        "description": "Fetch works that cite the given paper.",
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "work_id": {"type": "string", "description": "OpenAlex Work ID."},
                "max_results": {"type": "integer", "default": 50, "minimum": 1, "maximum": 200},
            },
            "required": ["work_id"],
        },
        "returns": {"type": "List[Paper]", "description": "List of citing papers."},
    },
    "get_corpus_summary": {
        "name": "get_corpus_summary",
        "display_name": "Get Corpus Summary",
        "description": "Return paper count and year histogram for a corpus.",
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus_id": {"type": "string", "description": "Corpus identifier."},
            },
            "required": ["corpus_id"],
        },
        "returns": {"type": "Dict", "description": "Summary with paper count and year histogram."},
    },
    "get_work": {
        "name": "get_work",
        "display_name": "Get Work",
        "description": "Fetch a single paper by OpenAlex Work ID.",
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "work_id": {"type": "string", "description": "OpenAlex Work ID."},
            },
            "required": ["work_id"],
        },
        "returns": {"type": "Paper", "description": "Single paper, or None if not found."},
    },
    "list_candidate_papers": {
        "name": "list_candidate_papers",
        "display_name": "List Candidate Papers",
        "description": "Return top papers from a corpus sorted by cited_by_count.",
        "provider": "scholarly-data",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus_id": {"type": "string", "description": "Corpus identifier."},
                "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["corpus_id"],
        },
        "returns": {"type": "List[Paper]", "description": "Top papers sorted by cited_by_count descending."},
    },
}


# ── Aggregated helpers ──

def get_tool_names() -> List[str]:
    """Return all tool names exposed by this MCP server."""
    return list(TOOL_DEFINITIONS.keys())


def get_tool_schema(tool_name: str) -> Dict[str, Any]:
    """Return the full definition dict for a single tool."""
    if tool_name not in TOOL_DEFINITIONS:
        raise ValueError(f"Unknown scholarly-data tool: {tool_name}")
    return TOOL_DEFINITIONS[tool_name]


def get_all_tool_schemas() -> List[Dict[str, Any]]:
    """Return full definitions for every tool in this server."""
    return list(TOOL_DEFINITIONS.values())


def get_openai_tool_schemas() -> List[Dict[str, Any]]:
    """Return tools in OpenAI function-calling format."""
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


SCHOLARLY_DATA_TOOLS = TOOL_DEFINITIONS

