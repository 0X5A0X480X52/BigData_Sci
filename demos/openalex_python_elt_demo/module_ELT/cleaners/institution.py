# -*- coding: utf-8 -*-
"""
机构清洗器

从原始数据中提取机构实体。
"""

from typing import Any, Dict, List, Optional
import logging

from .base import BaseCleaner
from ..models.entities import InstitutionEntity

logger = logging.getLogger(__name__)


class InstitutionCleaner(BaseCleaner[InstitutionEntity]):
    """
    机构清洗器
    
    负责提取机构的基本字段：
    - iname (display_name)
    - itype (type)
    - country_code
    - openalex_id
    """
    
    def extract(self, raw_data: Dict[str, Any]) -> Optional[InstitutionEntity]:
        """
        从原始数据中提取机构实体
        
        Args:
            raw_data: 机构原始数据，可能来自：
                - OpenAlex Institution API 直接返回
                - Work 的 authorships[].institutions[] 嵌套结构
            
        Returns:
            InstitutionEntity 对象，数据无效时返回 None
        """
        # 机构名称是必需字段
        iname = self.safe_get(raw_data, "display_name")
        if not iname:
            self.logger.warning("机构名称缺失，跳过该记录")
            return None
        
        iname = self.clean_string(iname, max_length=255)
        
        # 机构类型
        itype = self.safe_get(raw_data, "type")
        if itype:
            itype = self.clean_string(itype, max_length=50)
        
        # 国家代码 (ISO-3166 二字码)
        country_code = self.safe_get(raw_data, "country_code")
        if country_code:
            country_code = str(country_code).upper()[:2]
        
        # OpenAlex ID
        openalex_id = self.extract_openalex_id(self.safe_get(raw_data, "id"))
        
        return InstitutionEntity(
            iname=iname,
            itype=itype,
            country_code=country_code,
            openalex_id=openalex_id,
        )
    
    def extract_from_authorship(
        self, authorship: Dict[str, Any]
    ) -> List[InstitutionEntity]:
        """
        从 authorship 结构中提取所有机构
        
        OpenAlex Work 返回的 authorships 结构中可能包含多个机构：
        {
            "author": {...},
            "institutions": [
                {"id": "...", "display_name": "...", "country_code": "US", "type": "education"},
                ...
            ]
        }
        
        Args:
            authorship: authorships[] 中的单个元素
            
        Returns:
            InstitutionEntity 列表
        """
        institutions = []
        
        inst_list = self.safe_get(authorship, "institutions") or []
        for inst_data in inst_list:
            entity = self.extract(inst_data)
            if entity:
                institutions.append(entity)
        
        return institutions
