# -*- coding: utf-8 -*-
"""
引用关系清洗器

从原始数据中提取论文引用关系。
OpenAlex 的 referenced_works 字段包含当前论文所引用的其他论文列表。
"""

from typing import Any, Dict, List, Optional, Tuple
import logging

from .base import BaseCleaner
from ..models.entities import CitationEntity

logger = logging.getLogger(__name__)


class CitationCleaner(BaseCleaner[CitationEntity]):
    """
    引用关系清洗器
    
    负责从 OpenAlex Work 数据中提取引用关系：
    - referenced_works: 当前论文引用的其他论文（参考文献）
    
    OpenAlex Work 数据结构：
    {
        "id": "https://openalex.org/W2741809807",
        "referenced_works": [
            "https://openalex.org/W2100837269",
            "https://openalex.org/W2044421704",
            ...
        ]
    }
    """
    
    def extract(self, raw_data: Dict[str, Any]) -> Optional[CitationEntity]:
        """
        本方法不直接使用，引用关系通过 extract_citations 批量提取。
        """
        return None
    
    def extract_citations(
        self,
        raw_data: Dict[str, Any]
    ) -> List[CitationEntity]:
        """
        从原始数据中提取引用关系列表
        
        Args:
            raw_data: OpenAlex Work 原始 JSON
            
        Returns:
            CitationEntity 列表（当前论文引用的所有论文）
        """
        citations = []
        
        # 获取当前论文的 OpenAlex ID
        work_id_url = self.safe_get(raw_data, "id")
        citing_work_id = self.extract_openalex_id(work_id_url)
        
        if not citing_work_id:
            self.logger.warning("无法提取当前论文的 OpenAlex ID")
            return citations
        
        # 获取引用列表
        referenced_works = self.safe_get(raw_data, "referenced_works") or []
        
        for ref_url in referenced_works:
            if not ref_url:
                continue
            
            # 提取被引论文的 OpenAlex ID
            cited_work_id = self.extract_openalex_id(ref_url)
            
            if cited_work_id:
                citation = CitationEntity(
                    citing_work_openalex_id=citing_work_id,
                    cited_work_openalex_id=cited_work_id,
                )
                citations.append(citation)
        
        return citations
    
    def extract_referenced_work_ids(
        self,
        raw_data: Dict[str, Any]
    ) -> Tuple[Optional[str], List[str]]:
        """
        仅提取引用的 OpenAlex ID 列表（轻量级）
        
        用于在不需要完整 CitationEntity 对象时快速获取引用关系。
        
        Args:
            raw_data: OpenAlex Work 原始 JSON
            
        Returns:
            Tuple[citing_work_id, List[cited_work_ids]]
        """
        # 获取当前论文的 OpenAlex ID
        work_id_url = self.safe_get(raw_data, "id")
        citing_work_id = self.extract_openalex_id(work_id_url)
        
        if not citing_work_id:
            return None, []
        
        # 获取引用列表
        referenced_works = self.safe_get(raw_data, "referenced_works") or []
        
        cited_work_ids = []
        for ref_url in referenced_works:
            if ref_url:
                cited_id = self.extract_openalex_id(ref_url)
                if cited_id:
                    cited_work_ids.append(cited_id)
        
        return citing_work_id, cited_work_ids
