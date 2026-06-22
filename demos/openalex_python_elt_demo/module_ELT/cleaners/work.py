# -*- coding: utf-8 -*-
"""
论文清洗器

从原始数据中提取论文实体的基本字段。
"""

from typing import Any, Dict, Optional
from datetime import datetime
import logging

from .base import BaseCleaner
from ..models.entities import WorkEntity

logger = logging.getLogger(__name__)


class WorkCleaner(BaseCleaner[WorkEntity]):
    """
    论文清洗器
    
    负责提取论文的基本字段：
    - title, doi, abstract
    - publication_date
    - work_type
    - openalex_id
    """
    
    def extract(self, raw_data: Dict[str, Any]) -> Optional[WorkEntity]:
        """
        从原始数据中提取论文实体
        
        Args:
            raw_data: OpenAlex Work 原始 JSON
            
        Returns:
            WorkEntity 对象，数据无效时返回 None
        """
        # 标题是必需字段
        title = self.safe_get(raw_data, "title") or self.safe_get(raw_data, "display_name")
        if not title:
            self.logger.warning("论文标题缺失，跳过该记录")
            return None
        
        title = self.clean_string(title)
        
        # DOI 清洗
        doi = self._clean_doi(self.safe_get(raw_data, "doi"))
        
        # 摘要处理
        abstract = self._extract_abstract(raw_data)
        
        # 发表日期
        publication_date = self._parse_date(self.safe_get(raw_data, "publication_date"))
        
        # 论文类型
        work_type = self.safe_get(raw_data, "type")
        
        # OpenAlex ID
        openalex_id = self.extract_openalex_id(self.safe_get(raw_data, "id"))
        openalex_url = self.safe_get(raw_data, "id")
        
        # 引用数
        cited_by_count = self.safe_get(raw_data, "cited_by_count")
        
        return WorkEntity(
            title=title,
            doi=doi,
            abstract=abstract,
            publication_date=publication_date,
            work_type=work_type,
            openalex_id=openalex_id,
            openalex_url=openalex_url,
            cited_by_count=cited_by_count,
        )
    
    def _clean_doi(self, doi: Optional[str]) -> Optional[str]:
        """
        清洗 DOI
        
        Args:
            doi: 原始 DOI，可能是 URL 格式
            
        Returns:
            标准化的 DOI（不含 https://doi.org/ 前缀）
        """
        if not doi:
            return None
        
        doi = str(doi).strip()
        
        # 移除 URL 前缀
        prefixes = ["https://doi.org/", "http://doi.org/", "doi.org/", "doi:"]
        for prefix in prefixes:
            if doi.lower().startswith(prefix.lower()):
                doi = doi[len(prefix):]
                break
        
        return doi if doi else None
    
    def _extract_abstract(self, raw_data: Dict[str, Any]) -> Optional[str]:
        """
        提取摘要
        
        OpenAlex 返回的摘要可能是：
        1. 直接的 abstract 字段
        2. abstract_inverted_index（倒排索引格式）
        
        Args:
            raw_data: 原始数据
            
        Returns:
            摘要文本
        """
        # 尝试直接获取 abstract
        abstract = self.safe_get(raw_data, "abstract")
        if abstract:
            return self.clean_string(abstract)
        
        # 尝试从 abstract_inverted_index 还原
        inverted_index = self.safe_get(raw_data, "abstract_inverted_index")
        if inverted_index:
            return self._invert_abstract(inverted_index)
        
        return None
    
    def _invert_abstract(self, inverted_index: Dict[str, list]) -> Optional[str]:
        """
        从倒排索引还原摘要文本
        
        Args:
            inverted_index: OpenAlex 的 abstract_inverted_index
                格式: {"word1": [0, 5], "word2": [1, 3], ...}
            
        Returns:
            还原的摘要文本
        """
        if not inverted_index:
            return None
        
        try:
            # 构建 (position, word) 列表
            position_word_pairs = []
            for word, positions in inverted_index.items():
                for pos in positions:
                    position_word_pairs.append((pos, word))
            
            # 按位置排序
            position_word_pairs.sort(key=lambda x: x[0])
            
            # 拼接为文本
            words = [word for _, word in position_word_pairs]
            return " ".join(words)
            
        except Exception as e:
            self.logger.warning(f"摘要倒排索引还原失败: {e}")
            return None
    
    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime]:
        """
        解析日期字符串
        
        Args:
            date_str: 日期字符串，格式可能是 YYYY-MM-DD 或 YYYY
            
        Returns:
            datetime 对象
        """
        if not date_str:
            return None
        
        date_str = str(date_str).strip()
        
        # 尝试不同的日期格式
        formats = [
            "%Y-%m-%d",
            "%Y-%m",
            "%Y",
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        
        self.logger.warning(f"无法解析日期: {date_str}")
        return None
