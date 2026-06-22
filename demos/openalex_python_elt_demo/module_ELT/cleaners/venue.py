# -*- coding: utf-8 -*-
"""
期刊/会议清洗器

从原始数据中提取 Venue (Source) 实体。
"""

from typing import Any, Dict, List, Optional
import logging

from .base import BaseCleaner
from ..models.entities import VenueEntity

logger = logging.getLogger(__name__)


class VenueCleaner(BaseCleaner[VenueEntity]):
    """
    期刊/会议清洗器
    
    负责提取 Venue 的基本字段：
    - vname (display_name)
    - issn, issn_print, issn_online
    - homepage
    - publisher
    - is_open_access
    - openalex_id
    """
    
    def extract(self, raw_data: Dict[str, Any]) -> Optional[VenueEntity]:
        """
        从原始数据中提取期刊/会议实体
        
        Args:
            raw_data: Venue/Source 原始数据，可能来自：
                - OpenAlex Source API 直接返回
                - Work 的 primary_location.source 嵌套结构
                - Work 的 locations[].source 嵌套结构
            
        Returns:
            VenueEntity 对象，数据无效时返回 None
        """
        # 名称是必需字段
        vname = self.safe_get(raw_data, "display_name")
        if not vname:
            self.logger.warning("期刊/会议名称缺失，跳过该记录")
            return None
        
        vname = self.clean_string(vname, max_length=255)
        
        # ISSN 处理
        issn, issn_print, issn_online = self._extract_issns(raw_data)
        
        # 其他字段
        homepage = self.safe_get(raw_data, "homepage_url")
        if homepage:
            homepage = self.clean_string(homepage, max_length=255)
        
        publisher = self.safe_get(raw_data, "host_organization_name")
        if publisher:
            publisher = self.clean_string(publisher, max_length=255)
        
        # 开放获取
        is_open_access = self.safe_get(raw_data, "is_oa", default=False)
        
        # 国家代码
        country_code = self.safe_get(raw_data, "country_code")
        if country_code:
            country_code = str(country_code).upper()[:2]
        
        # OpenAlex ID
        openalex_id = self.extract_openalex_id(self.safe_get(raw_data, "id"))
        
        return VenueEntity(
            vname=vname,
            issn=issn,
            issn_print=issn_print,
            issn_online=issn_online,
            homepage=homepage,
            publisher=publisher,
            is_open_access=bool(is_open_access),
            country_code=country_code,
            openalex_id=openalex_id,
        )
    
    def _extract_issns(
        self, raw_data: Dict[str, Any]
    ) -> tuple:
        """
        提取 ISSN 信息
        
        OpenAlex 的 ISSN 可能有多种格式：
        - issn_l: linking ISSN
        - issn: ISSN 列表
        
        Args:
            raw_data: 原始数据
            
        Returns:
            (issn, issn_print, issn_online) 元组
        """
        issn = None
        issn_print = None
        issn_online = None
        
        # 优先使用 issn_l (linking ISSN)
        issn_l = self.safe_get(raw_data, "issn_l")
        if issn_l:
            issn = self._clean_issn(issn_l)
        
        # 从 issn 列表中获取
        issn_list = self.safe_get(raw_data, "issn") or []
        if isinstance(issn_list, list) and len(issn_list) > 0:
            # 第一个通常是 print ISSN
            if len(issn_list) >= 1:
                issn_print = self._clean_issn(issn_list[0])
            # 第二个通常是 online ISSN
            if len(issn_list) >= 2:
                issn_online = self._clean_issn(issn_list[1])
            
            # 如果没有 issn_l，使用第一个作为主 ISSN
            if not issn and issn_print:
                issn = issn_print
        
        return issn, issn_print, issn_online
    
    def _clean_issn(self, issn: Optional[str]) -> Optional[str]:
        """
        清洗 ISSN
        
        Args:
            issn: 原始 ISSN
            
        Returns:
            标准化的 ISSN (格式: XXXX-XXXX)
        """
        if not issn:
            return None
        
        issn = str(issn).strip().upper()
        
        # 移除非字母数字字符（除了连字符）
        cleaned = "".join(c for c in issn if c.isalnum())
        
        # 格式化为 XXXX-XXXX
        if len(cleaned) == 8:
            return f"{cleaned[:4]}-{cleaned[4:]}"
        
        return issn[:20] if len(issn) <= 20 else issn[:20]
    
    def extract_from_location(
        self, location: Dict[str, Any]
    ) -> Optional[VenueEntity]:
        """
        从 location 结构中提取期刊/会议
        
        OpenAlex Work 的 primary_location 或 locations[] 结构：
        {
            "source": {"id": "...", "display_name": "...", ...},
            "is_oa": true,
            "pdf_url": "..."
        }
        
        Args:
            location: location 数据
            
        Returns:
            VenueEntity 对象
        """
        source = self.safe_get(location, "source")
        if not source:
            return None
        
        return self.extract(source)
