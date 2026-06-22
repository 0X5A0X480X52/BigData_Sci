"""Skill: identify key papers."""

from __future__ import annotations


def identify_key_papers(state, mcp, task):
    structure = state["field_structure"]
    key_papers = structure["key_papers"]
    state["key_papers"] = key_papers
    return key_papers
