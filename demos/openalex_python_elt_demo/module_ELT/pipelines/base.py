# -*- coding: utf-8 -*-
"""
清洗流水线基类

Pipeline 负责适配不同数据源的 JSON 结构，组合 Cleaner 完成完整的清洗流程。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import logging

from ..models.entities import (
    WorkEntity,
    AuthorEntity,
    InstitutionEntity,
    ConceptEntity,
    VenueEntity,
    CountryEntity,
    WorkTypeEntity,
    WorkAuthorInstitution,
    WorkConcept,
    WorkVenue,
    CitationEntity,
)

logger = logging.getLogger(__name__)


@dataclass
class CleanedResult:
    """
    清洗结果数据结构
    
    包含一条完整记录清洗后的所有实体和关联关系。
    """
    # 主实体
    work: Optional[WorkEntity] = None
    
    # 关联实体（去重后）
    authors: List[AuthorEntity] = field(default_factory=list)
    institutions: List[InstitutionEntity] = field(default_factory=list)
    concepts: List[ConceptEntity] = field(default_factory=list)
    venues: List[VenueEntity] = field(default_factory=list)
    countries: List[CountryEntity] = field(default_factory=list)
    work_types: List[WorkTypeEntity] = field(default_factory=list)
    
    # 关联关系
    work_author_institutions: List[WorkAuthorInstitution] = field(default_factory=list)
    work_concepts: List[WorkConcept] = field(default_factory=list)
    work_venues: List[WorkVenue] = field(default_factory=list)
    citations: List[CitationEntity] = field(default_factory=list)  # 引用关系
    
    # 原始数据（可选，用于调试）
    raw_data: Optional[Dict[str, Any]] = None
    
    # 处理状态
    success: bool = True
    errors: List[str] = field(default_factory=list)
    
    def is_valid(self) -> bool:
        """检查清洗结果是否有效（至少有 work）"""
        return self.work is not None and self.success
    
    def add_error(self, error: str):
        """添加错误信息"""
        self.errors.append(error)
        self.success = False
    
    def get_all_country_codes(self) -> set:
        """获取所有涉及的国家代码"""
        codes = set()
        for inst in self.institutions:
            if inst.country_code:
                codes.add(inst.country_code)
        for venue in self.venues:
            if venue.country_code:
                codes.add(venue.country_code)
        return codes
    
    def summary(self) -> Dict[str, int]:
        """获取清洗结果摘要"""
        return {
            "work": 1 if self.work else 0,
            "authors": len(self.authors),
            "institutions": len(self.institutions),
            "concepts": len(self.concepts),
            "venues": len(self.venues),
            "work_author_institutions": len(self.work_author_institutions),
            "work_concepts": len(self.work_concepts),
            "work_venues": len(self.work_venues),
            "citations": len(self.citations),
            "errors": len(self.errors),
        }


@dataclass
class BatchCleanedResult:
    """
    批量清洗结果
    
    用于批量处理多条记录的结果汇总。
    """
    results: List[CleanedResult] = field(default_factory=list)
    
    # 汇总的去重实体
    all_authors: Dict[str, AuthorEntity] = field(default_factory=dict)  # key: openalex_id
    all_institutions: Dict[str, InstitutionEntity] = field(default_factory=dict)
    all_concepts: Dict[str, ConceptEntity] = field(default_factory=dict)
    all_venues: Dict[str, VenueEntity] = field(default_factory=dict)
    all_countries: Dict[str, CountryEntity] = field(default_factory=dict)  # key: country_code
    all_work_types: Dict[str, WorkTypeEntity] = field(default_factory=dict)  # key: type_name
    
    # 引用关系汇总（所有引用关系，包括可能指向未获取论文的引用）
    all_citations: List[CitationEntity] = field(default_factory=list)
    # 被引用的外部论文 ID（在当前批次中不存在的论文）
    external_cited_works: set = field(default_factory=set)
    
    def add_result(self, result: CleanedResult):
        """添加单条清洗结果并合并实体"""
        self.results.append(result)
        
        # 合并作者
        for author in result.authors:
            if author.openalex_id and author.openalex_id not in self.all_authors:
                self.all_authors[author.openalex_id] = author
        
        # 合并机构
        for inst in result.institutions:
            if inst.openalex_id and inst.openalex_id not in self.all_institutions:
                self.all_institutions[inst.openalex_id] = inst
        
        # 合并概念
        for concept in result.concepts:
            if concept.openalex_id and concept.openalex_id not in self.all_concepts:
                self.all_concepts[concept.openalex_id] = concept
        
        # 合并期刊
        for venue in result.venues:
            if venue.openalex_id and venue.openalex_id not in self.all_venues:
                self.all_venues[venue.openalex_id] = venue
        
        # 合并国家
        for code in result.get_all_country_codes():
            if code not in self.all_countries:
                self.all_countries[code] = CountryEntity(country_code=code)
        
        # 合并论文类型
        if result.work and result.work.work_type:
            type_name = result.work.work_type
            if type_name not in self.all_work_types:
                self.all_work_types[type_name] = WorkTypeEntity(work_type_name=type_name)
        
        # 合并引用关系
        for citation in result.citations:
            self.all_citations.append(citation)
            # 记录被引用的论文 ID
            self.external_cited_works.add(citation.cited_work_openalex_id)
    
    @property
    def success_count(self) -> int:
        """成功处理的记录数"""
        return sum(1 for r in self.results if r.is_valid())
    
    @property
    def error_count(self) -> int:
        """处理失败的记录数"""
        return sum(1 for r in self.results if not r.is_valid())
    
    def summary(self) -> Dict[str, Any]:
        """获取批量处理摘要"""
        return {
            "total": len(self.results),
            "success": self.success_count,
            "errors": self.error_count,
            "unique_authors": len(self.all_authors),
            "unique_institutions": len(self.all_institutions),
            "unique_concepts": len(self.all_concepts),
            "unique_venues": len(self.all_venues),
            "unique_countries": len(self.all_countries),
            "unique_work_types": len(self.all_work_types),
            "total_citations": len(self.all_citations),
            "external_cited_works": len(self.external_cited_works),
        }


class BasePipeline(ABC):
    """
    清洗流水线抽象基类
    
    职责：
    - 适配特定数据源的 JSON 结构
    - 组合调用各 Cleaner 提取实体
    - 处理实体间的关联关系
    - 输出标准化的 CleanedResult
    """
    
    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
    
    @abstractmethod
    def process(self, raw_data: Dict[str, Any]) -> CleanedResult:
        """
        处理单条原始数据
        
        Args:
            raw_data: 原始 JSON 数据
            
        Returns:
            CleanedResult: 清洗结果
        """
        pass
    
    def process_batch(self, raw_data_list: List[Dict[str, Any]]) -> BatchCleanedResult:
        """
        批量处理原始数据
        
        Args:
            raw_data_list: 原始 JSON 数据列表
            
        Returns:
            BatchCleanedResult: 批量清洗结果
        """
        batch_result = BatchCleanedResult()
        
        for i, raw_data in enumerate(raw_data_list):
            try:
                result = self.process(raw_data)
                batch_result.add_result(result)
            except Exception as e:
                self.logger.error(f"处理第 {i+1} 条记录时出错: {e}")
                error_result = CleanedResult(raw_data=raw_data)
                error_result.add_error(str(e))
                batch_result.add_result(error_result)
        
        self.logger.info(f"批量处理完成: {batch_result.summary()}")
        return batch_result
