# -*- coding: utf-8 -*-
"""
MySQL 数据插入器

负责将清洗后的数据按外键依赖顺序插入数据库。
"""

import logging
from typing import Dict, List, Optional, Set
from dataclasses import asdict

from .connection import MySQLConnection
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
from ..pipelines.base import CleanedResult, BatchCleanedResult

logger = logging.getLogger(__name__)


class MySQLInserter:
    """
    MySQL 数据插入器
    
    按外键依赖顺序插入数据：
    1. countries (基础表)
    2. work_types (基础表)
    3. institutions (依赖 countries)
    4. authors (独立)
    5. venues (依赖 countries)
    6. concepts (独立)
    7. works (依赖 work_types)
    8. works_authors_institutions (依赖 works, authors, institutions)
    9. works_concepts (依赖 works, concepts)
    10. works_venues (依赖 works, venues)
    11. works_databases (依赖 works, databases)
    12. citations (依赖 works - 最后插入，只关联已存在的论文)
    """
    
    def __init__(self, db: MySQLConnection):
        """
        初始化插入器
        
        Args:
            db: MySQL 连接管理器
        """
        self.db = db
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # ID 映射缓存（openalex_id -> 数据库 id）
        self._country_cache: Dict[str, int] = {}  # country_code -> country_id
        self._work_type_cache: Dict[str, int] = {}  # type_name -> type_id
        self._institution_cache: Dict[str, int] = {}  # openalex_id -> ins_id
        self._author_cache: Dict[str, int] = {}  # openalex_id -> author_id
        self._venue_cache: Dict[str, int] = {}  # openalex_id -> venue_id
        self._concept_cache: Dict[str, int] = {}  # openalex_id -> concept_id
        self._work_cache: Dict[str, int] = {}  # openalex_id -> work_id
        self._database_id: Optional[int] = None  # OpenAlex 数据库 ID
    
    def insert_batch(self, batch_result: BatchCleanedResult) -> Dict[str, int]:
        """
        批量插入清洗结果
        
        Args:
            batch_result: 批量清洗结果
            
        Returns:
            插入统计 {entity_type: count}
        """
        stats = {
            "countries": 0,
            "work_types": 0,
            "institutions": 0,
            "authors": 0,
            "venues": 0,
            "concepts": 0,
            "works": 0,
            "work_author_institutions": 0,
            "work_concepts": 0,
            "work_venues": 0,
            "citations": 0,
            "citations_skipped": 0,  # 因被引论文不存在而跳过的引用
        }
        
        try:
            with self.db.transaction():
                # 1. 确保 OpenAlex 数据库记录存在
                self._ensure_database_record()
                
                # 2. 插入基础实体（按依赖顺序）
                stats["countries"] = self._insert_countries(
                    list(batch_result.all_countries.values())
                )
                stats["work_types"] = self._insert_work_types(
                    list(batch_result.all_work_types.values())
                )
                stats["institutions"] = self._insert_institutions(
                    list(batch_result.all_institutions.values())
                )
                stats["authors"] = self._insert_authors(
                    list(batch_result.all_authors.values())
                )
                stats["venues"] = self._insert_venues(
                    list(batch_result.all_venues.values())
                )
                stats["concepts"] = self._insert_concepts(
                    list(batch_result.all_concepts.values())
                )
                
                # 3. 插入论文和关联关系
                for result in batch_result.results:
                    if not result.is_valid():
                        continue
                    
                    # 插入论文
                    work_id = self._insert_work(result.work)
                    if work_id:
                        stats["works"] += 1
                        
                        # 插入关联关系
                        stats["work_author_institutions"] += self._insert_work_author_institutions(
                            result.work_author_institutions, work_id
                        )
                        stats["work_concepts"] += self._insert_work_concepts(
                            result.work_concepts, work_id
                        )
                        stats["work_venues"] += self._insert_work_venues(
                            result.work_venues, work_id
                        )
                        
                        # 插入 works_databases 关联
                        self._insert_work_database(work_id, result.work.openalex_url)
                
                # 4. 最后插入引用关系（只关联已存在的论文）
                citation_stats = self._insert_citations(batch_result.all_citations)
                stats["citations"] = citation_stats["inserted"]
                stats["citations_skipped"] = citation_stats["skipped"]
            
            self.logger.info(f"批量插入完成: {stats}")
            return stats
            
        except Exception as e:
            self.logger.error(f"批量插入失败: {e}")
            raise
    
    def _ensure_database_record(self):
        """确保 OpenAlex 数据库记录存在"""
        if self._database_id:
            return
        
        # 查询是否已存在
        result = self.db.fetchone(
            "SELECT database_id FROM `databases` WHERE dname = %s",
            ("OpenAlex",)
        )
        
        if result:
            self._database_id = result[0]
        else:
            # 插入新记录
            self.db.execute(
                "INSERT INTO `databases` (dname, website, description) VALUES (%s, %s, %s)",
                ("OpenAlex", "https://openalex.org", "免费开放的学术数据库")
            )
            self.db.commit()
            self._database_id = self.db.connection.insert_id()
        
        self.logger.debug(f"OpenAlex database_id: {self._database_id}")
    
    def _insert_countries(self, countries: List[CountryEntity]) -> int:
        """
        插入国家数据
        
        使用 INSERT IGNORE 跳过已存在的记录。
        """
        if not countries:
            return 0
        
        count = 0
        for country in countries:
            if country.country_code in self._country_cache:
                continue
            
            # 查询是否已存在
            result = self.db.fetchone(
                "SELECT country_id FROM countries WHERE country_code = %s",
                (country.country_code,)
            )
            
            if result:
                self._country_cache[country.country_code] = result[0]
            else:
                # 插入新记录
                self.db.execute(
                    """INSERT INTO countries (country_code, eng_name) 
                       VALUES (%s, %s)""",
                    (country.country_code, country.eng_name)
                )
                self._country_cache[country.country_code] = self.db.connection.insert_id()
                count += 1
        
        return count
    
    def _insert_work_types(self, work_types: List[WorkTypeEntity]) -> int:
        """插入论文类型数据"""
        if not work_types:
            return 0
        
        count = 0
        for wt in work_types:
            if wt.work_type_name in self._work_type_cache:
                continue
            
            result = self.db.fetchone(
                "SELECT type_id FROM work_types WHERE work_type_name = %s",
                (wt.work_type_name,)
            )
            
            if result:
                self._work_type_cache[wt.work_type_name] = result[0]
            else:
                self.db.execute(
                    "INSERT INTO work_types (work_type_name) VALUES (%s)",
                    (wt.work_type_name,)
                )
                self._work_type_cache[wt.work_type_name] = self.db.connection.insert_id()
                count += 1
        
        return count
    
    def _insert_institutions(self, institutions: List[InstitutionEntity]) -> int:
        """插入机构数据"""
        if not institutions:
            return 0
        
        count = 0
        for inst in institutions:
            if not inst.openalex_id or inst.openalex_id in self._institution_cache:
                continue
            
            # 获取国家 ID
            country_id = None
            if inst.country_code and inst.country_code in self._country_cache:
                country_id = self._country_cache[inst.country_code]
            
            # 检查是否已存在（基于名称，因为没有唯一外部 ID 字段）
            # TODO: 考虑添加 openalex_id 字段到 institutions 表
            result = self.db.fetchone(
                "SELECT ins_id FROM institutions WHERE iname = %s LIMIT 1",
                (inst.iname,)
            )
            
            if result:
                self._institution_cache[inst.openalex_id] = result[0]
            else:
                self.db.execute(
                    """INSERT INTO institutions (iname, icountry_id, itype) 
                       VALUES (%s, %s, %s)""",
                    (inst.iname, country_id, inst.itype)
                )
                self._institution_cache[inst.openalex_id] = self.db.connection.insert_id()
                count += 1
        
        return count
    
    def _insert_authors(self, authors: List[AuthorEntity]) -> int:
        """插入作者数据"""
        if not authors:
            return 0
        
        count = 0
        for author in authors:
            if not author.openalex_id or author.openalex_id in self._author_cache:
                continue
            
            # 优先通过 ORCID 查找
            if author.orcid:
                result = self.db.fetchone(
                    "SELECT author_id FROM authors WHERE orcid = %s",
                    (author.orcid,)
                )
                if result:
                    self._author_cache[author.openalex_id] = result[0]
                    continue
            
            # 通过名称查找（可能有重名，这里简单处理）
            result = self.db.fetchone(
                "SELECT author_id FROM authors WHERE aname = %s AND orcid IS NULL LIMIT 1",
                (author.aname,)
            )
            
            if result and not author.orcid:
                self._author_cache[author.openalex_id] = result[0]
            else:
                self.db.execute(
                    "INSERT INTO authors (aname, orcid) VALUES (%s, %s)",
                    (author.aname, author.orcid)
                )
                self._author_cache[author.openalex_id] = self.db.connection.insert_id()
                count += 1
        
        return count
    
    def _insert_venues(self, venues: List[VenueEntity]) -> int:
        """插入期刊/会议数据"""
        if not venues:
            return 0
        
        count = 0
        for venue in venues:
            if not venue.openalex_id or venue.openalex_id in self._venue_cache:
                continue
            
            # 获取国家 ID
            country_id = None
            if venue.country_code and venue.country_code in self._country_cache:
                country_id = self._country_cache[venue.country_code]
            
            # 通过 ISSN 或名称查找
            if venue.issn:
                result = self.db.fetchone(
                    "SELECT venue_id FROM venues WHERE issn = %s",
                    (venue.issn,)
                )
            else:
                result = self.db.fetchone(
                    "SELECT venue_id FROM venues WHERE vname = %s LIMIT 1",
                    (venue.vname,)
                )
            
            if result:
                self._venue_cache[venue.openalex_id] = result[0]
            else:
                self.db.execute(
                    """INSERT INTO venues 
                       (vname, issn, issn_print, issn_online, homepage, publisher, is_open_access, country_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (venue.vname, venue.issn, venue.issn_print, venue.issn_online,
                     venue.homepage, venue.publisher, venue.is_open_access, country_id)
                )
                self._venue_cache[venue.openalex_id] = self.db.connection.insert_id()
                count += 1
        
        return count
    
    def _insert_concepts(self, concepts: List[ConceptEntity]) -> int:
        """插入概念数据"""
        if not concepts:
            return 0
        
        count = 0
        for concept in concepts:
            if not concept.openalex_id or concept.openalex_id in self._concept_cache:
                continue
            
            # 通过名称查找
            result = self.db.fetchone(
                "SELECT concept_id FROM concepts WHERE cname = %s LIMIT 1",
                (concept.cname,)
            )
            
            if result:
                self._concept_cache[concept.openalex_id] = result[0]
            else:
                self.db.execute(
                    "INSERT INTO concepts (cname, level) VALUES (%s, %s)",
                    (concept.cname, concept.level)
                )
                self._concept_cache[concept.openalex_id] = self.db.connection.insert_id()
                count += 1
        
        return count
    
    def _insert_work(self, work: WorkEntity) -> Optional[int]:
        """
        插入论文数据
        
        返回插入的 work_id，如果已存在则返回已有 ID。
        """
        if not work:
            return None
        
        if work.openalex_id and work.openalex_id in self._work_cache:
            return self._work_cache[work.openalex_id]
        
        # 通过 DOI 查找
        if work.doi:
            result = self.db.fetchone(
                "SELECT work_id FROM works WHERE doi = %s",
                (work.doi,)
            )
            if result:
                if work.openalex_id:
                    self._work_cache[work.openalex_id] = result[0]
                return result[0]
        
        # 获取类型 ID
        type_id = None
        if work.work_type and work.work_type in self._work_type_cache:
            type_id = self._work_type_cache[work.work_type]
        
        # 处理发表日期
        pub_date = None
        if work.publication_date:
            pub_date = work.publication_date.strftime("%Y-%m-%d %H:%M:%S")
        
        # 插入
        self.db.execute(
            """INSERT INTO works (doi, title, abstract, publication_date, type_id)
               VALUES (%s, %s, %s, %s, %s)""",
            (work.doi, work.title, work.abstract, pub_date, type_id)
        )
        work_id = self.db.connection.insert_id()
        
        if work.openalex_id:
            self._work_cache[work.openalex_id] = work_id
        
        return work_id
    
    def _insert_work_author_institutions(
        self,
        relations: List[WorkAuthorInstitution],
        work_id: int
    ) -> int:
        """插入论文-作者-机构关联"""
        if not relations:
            return 0
        
        count = 0
        for rel in relations:
            # 获取作者和机构 ID
            author_id = self._author_cache.get(rel.author_openalex_id)
            ins_id = self._institution_cache.get(rel.institution_openalex_id)
            
            if not author_id or not ins_id:
                continue
            
            try:
                self.db.execute(
                    """INSERT IGNORE INTO works_authors_institutions 
                       (work_id, author_id, ins_id, author_order, is_corresponding)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (work_id, author_id, ins_id, rel.author_order, rel.is_corresponding)
                )
                count += 1
            except Exception as e:
                self.logger.debug(f"插入 work_author_institution 跳过: {e}")
        
        return count
    
    def _insert_work_concepts(
        self,
        relations: List[WorkConcept],
        work_id: int
    ) -> int:
        """插入论文-概念关联"""
        if not relations:
            return 0
        
        count = 0
        for rel in relations:
            concept_id = self._concept_cache.get(rel.concept_openalex_id)
            
            if not concept_id:
                continue
            
            try:
                self.db.execute(
                    """INSERT IGNORE INTO works_concepts 
                       (work_id, concept_id, score, is_original_keyword)
                       VALUES (%s, %s, %s, %s)""",
                    (work_id, concept_id, rel.score, rel.is_original_keyword)
                )
                count += 1
            except Exception as e:
                self.logger.debug(f"插入 work_concept 跳过: {e}")
        
        return count
    
    def _insert_work_venues(
        self,
        relations: List[WorkVenue],
        work_id: int
    ) -> int:
        """插入论文-期刊关联"""
        if not relations:
            return 0
        
        count = 0
        for rel in relations:
            venue_id = self._venue_cache.get(rel.venue_openalex_id)
            
            if not venue_id:
                continue
            
            try:
                self.db.execute(
                    """INSERT IGNORE INTO works_venues 
                       (work_id, venue_id, volumn_issue, page_nums, is_primary)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (work_id, venue_id, rel.volumn_issue, rel.page_nums, rel.is_primary)
                )
                count += 1
            except Exception as e:
                self.logger.debug(f"插入 work_venue 跳过: {e}")
        
        return count
    
    def _insert_work_database(self, work_id: int, access_url: Optional[str]):
        """插入论文-数据库关联"""
        if not self._database_id:
            return
        
        try:
            self.db.execute(
                """INSERT IGNORE INTO works_databases 
                   (work_id, database_id, access_url)
                   VALUES (%s, %s, %s)""",
                (work_id, self._database_id, access_url)
            )
        except Exception as e:
            self.logger.debug(f"插入 work_database 跳过: {e}")
    
    def _insert_citations(
        self,
        citations: List[CitationEntity]
    ) -> Dict[str, int]:
        """
        插入引用关系
        
        引用关系的特殊处理：
        1. 施引论文（citing_work）必须在当前批次中
        2. 被引论文（cited_work）可能不在当前批次中
        3. 只插入两篇论文都在数据库中的引用关系
        
        Args:
            citations: 引用关系列表
            
        Returns:
            {"inserted": int, "skipped": int}
        """
        if not citations:
            return {"inserted": 0, "skipped": 0}
        
        inserted = 0
        skipped = 0
        
        for citation in citations:
            # 获取施引论文 ID（应该在缓存中）
            citing_work_id = self._work_cache.get(citation.citing_work_openalex_id)
            
            if not citing_work_id:
                # 施引论文不在当前批次中，跳过
                skipped += 1
                continue
            
            # 获取被引论文 ID
            cited_work_id = self._work_cache.get(citation.cited_work_openalex_id)
            
            if not cited_work_id:
                # 被引论文不在缓存中，尝试从数据库查询
                # 这里需要通过 openalex_id 查询，但目前表中没有这个字段
                # 所以暂时跳过未获取的被引论文
                skipped += 1
                continue
            
            try:
                self.db.execute(
                    """INSERT IGNORE INTO citations 
                       (citing_work_id, cited_work_id)
                       VALUES (%s, %s)""",
                    (citing_work_id, cited_work_id)
                )
                inserted += 1
            except Exception as e:
                self.logger.debug(f"插入 citation 跳过: {e}")
                skipped += 1
        
        self.logger.info(f"引用关系插入完成: 成功 {inserted}, 跳过 {skipped}")
        return {"inserted": inserted, "skipped": skipped}
    
    def clear_cache(self):
        """清空 ID 映射缓存"""
        self._country_cache.clear()
        self._work_type_cache.clear()
        self._institution_cache.clear()
        self._author_cache.clear()
        self._venue_cache.clear()
        self._concept_cache.clear()
        self._work_cache.clear()
        self._database_id = None
