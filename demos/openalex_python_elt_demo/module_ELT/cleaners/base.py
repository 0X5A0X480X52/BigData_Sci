# -*- coding: utf-8 -*-
"""
清洗器基类

定义清洗器的统一接口，每个 Cleaner 负责单一实体类型的字段提取与标准化。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TypeVar, Generic
import logging

logger = logging.getLogger(__name__)

# 泛型类型，表示输出的实体类型
T = TypeVar('T')


class BaseCleaner(ABC, Generic[T]):
    """
    清洗器抽象基类
    
    职责：
    - 从原始数据中提取单一实体的字段
    - 字段标准化（类型转换、格式统一）
    - 不处理实体间的关联关系（由 Pipeline 负责）
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @abstractmethod
    def extract(self, raw_data: Dict[str, Any]) -> Optional[T]:
        """
        从原始数据中提取实体
        
        Args:
            raw_data: 原始 JSON 数据（字典）
            
        Returns:
            提取的实体对象，如果数据无效返回 None
        """
        pass
    
    def safe_get(
        self,
        data: Dict[str, Any],
        *keys: str,
        default: Any = None
    ) -> Any:
        """
        安全地获取嵌套字典中的值
        
        Args:
            data: 字典数据
            *keys: 键路径，如 safe_get(d, "a", "b", "c") 等价于 d["a"]["b"]["c"]
            default: 默认值
            
        Returns:
            获取的值，如果路径不存在返回默认值
        """
        result = data
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            else:
                return default
        return result if result is not None else default
    
    def clean_string(self, value: Any, max_length: Optional[int] = None) -> Optional[str]:
        """
        清洗字符串值
        
        Args:
            value: 原始值
            max_length: 最大长度限制
            
        Returns:
            清洗后的字符串，无效值返回 None
        """
        if value is None:
            return None
        
        # 转为字符串并去除首尾空白
        s = str(value).strip()
        
        if not s:
            return None
        
        # 截断过长的字符串
        if max_length and len(s) > max_length:
            s = s[:max_length]
            self.logger.warning(f"字符串被截断至 {max_length} 字符")
        
        return s
    
    def extract_openalex_id(self, url_or_id: Optional[str]) -> Optional[str]:
        """
        从 OpenAlex URL 中提取 ID
        
        Args:
            url_or_id: OpenAlex URL 或 ID
                例如: "https://openalex.org/W2741809807" -> "W2741809807"
            
        Returns:
            OpenAlex ID，无效值返回 None
        """
        if not url_or_id:
            return None
        
        url_or_id = str(url_or_id).strip()
        
        # 如果是完整 URL，提取最后一部分
        if "/" in url_or_id:
            return url_or_id.split("/")[-1]
        
        return url_or_id
    
    def clean_orcid(self, orcid: Optional[str]) -> Optional[str]:
        """
        清洗 ORCID
        
        Args:
            orcid: 原始 ORCID，可能是 URL 格式
                例如: "https://orcid.org/0000-0001-7318-9658"
            
        Returns:
            标准化的 ORCID（仅数字部分），无效值返回 None
        """
        if not orcid:
            return None
        
        orcid = str(orcid).strip()
        
        # 从 URL 中提取 ORCID
        if "orcid.org/" in orcid:
            orcid = orcid.split("orcid.org/")[-1]
        
        # 验证格式: xxxx-xxxx-xxxx-xxxx
        orcid = orcid.strip("/")
        if len(orcid) == 19 and orcid.count("-") == 3:
            return orcid
        
        return None
