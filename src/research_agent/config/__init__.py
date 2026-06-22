"""Configuration package for the research agent.

Provides YAML-based configuration with environment-variable override.
"""

from .load import ConfigLoader, load_config

__all__ = ["ConfigLoader", "load_config"]
