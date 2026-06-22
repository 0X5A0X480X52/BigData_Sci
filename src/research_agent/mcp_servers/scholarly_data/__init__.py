"""Scholarly Data MCP Server.

Tools: create_field_corpus, create_seed_lineage_corpus, expand_references,
       expand_citing_works, get_corpus_summary, get_work, list_candidate_papers.
"""

from .server import ScholarlyDataMCPServer
from .tools import SCHOLARLY_DATA_TOOLS, get_all_tool_schemas

__all__ = ["ScholarlyDataMCPServer", "SCHOLARLY_DATA_TOOLS", "get_all_tool_schemas"]
