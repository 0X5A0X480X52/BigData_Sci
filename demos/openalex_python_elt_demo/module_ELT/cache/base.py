# -*- coding: utf-8 -*-
"""
缓存管理器基类

定义缓存管理器的统一接口，所有缓存后端都需要实现这些方法。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class CacheManager(ABC):
    """
    缓存管理器抽象基类
    
    提供统一的缓存接口，支持：
    - 保存/加载单个对象
    - 批量操作
    - 键值存在检查
    - 键列表查询
    """
    
    @abstractmethod
    def save(self, key: str, data: Any) -> bool:
        """
        保存数据到缓存
        
        Args:
            key: 缓存键，建议格式: "{entity_type}/{id}"
            data: 要缓存的数据（通常是 dict）
            
        Returns:
            bool: 保存是否成功
        """
        pass
    
    @abstractmethod
    def load(self, key: str) -> Optional[Any]:
        """
        从缓存加载数据
        
        Args:
            key: 缓存键
            
        Returns:
            缓存的数据，如果不存在返回 None
        """
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """
        检查缓存键是否存在
        
        Args:
            key: 缓存键
            
        Returns:
            bool: 是否存在
        """
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """
        删除缓存
        
        Args:
            key: 缓存键
            
        Returns:
            bool: 删除是否成功
        """
        pass
    
    @abstractmethod
    def list_keys(self, prefix: Optional[str] = None) -> List[str]:
        """
        列出所有缓存键
        
        Args:
            prefix: 可选的键前缀过滤
            
        Returns:
            List[str]: 缓存键列表
        """
        pass
    
    def save_batch(self, items: Dict[str, Any]) -> int:
        """
        批量保存数据
        
        Args:
            items: {key: data} 字典
            
        Returns:
            int: 成功保存的数量
        """
        success_count = 0
        for key, data in items.items():
            if self.save(key, data):
                success_count += 1
        return success_count
    
    def load_batch(self, keys: List[str]) -> Dict[str, Any]:
        """
        批量加载数据
        
        Args:
            keys: 缓存键列表
            
        Returns:
            Dict[str, Any]: {key: data} 字典，不存在的键不包含在结果中
        """
        result = {}
        for key in keys:
            data = self.load(key)
            if data is not None:
                result[key] = data
        return result
    
    def count(self, prefix: Optional[str] = None) -> int:
        """
        统计缓存数量
        
        Args:
            prefix: 可选的键前缀过滤
            
        Returns:
            int: 缓存数量
        """
        return len(self.list_keys(prefix))
