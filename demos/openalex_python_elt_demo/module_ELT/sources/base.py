# -*- coding: utf-8 -*-
"""
数据源基类

定义数据源的统一接口，所有数据源都需要实现这些方法。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional
import logging

logger = logging.getLogger(__name__)


class BaseSource(ABC):
    """
    数据源抽象基类
    
    职责：
    - 定义数据获取的统一接口
    - 管理 API 连接和认证
    - 支持分页获取
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称"""
        pass
    
    @abstractmethod
    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个论文
        
        Args:
            work_id: 论文 ID
            
        Returns:
            论文原始 JSON 数据
        """
        pass
    
    @abstractmethod
    def search_works(
        self,
        query: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        per_page: int = 25,
        max_results: Optional[int] = None,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        搜索论文（生成器）
        
        Args:
            query: 搜索关键词
            filters: 过滤条件
            per_page: 每页数量
            max_results: 最大结果数
            
        Yields:
            论文原始 JSON 数据
        """
        pass
    
    @abstractmethod
    def get_author(self, author_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个作者
        
        Args:
            author_id: 作者 ID
            
        Returns:
            作者原始 JSON 数据
        """
        pass
    
    @abstractmethod
    def get_institution(self, institution_id: str) -> Optional[Dict[str, Any]]:
        """
        获取单个机构
        
        Args:
            institution_id: 机构 ID
            
        Returns:
            机构原始 JSON 数据
        """
        pass
