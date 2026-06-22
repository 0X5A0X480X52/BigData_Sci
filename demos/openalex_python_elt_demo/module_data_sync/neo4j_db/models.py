"""
Neo4j图数据库数据模型定义
包含所有节点类型和关系类型的Cypher模板
"""

from typing import Dict, List, Any
from dataclasses import dataclass


# =====================================================
# 节点类型定义（7种）
# =====================================================

NODE_LABELS = {
    'author': 'Author',
    'work': 'Work',
    'institution': 'Institution',
    'venue': 'Venue',
    'concept': 'Concept',
    'country': 'Country',
    'database': 'Database',
    'work_type': 'WorkType'
}


# =====================================================
# 关系类型定义（8种）
# =====================================================

RELATIONSHIP_TYPES = {
    'authored': 'AUTHORED',           # Author -> Work
    'cites': 'CITES',                 # Work -> Work (引用关系)
    'published_in': 'PUBLISHED_IN',   # Work -> Venue
    'about': 'ABOUT',                 # Work -> Concept
    'works_at': 'WORKS_AT',           # Author -> Institution (历史机构)
    'located_in': 'LOCATED_IN',       # Institution -> Country
    'affiliated_with': 'AFFILIATED_WITH',  # Work -> Institution (通过作者)
    'has_type': 'HAS_TYPE'            # Work -> WorkType
}


# =====================================================
# Cypher查询模板 - 节点创建/更新（使用MERGE）
# =====================================================

CYPHER_MERGE_NODE = {
    # Author节点：使用mysql_id作为唯一标识
    'author': """
        UNWIND $nodes AS node
        MERGE (a:Author {mysql_id: node.author_id})
        SET a.name = node.aname,
            a.orcid = node.orcid,
            a.created_at = node.created_at,
            a.updated_at = node.updated_at,
            a.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(a) as created_count
    """,
    
    # Work节点：论文核心信息
    'work': """
        UNWIND $nodes AS node
        MERGE (w:Work {mysql_id: node.work_id})
        SET w.doi = node.doi,
            w.title = node.title,
            w.abstract = node.abstract,
            w.publication_date = node.publication_date,
            w.created_at = node.created_at,
            w.updated_at = node.updated_at,
            w.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(w) as created_count
    """,
    
    # Institution节点
    'institution': """
        UNWIND $nodes AS node
        MERGE (i:Institution {mysql_id: node.ins_id})
        SET i.name = node.iname,
            i.type = node.itype,
            i.created_at = node.created_at,
            i.updated_at = node.updated_at,
            i.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(i) as created_count
    """,
    
    # Venue节点：期刊/会议
    'venue': """
        UNWIND $nodes AS node
        MERGE (v:Venue {mysql_id: node.venue_id})
        SET v.name = node.vname,
            v.issn = node.issn,
            v.issn_print = node.issn_print,
            v.issn_online = node.issn_online,
            v.publisher = node.publisher,
            v.indexing = node.indexing,
            v.impact_factor = node.impact_factor,
            v.is_open_access = node.is_open_access,
            v.created_at = node.created_at,
            v.updated_at = node.updated_at,
            v.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(v) as created_count
    """,
    
    # Concept节点：概念/关键词
    'concept': """
        UNWIND $nodes AS node
        MERGE (c:Concept {mysql_id: node.concept_id})
        SET c.name = node.cname,
            c.level = node.level,
            c.created_at = node.created_at,
            c.updated_at = node.updated_at,
            c.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(c) as created_count
    """,
    
    # Country节点
    'country': """
        UNWIND $nodes AS node
        MERGE (c:Country {mysql_id: node.country_id})
        SET c.country_code = node.country_code,
            c.country_code3 = node.country_code3,
            c.eng_name = node.eng_name,
            c.cn_name = node.cn_name,
            c.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(c) as created_count
    """,
    
    # Database节点：检索库
    'database': """
        UNWIND $nodes AS node
        MERGE (d:Database {mysql_id: node.database_id})
        SET d.name = node.dname,
            d.website = node.website,
            d.description = node.description,
            d.created_at = node.created_at,
            d.updated_at = node.updated_at,
            d.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(d) as created_count
    """,
    
    # WorkType节点
    'work_type': """
        UNWIND $nodes AS node
        MERGE (wt:WorkType {mysql_id: node.type_id})
        SET wt.name = node.work_type_name,
            wt.description = node.desc,
            wt.is_deleted = COALESCE(node.is_deleted, false)
        RETURN count(wt) as created_count
    """
}


# =====================================================
# Cypher查询模板 - 关系创建（使用MERGE）
# =====================================================

CYPHER_MERGE_RELATIONSHIP = {
    # Author -> Work (AUTHORED)
    'authored': """
        UNWIND $relationships AS rel
        MATCH (a:Author {mysql_id: rel.author_id})
        MATCH (w:Work {mysql_id: rel.work_id})
        MERGE (a)-[r:AUTHORED]->(w)
        SET r.author_order = rel.author_order,
            r.is_corresponding = rel.is_corresponding,
            r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Work -> Work (CITES) - 引用关系，图分析核心
    'cites': """
        UNWIND $relationships AS rel
        MATCH (w1:Work {mysql_id: rel.citing_work_id})
        MATCH (w2:Work {mysql_id: rel.cited_work_id})
        MERGE (w1)-[r:CITES]->(w2)
        SET r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Work -> Venue (PUBLISHED_IN)
    'published_in': """
        UNWIND $relationships AS rel
        MATCH (w:Work {mysql_id: rel.work_id})
        MATCH (v:Venue {mysql_id: rel.venue_id})
        MERGE (w)-[r:PUBLISHED_IN]->(v)
        SET r.volume_issue = rel.volumn_issue,
            r.page_nums = rel.page_nums,
            r.is_core = rel.is_core,
            r.is_primary = rel.is_primary,
            r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Work -> Concept (ABOUT)
    'about': """
        UNWIND $relationships AS rel
        MATCH (w:Work {mysql_id: rel.work_id})
        MATCH (c:Concept {mysql_id: rel.concept_id})
        MERGE (w)-[r:ABOUT]->(c)
        SET r.score = rel.score,
            r.is_original_keyword = rel.is_original_keyword,
            r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Author -> Institution (WORKS_AT) - 历史机构关系
    'works_at': """
        UNWIND $relationships AS rel
        MATCH (a:Author {mysql_id: rel.author_id})
        MATCH (i:Institution {mysql_id: rel.ins_id})
        MERGE (a)-[r:WORKS_AT]->(i)
        SET r.start_year = rel.start_year,
            r.end_year = rel.end_year,
            r.is_current = rel.is_current,
            r.from_source = rel.from_source,
            r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Institution -> Country (LOCATED_IN)
    'located_in': """
        UNWIND $relationships AS rel
        MATCH (i:Institution {mysql_id: rel.ins_id})
        MATCH (c:Country {mysql_id: rel.country_id})
        MERGE (i)-[r:LOCATED_IN]->(c)
        SET r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Work -> Institution (AFFILIATED_WITH) - 通过作者关联
    'affiliated_with': """
        UNWIND $relationships AS rel
        MATCH (w:Work {mysql_id: rel.work_id})
        MATCH (i:Institution {mysql_id: rel.ins_id})
        MERGE (w)-[r:AFFILIATED_WITH]->(i)
        SET r.author_id = rel.author_id,
            r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """,
    
    # Work -> WorkType (HAS_TYPE)
    'has_type': """
        UNWIND $relationships AS rel
        MATCH (w:Work {mysql_id: rel.work_id})
        MATCH (wt:WorkType {mysql_id: rel.type_id})
        MERGE (w)-[r:HAS_TYPE]->(wt)
        SET r.is_deleted = COALESCE(rel.is_deleted, false)
        RETURN count(r) as created_count
    """
}


# =====================================================
# 软删除Cypher模板
# =====================================================

CYPHER_SOFT_DELETE = {
    'node': """
        MATCH (n:{label} {mysql_id: $mysql_id})
        SET n.is_deleted = true, n.deleted_at = datetime()
        RETURN n
    """,
    
    'relationship': """
        MATCH ()-[r:{rel_type}]->()
        WHERE id(r) = $relationship_id
        SET r.is_deleted = true, r.deleted_at = datetime()
        RETURN r
    """
}


# =====================================================
# 索引创建Cypher（提高查询性能）
# =====================================================

CYPHER_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS FOR (a:Author) ON (a.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (w:Work) ON (w.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (w:Work) ON (w.doi)",
    "CREATE INDEX IF NOT EXISTS FOR (i:Institution) ON (i.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (v:Venue) ON (v.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (c:Concept) ON (c.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (c:Country) ON (c.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (d:Database) ON (d.mysql_id)",
    "CREATE INDEX IF NOT EXISTS FOR (wt:WorkType) ON (wt.mysql_id)",
    # 全文索引（可选，用于搜索）
    "CREATE FULLTEXT INDEX work_fulltext IF NOT EXISTS FOR (w:Work) ON EACH [w.title, w.abstract]",
    "CREATE FULLTEXT INDEX author_fulltext IF NOT EXISTS FOR (a:Author) ON EACH [a.name]"
]


# =====================================================
# 约束创建Cypher（保证唯一性）
# =====================================================

CYPHER_CREATE_CONSTRAINTS = [
    "CREATE CONSTRAINT author_mysql_id IF NOT EXISTS FOR (a:Author) REQUIRE a.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT work_mysql_id IF NOT EXISTS FOR (w:Work) REQUIRE w.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT institution_mysql_id IF NOT EXISTS FOR (i:Institution) REQUIRE i.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT venue_mysql_id IF NOT EXISTS FOR (v:Venue) REQUIRE v.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT concept_mysql_id IF NOT EXISTS FOR (c:Concept) REQUIRE c.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT country_mysql_id IF NOT EXISTS FOR (c:Country) REQUIRE c.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT database_mysql_id IF NOT EXISTS FOR (d:Database) REQUIRE d.mysql_id IS UNIQUE",
    "CREATE CONSTRAINT work_type_mysql_id IF NOT EXISTS FOR (wt:WorkType) REQUIRE wt.mysql_id IS UNIQUE"
]


# =====================================================
# 数据模型辅助函数
# =====================================================

def get_node_sync_order() -> List[str]:
    """
    返回节点同步顺序（需要先同步基础节点，再同步依赖节点）
    """
    return [
        'country',       # 1. 国家（无依赖）
        'work_type',     # 2. 论文类型（无依赖）
        'database',      # 3. 检索库（无依赖）
        'concept',       # 4. 概念（无依赖）
        'author',        # 5. 作者（无依赖）
        'institution',   # 6. 机构（无依赖，国家关系可后建）
        'venue',         # 7. 期刊（无依赖，国家关系可后建）
        'work'           # 8. 论文（最后，依赖work_type）
    ]


def get_relationship_sync_order() -> List[str]:
    """
    返回关系同步顺序（需要先确保节点存在）
    """
    return [
        'has_type',         # 1. Work -> WorkType
        'located_in',       # 2. Institution -> Country
        'authored',         # 3. Author -> Work
        'works_at',         # 4. Author -> Institution
        'affiliated_with',  # 5. Work -> Institution
        'published_in',     # 6. Work -> Venue
        'about',            # 7. Work -> Concept
        'cites'             # 8. Work -> Work (引用关系，最后处理)
    ]


@dataclass
class SyncResult:
    """同步结果数据类"""
    entity_type: str
    operation: str  # 'node' or 'relationship'
    success_count: int
    failed_count: int
    error_messages: List[str]
    
    def __str__(self):
        status = "✓" if self.failed_count == 0 else "✗"
        return (f"{status} {self.entity_type} ({self.operation}): "
                f"{self.success_count} 成功, {self.failed_count} 失败")
