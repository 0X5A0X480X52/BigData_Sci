"""Skill registry for the research agent MVP."""

from .analyze_key_papers import analyze_key_papers
from .build_research_corpus import build_research_corpus
from .discover_research_perspectives import discover_research_perspectives
from .generate_field_guide import generate_field_guide
from .identify_key_papers import identify_key_papers
from .map_field_structure import map_field_structure
from .scope_new_field import scope_new_field
from .write_llm_report import write_llm_report

SKILLS = {
    "scope_new_field": scope_new_field,
    "discover_research_perspectives": discover_research_perspectives,
    "build_research_corpus": build_research_corpus,
    "map_field_structure": map_field_structure,
    "identify_key_papers": identify_key_papers,
    "analyze_key_papers": analyze_key_papers,
    "generate_field_guide": generate_field_guide,
    "write_llm_report": write_llm_report,
}

__all__ = ["SKILLS"]
