"""Skill: map graph, trends and topics."""

from __future__ import annotations


def map_field_structure(state, mcp, task):
    corpus = state["field_corpus"]
    structure = mcp.call("graph-analytics", "map_field_structure", corpus=corpus)
    state["field_structure"] = structure
    return structure
