# -*- coding: utf-8 -*-
"""
清洗流水线模块

Pipeline 负责适配不同数据源的 JSON 结构，
组合 Cleaner 完成完整的清洗流程。
"""

from .base import BasePipeline, CleanedResult
from .openalex import OpenAlexPipeline

__all__ = ["BasePipeline", "CleanedResult", "OpenAlexPipeline"]
