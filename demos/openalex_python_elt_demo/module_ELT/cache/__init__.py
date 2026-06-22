# -*- coding: utf-8 -*-
"""
缓存管理模块

提供统一的缓存接口，支持文件缓存和 MongoDB 缓存。
"""

from typing import Literal
from .base import CacheManager
from .file_cache import FileCacheManager

__all__ = ["CacheManager", "FileCacheManager", "get_cache_manager"]


def get_cache_manager(
    backend: Literal["file", "mongo"] = "file",
    **kwargs
) -> CacheManager:
    """
    缓存管理器工厂函数
    
    Args:
        backend: 缓存后端类型，"file" 或 "mongo"
        **kwargs: 传递给具体缓存管理器的参数
        
    Returns:
        CacheManager: 缓存管理器实例
    """
    if backend == "file":
        return FileCacheManager(**kwargs)
    elif backend == "mongo":
        # TODO: 实现 MongoDB 缓存
        from .mongo_cache import MongoCacheManager
        return MongoCacheManager(**kwargs)
    else:
        raise ValueError(f"不支持的缓存后端: {backend}")
