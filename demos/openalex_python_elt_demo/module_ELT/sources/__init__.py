# -*- coding: utf-8 -*-
"""
数据源模块

提供统一的数据源接口，支持多种数据源的扩展。
"""

from .base import BaseSource
from .openalex import OpenAlexSource

__all__ = ["BaseSource", "OpenAlexSource"]
