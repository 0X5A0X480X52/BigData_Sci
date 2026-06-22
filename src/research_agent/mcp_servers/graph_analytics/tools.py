"""Graph Analytics MCP — tool definitions and JSON Schemas."""

from __future__ import annotations

from typing import Any, Dict, List

TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "build_graph_snapshot": {
        "name": "build_graph_snapshot",
        "display_name": "Build Graph Snapshot",
        "description": (
            "Build a multi-entity citation graph snapshot from a corpus. "
            "Nodes: Paper, Author, Topic, Institution, Venue. "
            "Edges: CITES, AUTHORED_BY, HAS_TOPIC, AFFILIATED_WITH, PUBLISHED_IN."
        ),
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus": {
                    "type": "object",
                    "description": "Corpus object containing papers to build the graph from.",
                },
                "parameters": {
                    "type": "object",
                    "description": "Optional dict with max_nodes, max_edges overrides.",
                },
            },
            "required": ["corpus"],
        },
        "returns": {"type": "GraphSnapshot", "description": "Graph snapshot with nodes, edges, and metadata."},
    },
    "run_pagerank": {
        "name": "run_pagerank",
        "display_name": "Run PageRank",
        "description": "Run PageRank on a graph snapshot and return node scores.",
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot": {"type": "object", "description": "GraphSnapshot to run PageRank on."},
                "iterations": {"type": "integer", "default": 30, "minimum": 1, "maximum": 200},
                "damping": {"type": "number", "default": 0.85, "minimum": 0.5, "maximum": 0.99},
            },
            "required": ["snapshot"],
        },
        "returns": {"type": "Dict[str, float]", "description": "node_id → PageRank score mapping."},
    },
    "detect_communities": {
        "name": "detect_communities",
        "display_name": "Detect Communities",
        "description": "Detect communities using Louvain (with fallback to BFS connected components).",
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot": {"type": "object", "description": "GraphSnapshot to partition."},
            },
            "required": ["snapshot"],
        },
        "returns": {"type": "Dict[str, int]", "description": "node_id → community_id mapping."},
    },
    "rank_key_papers": {
        "name": "rank_key_papers",
        "display_name": "Rank Key Papers",
        "description": (
            "Composite ranking: 45% PageRank + 30% citation count + "
            "15% community representative + 10% bridge score.  Assigns roles."
        ),
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus": {"type": "object", "description": "Corpus containing papers to rank."},
                "snapshot": {"type": "object", "description": "GraphSnapshot for PageRank and community context."},
                "limit": {"type": "integer", "default": 15, "minimum": 1, "maximum": 100},
            },
            "required": ["corpus", "snapshot"],
        },
        "returns": {"type": "List[Dict]", "description": "Ranked papers with scores and roles."},
    },
    "find_bridge_papers": {
        "name": "find_bridge_papers",
        "display_name": "Find Bridge Papers",
        "description": "Find papers whose edges cross community boundaries.",
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "snapshot": {"type": "object", "description": "GraphSnapshot."},
                "communities": {"type": "object", "description": "node_id → community_id mapping."},
            },
            "required": ["snapshot", "communities"],
        },
        "returns": {"type": "List[Dict]", "description": "Bridge papers with cross-community edge counts."},
    },
    "compute_topic_statistics": {
        "name": "compute_topic_statistics",
        "display_name": "Compute Topic Statistics",
        "description": "Compute topic frequency ranking from a corpus.",
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus": {"type": "object", "description": "Corpus."},
                "top_k": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
            },
            "required": ["corpus"],
        },
        "returns": {"type": "List[Dict]", "description": "Topics with counts."},
    },
    "compute_yearly_trend": {
        "name": "compute_yearly_trend",
        "display_name": "Compute Yearly Trend",
        "description": "Compute year-by-year paper count histogram from a corpus.",
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus": {"type": "object", "description": "Corpus."},
            },
            "required": ["corpus"],
        },
        "returns": {"type": "Dict[int, int]", "description": "year → paper count."},
    },
    "map_field_structure": {
        "name": "map_field_structure",
        "display_name": "Map Field Structure",
        "description": "Convenience: build snapshot + run PageRank + detect communities + rank key papers.",
        "provider": "graph-analytics",
        "parameters": {
            "type": "object",
            "properties": {
                "corpus": {"type": "object", "description": "Corpus."},
            },
            "required": ["corpus"],
        },
        "returns": {"type": "Dict", "description": "Aggregated field structure with snapshot, communities, key papers, trends."},
    },
}


def get_tool_names() -> List[str]:
    return list(TOOL_DEFINITIONS.keys())


def get_tool_schema(tool_name: str) -> Dict[str, Any]:
    if tool_name not in TOOL_DEFINITIONS:
        raise ValueError(f"Unknown graph-analytics tool: {tool_name}")
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


GRAPH_ANALYTICS_TOOLS = TOOL_DEFINITIONS
