# -*- coding: utf-8 -*-
"""
清洗器模块

提供实体级别的数据提取与标准化功能。
每个 Cleaner 负责单一实体类型的字段映射。
"""

from .base import BaseCleaner
from .work import WorkCleaner
from .author import AuthorCleaner
from .institution import InstitutionCleaner
from .concept import ConceptCleaner
from .venue import VenueCleaner
from .citation import CitationCleaner

__all__ = [
    "BaseCleaner",
    "WorkCleaner",
    "AuthorCleaner",
    "InstitutionCleaner",
    "ConceptCleaner",
    "VenueCleaner",
    "CitationCleaner",
]
