"""Graph Analytics MCP Server.

Tools: build_graph_snapshot, run_pagerank, detect_communities, rank_key_papers,
       find_bridge_papers, compute_topic_statistics, compute_yearly_trend,
       map_field_structure.
"""

from .server import GraphAnalyticsMCPServer
from .tools import GRAPH_ANALYTICS_TOOLS, get_all_tool_schemas

__all__ = ["GraphAnalyticsMCPServer", "GRAPH_ANALYTICS_TOOLS", "get_all_tool_schemas"]
