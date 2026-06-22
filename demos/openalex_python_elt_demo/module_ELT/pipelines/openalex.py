# -*- coding: utf-8 -*-
"""
OpenAlex 清洗流水线

适配 pyalex 库返回的 JSON 结构，组合 Cleaner 完成清洗。
"""

from typing import Any, Dict, List, Optional
import logging

from .base import BasePipeline, CleanedResult
from ..cleaners import (
    WorkCleaner,
    AuthorCleaner,
    InstitutionCleaner,
    ConceptCleaner,
    VenueCleaner,
    CitationCleaner,
)
from ..models.entities import (
    WorkAuthorInstitution,
    WorkConcept,
    WorkVenue,
)

logger = logging.getLogger(__name__)


class OpenAlexPipeline(BasePipeline):
    """
    OpenAlex 数据清洗流水线
    
    适配 pyalex 返回的 Work JSON 结构：
    {
        "id": "https://openalex.org/W...",
        "doi": "https://doi.org/...",
        "title": "...",
        "abstract_inverted_index": {...},
        "publication_date": "YYYY-MM-DD",
        "type": "article",
        "authorships": [
            {
                "author": {"id": "...", "display_name": "...", "orcid": "..."},
                "author_position": "first",
                "is_corresponding": false,
                "institutions": [{"id": "...", "display_name": "...", ...}]
            }
        ],
        "concepts": [
            {"id": "...", "display_name": "...", "level": 2, "score": 0.85}
        ],
        "primary_location": {
            "source": {"id": "...", "display_name": "...", ...},
            ...
        },
        "biblio": {"volume": "...", "issue": "...", "first_page": "...", "last_page": "..."}
    }
    """
    
    def __init__(self):
        super().__init__()
        
        # 初始化各 Cleaner
        self.work_cleaner = WorkCleaner()
        self.author_cleaner = AuthorCleaner()
        self.institution_cleaner = InstitutionCleaner()
        self.concept_cleaner = ConceptCleaner()
        self.venue_cleaner = VenueCleaner()
        self.citation_cleaner = CitationCleaner()
    
    def process(self, raw_data: Dict[str, Any]) -> CleanedResult:
        """
        处理单条 OpenAlex Work 数据
        
        Args:
            raw_data: pyalex 返回的 Work JSON
            
        Returns:
            CleanedResult: 清洗结果
        """
        result = CleanedResult(raw_data=raw_data)
        
        try:
            # 1. 提取论文基本信息
            work = self.work_cleaner.extract(raw_data)
            if not work:
                result.add_error("论文信息提取失败")
                return result
            result.work = work
            
            # 2. 提取作者和机构信息
            self._process_authorships(raw_data, result)
            
            # 3. 提取概念/关键词
            self._process_concepts(raw_data, result)
            
            # 4. 提取期刊/会议信息
            self._process_locations(raw_data, result)
            
            # 5. 提取引用关系
            self._process_citations(raw_data, result)
            
        except Exception as e:
            self.logger.error(f"处理 Work 数据时出错: {e}")
            result.add_error(str(e))
        
        return result
    
    def _process_authorships(
        self,
        raw_data: Dict[str, Any],
        result: CleanedResult
    ):
        """
        处理 authorships 结构
        
        提取作者、机构，并建立 work-author-institution 关联。
        """
        authorships = raw_data.get("authorships") or []
        work_openalex_id = result.work.openalex_id
        
        # 用于去重的集合
        seen_authors = set()
        seen_institutions = set()
        
        for idx, authorship in enumerate(authorships):
            # 提取作者
            author = self.author_cleaner.extract_from_authorship(authorship)
            if not author:
                continue
            
            # 作者去重
            if author.openalex_id and author.openalex_id not in seen_authors:
                seen_authors.add(author.openalex_id)
                result.authors.append(author)
            
            # 提取机构
            institutions = self.institution_cleaner.extract_from_authorship(authorship)
            
            for inst in institutions:
                # 机构去重
                if inst.openalex_id and inst.openalex_id not in seen_institutions:
                    seen_institutions.add(inst.openalex_id)
                    result.institutions.append(inst)
                
                # 建立 work-author-institution 关联
                if author.openalex_id and inst.openalex_id:
                    wai = WorkAuthorInstitution(
                        work_openalex_id=work_openalex_id,
                        author_openalex_id=author.openalex_id,
                        institution_openalex_id=inst.openalex_id,
                        author_order=idx + 1,  # 1-based
                        is_corresponding=authorship.get("is_corresponding", False),
                    )
                    result.work_author_institutions.append(wai)
            
            # 如果作者没有机构，仍然建立关联（使用空机构）
            if not institutions and author.openalex_id:
                # 跳过没有机构的关联，或者使用占位符
                pass
    
    def _process_concepts(
        self,
        raw_data: Dict[str, Any],
        result: CleanedResult
    ):
        """
        处理 concepts 结构
        
        提取概念，并建立 work-concept 关联。
        """
        concepts_data = raw_data.get("concepts") or []
        work_openalex_id = result.work.openalex_id
        
        seen_concepts = set()
        
        for concept_data in concepts_data:
            # 提取概念和权重
            extracted = self.concept_cleaner.extract_with_score(concept_data)
            if not extracted:
                continue
            
            concept, score = extracted
            
            # 概念去重
            if concept.openalex_id and concept.openalex_id not in seen_concepts:
                seen_concepts.add(concept.openalex_id)
                result.concepts.append(concept)
            
            # 建立 work-concept 关联
            if concept.openalex_id:
                wc = WorkConcept(
                    work_openalex_id=work_openalex_id,
                    concept_openalex_id=concept.openalex_id,
                    score=score,
                    is_original_keyword=False,  # OpenAlex concepts 不是原始关键词
                )
                result.work_concepts.append(wc)
    
    def _process_locations(
        self,
        raw_data: Dict[str, Any],
        result: CleanedResult
    ):
        """
        处理 primary_location 和 locations 结构
        
        提取期刊/会议，并建立 work-venue 关联。
        """
        work_openalex_id = result.work.openalex_id
        seen_venues = set()
        
        # 处理 primary_location
        primary_location = raw_data.get("primary_location")
        if primary_location:
            venue = self.venue_cleaner.extract_from_location(primary_location)
            if venue and venue.openalex_id:
                if venue.openalex_id not in seen_venues:
                    seen_venues.add(venue.openalex_id)
                    result.venues.append(venue)
                
                # 建立关联
                biblio = raw_data.get("biblio") or {}
                wv = WorkVenue(
                    work_openalex_id=work_openalex_id,
                    venue_openalex_id=venue.openalex_id,
                    volume=biblio.get("volume"),
                    issue=biblio.get("issue"),
                    first_page=biblio.get("first_page"),
                    last_page=biblio.get("last_page"),
                    is_primary=True,
                )
                result.work_venues.append(wv)
        
        # 处理其他 locations
        locations = raw_data.get("locations") or []
        for location in locations:
            venue = self.venue_cleaner.extract_from_location(location)
            if not venue or not venue.openalex_id:
                continue
            
            if venue.openalex_id in seen_venues:
                continue
            
            seen_venues.add(venue.openalex_id)
            result.venues.append(venue)
            
            # 建立关联（非主要）
            wv = WorkVenue(
                work_openalex_id=work_openalex_id,
                venue_openalex_id=venue.openalex_id,
                is_primary=False,
            )
            result.work_venues.append(wv)
    
    def _process_citations(
        self,
        raw_data: Dict[str, Any],
        result: CleanedResult
    ):
        """
        处理 referenced_works 结构
        
        提取引用关系，建立 citation 关联。
        注意：被引用的论文可能不在当前批次中，需要在插入时特殊处理。
        """
        citations = self.citation_cleaner.extract_citations(raw_data)
        result.citations.extend(citations)
