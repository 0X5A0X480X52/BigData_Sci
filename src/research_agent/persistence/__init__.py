"""Persistence layer for the research agent.

Default backend: MySQL (via ``MySQLResearchRepository``).
The ``ResearchRepository`` ABC allows swap-out to other backends.
"""

from .repository import ResearchRepository
from .mysql_repository import MySQLResearchRepository

__all__ = ["ResearchRepository", "MySQLResearchRepository"]
