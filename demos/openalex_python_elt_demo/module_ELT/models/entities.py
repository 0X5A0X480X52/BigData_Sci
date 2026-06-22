# -*- coding: utf-8 -*-
"""
数据实体定义

使用 dataclass 定义与数据库表对应的实体类。
每个实体包含 openalex_id 字段用于追踪外部 ID。
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List


@dataclass
class CountryEntity:
    """国家实体 - 对应 countries 表"""
    country_code: str  # ISO-3166 二字码，如 US, CN
    country_code3: Optional[str] = None  # ISO-3166 三字码
    numeric_code: Optional[int] = None  # ISO-3166 数字码
    eng_name: Optional[str] = None  # 英文名
    cn_name: Optional[str] = None  # 中文名
    
    # 数据库主键（插入后填充）
    country_id: Optional[int] = None


@dataclass
class InstitutionEntity:
    """机构实体 - 对应 institutions 表"""
    iname: str  # 机构名称
    itype: Optional[str] = None  # 机构类型: university, company, research institute
    country_code: Optional[str] = None  # 国家代码（用于关联 countries 表）
    
    # 外部 ID 追踪
    openalex_id: Optional[str] = None  # OpenAlex 机构 ID
    
    # 数据库主键（插入后填充）
    ins_id: Optional[int] = None
    icountry_id: Optional[int] = None  # 关联的国家 ID


@dataclass
class AuthorEntity:
    """作者实体 - 对应 authors 表"""
    aname: str  # 作者姓名
    orcid: Optional[str] = None  # ORCID
    
    # 外部 ID 追踪
    openalex_id: Optional[str] = None  # OpenAlex 作者 ID
    
    # 数据库主键（插入后填充）
    author_id: Optional[int] = None


@dataclass
class ConceptEntity:
    """概念/关键词实体 - 对应 concepts 表"""
    cname: str  # 概念名称
    level: Optional[int] = None  # 概念层级（OpenAlex 特有）
    
    # 外部 ID 追踪
    openalex_id: Optional[str] = None  # OpenAlex 概念 ID
    
    # 数据库主键（插入后填充）
    concept_id: Optional[int] = None


@dataclass
class VenueEntity:
    """期刊/会议实体 - 对应 venues 表"""
    vname: str  # 期刊/会议名称
    issn: Optional[str] = None  # ISSN号（通用）
    issn_print: Optional[str] = None  # 印刷版ISSN
    issn_online: Optional[str] = None  # 在线版ISSN
    homepage: Optional[str] = None  # 官网
    publisher: Optional[str] = None  # 出版商
    is_open_access: bool = False  # 是否开放获取
    country_code: Optional[str] = None  # 国家代码
    
    # 外部 ID 追踪
    openalex_id: Optional[str] = None  # OpenAlex Source ID
    
    # 数据库主键（插入后填充）
    venue_id: Optional[int] = None
    country_id: Optional[int] = None


@dataclass
class WorkTypeEntity:
    """论文类型实体 - 对应 work_types 表"""
    work_type_name: str  # 类型名称: article, book-chapter, dataset 等
    desc: Optional[str] = None  # 类型描述
    
    # 数据库主键（插入后填充）
    type_id: Optional[int] = None


@dataclass
class WorkEntity:
    """论文实体 - 对应 works 表"""
    title: str  # 论文标题
    doi: Optional[str] = None  # DOI
    abstract: Optional[str] = None  # 摘要
    publication_date: Optional[datetime] = None  # 发表日期
    work_type: Optional[str] = None  # 论文类型名称（用于关联 work_types）
    
    # 外部 ID 追踪
    openalex_id: Optional[str] = None  # OpenAlex Work ID
    openalex_url: Optional[str] = None  # OpenAlex 完整 URL
    
    # 引用统计（可选，用于扩展）
    cited_by_count: Optional[int] = None
    
    # 数据库主键（插入后填充）
    work_id: Optional[int] = None
    type_id: Optional[int] = None  # 关联的类型 ID


# ============ 关联关系实体 ============

@dataclass
class WorkAuthorInstitution:
    """论文-作者-机构关联 - 对应 works_authors_institutions 表"""
    work_openalex_id: str  # 用于关联
    author_openalex_id: str
    institution_openalex_id: str
    author_order: Optional[int] = None  # 作者排序（1-based）
    is_corresponding: bool = False  # 是否为通讯作者
    
    # 数据库主键（插入后填充）
    wai_id: Optional[int] = None
    work_id: Optional[int] = None
    author_id: Optional[int] = None
    ins_id: Optional[int] = None


@dataclass
class WorkConcept:
    """论文-概念关联 - 对应 works_concepts 表"""
    work_openalex_id: str
    concept_openalex_id: str
    score: Optional[float] = None  # 权重/置信度
    is_original_keyword: bool = False  # 是否为原始关键词
    
    # 数据库主键（插入后填充）
    work_id: Optional[int] = None
    concept_id: Optional[int] = None


@dataclass
class WorkVenue:
    """论文-期刊关联 - 对应 works_venues 表"""
    work_openalex_id: str
    venue_openalex_id: str
    volume: Optional[str] = None  # 卷
    issue: Optional[str] = None  # 期
    first_page: Optional[str] = None  # 起始页
    last_page: Optional[str] = None  # 结束页
    is_primary: bool = True  # 是否为主要发表venue
    
    # 数据库主键（插入后填充）
    work_id: Optional[int] = None
    venue_id: Optional[int] = None
    
    @property
    def volumn_issue(self) -> Optional[str]:
        """生成卷期字符串"""
        parts = []
        if self.volume:
            parts.append(f"Vol.{self.volume}")
        if self.issue:
            parts.append(f"No.{self.issue}")
        return ", ".join(parts) if parts else None
    
    @property
    def page_nums(self) -> Optional[str]:
        """生成页码字符串"""
        if self.first_page and self.last_page:
            return f"{self.first_page}-{self.last_page}"
        elif self.first_page:
            return self.first_page
        return None


@dataclass
class WorkDatabase:
    """论文-数据库关联 - 对应 works_databases 表"""
    work_openalex_id: str
    database_name: str  # 数据库名称，如 "OpenAlex"
    access_url: Optional[str] = None  # 访问链接
    
    # 数据库主键（插入后填充）
    work_id: Optional[int] = None
    database_id: Optional[int] = None


@dataclass
class CitationEntity:
    """
    引用关系实体 - 对应 citations 表
    
    表示 citing_work 引用了 cited_work 的关系。
    注意：OpenAlex 返回的 referenced_works 是被当前论文引用的论文列表。
    """
    citing_work_openalex_id: str  # 施引论文（当前论文）
    cited_work_openalex_id: str   # 被引论文（参考文献）
    
    # 数据库主键（插入后填充）
    citing_work_id: Optional[int] = None
    cited_work_id: Optional[int] = None
