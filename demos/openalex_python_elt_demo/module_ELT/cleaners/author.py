# -*- coding: utf-8 -*-
"""
作者清洗器

从原始数据中提取作者实体。
"""

from typing import Any, Dict, Optional
import logging

from .base import BaseCleaner
from ..models.entities import AuthorEntity

logger = logging.getLogger(__name__)


class AuthorCleaner(BaseCleaner[AuthorEntity]):
    """
    作者清洗器
    
    负责提取作者的基本字段：
    - aname (display_name)
    - orcid
    - openalex_id
    """
    
    def extract(self, raw_data: Dict[str, Any]) -> Optional[AuthorEntity]:
        """
        从原始数据中提取作者实体
        
        Args:
            raw_data: 作者原始数据，可能来自：
                - OpenAlex Author API 直接返回
                - Work 的 authorships[].author 嵌套结构
            
        Returns:
            AuthorEntity 对象，数据无效时返回 None
        """
        # 作者姓名是必需字段
        aname = self.safe_get(raw_data, "display_name")
        if not aname:
            self.logger.warning("作者姓名缺失，跳过该记录")
            return None
        
        aname = self.clean_string(aname, max_length=200)
        
        # ORCID 清洗
        orcid = self.clean_orcid(self.safe_get(raw_data, "orcid"))
        
        # OpenAlex ID
        openalex_id = self.extract_openalex_id(self.safe_get(raw_data, "id"))
        
        return AuthorEntity(
            aname=aname,
            orcid=orcid,
            openalex_id=openalex_id,
        )
    
    def extract_from_authorship(self, authorship: Dict[str, Any]) -> Optional[AuthorEntity]:
        """
        从 authorship 结构中提取作者
        
        OpenAlex Work 返回的 authorships 结构：
        {
            "author": {"id": "...", "display_name": "...", "orcid": "..."},
            "author_position": "first",
            "is_corresponding": false,
            "institutions": [...]
        }
        
        Args:
            authorship: authorships[] 中的单个元素
            
        Returns:
            AuthorEntity 对象
        """
        author_data = self.safe_get(authorship, "author")
        if not author_data:
            return None
        
        return self.extract(author_data)
