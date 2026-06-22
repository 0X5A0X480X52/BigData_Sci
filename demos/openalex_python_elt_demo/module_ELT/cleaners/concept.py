# -*- coding: utf-8 -*-
"""
概念/关键词清洗器

从原始数据中提取概念实体。
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

from .base import BaseCleaner
from ..models.entities import ConceptEntity

logger = logging.getLogger(__name__)


class ConceptCleaner(BaseCleaner[ConceptEntity]):
    """
    概念清洗器
    
    负责提取概念的基本字段：
    - cname (display_name)
    - level (概念层级，OpenAlex 特有)
    - openalex_id
    """
    
    def extract(self, raw_data: Dict[str, Any]) -> Optional[ConceptEntity]:
        """
        从原始数据中提取概念实体
        
        Args:
            raw_data: 概念原始数据，可能来自：
                - OpenAlex Concept API 直接返回
                - Work 的 concepts[] 嵌套结构
            
        Returns:
            ConceptEntity 对象，数据无效时返回 None
        """
        # 概念名称是必需字段
        cname = self.safe_get(raw_data, "display_name")
        if not cname:
            self.logger.warning("概念名称缺失，跳过该记录")
            return None
        
        cname = self.clean_string(cname, max_length=255)
        
        # 概念层级
        level = self.safe_get(raw_data, "level")
        if level is not None:
            try:
                level = int(level)
            except (ValueError, TypeError):
                level = None
        
        # OpenAlex ID
        openalex_id = self.extract_openalex_id(self.safe_get(raw_data, "id"))
        
        return ConceptEntity(
            cname=cname,
            level=level,
            openalex_id=openalex_id,
        )
    
    def extract_with_score(
        self, raw_data: Dict[str, Any]
    ) -> Optional[Tuple[ConceptEntity, float]]:
        """
        从原始数据中提取概念及其权重
        
        OpenAlex Work 的 concepts[] 结构包含 score 字段：
        {
            "id": "...",
            "display_name": "Machine learning",
            "level": 2,
            "score": 0.85
        }
        
        Args:
            raw_data: 概念原始数据
            
        Returns:
            (ConceptEntity, score) 元组，数据无效时返回 None
        """
        entity = self.extract(raw_data)
        if not entity:
            return None
        
        score = self.safe_get(raw_data, "score")
        if score is not None:
            try:
                score = float(score)
            except (ValueError, TypeError):
                score = None
        
        return (entity, score)
    
    def extract_from_work(
        self, work_data: Dict[str, Any]
    ) -> List[Tuple[ConceptEntity, Optional[float]]]:
        """
        从 Work 数据中提取所有概念及其权重
        
        Args:
            work_data: OpenAlex Work 原始数据
            
        Returns:
            [(ConceptEntity, score), ...] 列表
        """
        results = []
        
        concepts_list = self.safe_get(work_data, "concepts") or []
        for concept_data in concepts_list:
            result = self.extract_with_score(concept_data)
            if result:
                results.append(result)
        
        return results
