"""
Elasticsearch文档构建器
负责从MySQL数据构建ES文档结构
"""

import os
import json
from typing import List, Dict, Any, Optional
from datetime import datetime
from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import ConnectionError, AuthenticationException, NotFoundError

from python_backend.common.DBConnector.MySQL_db import MySQLConnection


def _format_datetime(value):
    """Format datetime to 'YYYY-MM-DD HH:MM:SS' for ES mapping compatibility."""
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return value


class DocumentIndexer:
    """文档索引器类"""
    
    def __init__(self, mysql_conn: MySQLConnection):
        """
        初始化索引器
        
        Args:
            mysql_conn: MySQL连接对象
        """
        self.mysql_conn = mysql_conn
    
        import os
        import json
        from typing import List, Dict, Any, Optional
        from datetime import datetime
        from elasticsearch import Elasticsearch, helpers
        from elasticsearch.exceptions import ConnectionError, AuthenticationException, NotFoundError

        from python_backend.common.DBConnector.MySQL_db import MySQLConnection

    def build_work_documents(self, work_ids: Optional[List[int]] = None,
                             incremental: bool = False,
                             last_sync_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """构建论文文档

        Args:
            work_ids: 指定要构建的work_id列表（可选）
            incremental: 是否增量同步
            last_sync_time: 上次同步时间

        Returns:
            List[Dict]: ES文档列表
        """

        with self.mysql_conn.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            # 构建WHERE子句
            where_clauses = []
            if work_ids:
                ids_str = ','.join(str(id) for id in work_ids)
                where_clauses.append(f"w.work_id IN ({ids_str})")
            if incremental and last_sync_time:
                where_clauses.append(f"w.updated_at > '{last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}'")
            
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            # 查询基础论文信息
            cursor.execute(f"""
                SELECT 
                    w.work_id, w.doi, w.title, w.abstract, w.publication_date,
                    w.created_at, w.updated_at,
                    wt.work_type_name as work_type,
                    (SELECT COUNT(*) FROM citations WHERE cited_work_id = w.work_id) as cited_by_count
                FROM works w
                LEFT JOIN work_types wt ON w.type_id = wt.type_id
                {where_sql}
            """)
            
            works = cursor.fetchall()
            documents = []
            
            for work in works:
                work_id = work['work_id']
                
                # 构建文档
                doc = {
                    'work_id': work_id,
                    'doi': work['doi'],
                    'title': work['title'],
                    'abstract': work['abstract'],
                    'work_type': work['work_type'],
                    'cited_by_count': work['cited_by_count'] or 0,
                    'publication_date': work['publication_date'].isoformat() if work['publication_date'] else None,

                    'created_at': work['created_at'].isoformat() if work['created_at'] else None,
                    'updated_at': work['updated_at'].isoformat() if work['updated_at'] else None,
                    'is_deleted': False
                }
                
                # 添加作者信息（嵌套对象）
                doc['authors'] = self._get_work_authors(cursor, work_id)
                
                # 添加机构信息（嵌套对象）
                doc['institutions'] = self._get_work_institutions(cursor, work_id)
                
                # 添加期刊信息（嵌套对象）
                doc['venues'] = self._get_work_venues(cursor, work_id)
                
                # 添加概念信息（嵌套对象）
                doc['concepts'] = self._get_work_concepts(cursor, work_id)
                
                # 构建全文检索字段（title + abstract）
                full_text_parts = [work['title'] or '']
                if work['abstract']:
                    full_text_parts.append(work['abstract'])
                doc['full_text'] = ' '.join(full_text_parts)
                
                documents.append(doc)
            
            return documents
    
    def _get_work_authors(self, cursor, work_id: int) -> List[Dict[str, Any]]:
        """获取论文的作者信息"""
        cursor.execute("""
            SELECT 
                a.author_id, a.aname as name, a.orcid,
                wai.author_order, wai.is_corresponding
            FROM works_authors_institutions wai
            JOIN authors a ON wai.author_id = a.author_id
            WHERE wai.work_id = %s
            GROUP BY a.author_id, wai.author_order, wai.is_corresponding
            ORDER BY wai.author_order
        """, (work_id,))
        rows = list(cursor.fetchall())

        # 将数据库中的整数字段（如 is_corresponding: 0/1）转换为布尔值，以符合 ES mapping
        for r in rows:
            try:
                r['is_corresponding'] = bool(r.get('is_corresponding'))
            except Exception:
                r['is_corresponding'] = False

        return rows
    
    def _get_work_institutions(self, cursor, work_id: int) -> List[Dict[str, Any]]:
        """获取论文的机构信息"""
        cursor.execute("""
            SELECT DISTINCT
                i.ins_id, i.iname as name, i.itype as type,
                c.country_code, c.eng_name as country
            FROM works_authors_institutions wai
            JOIN institutions i ON wai.ins_id = i.ins_id
            LEFT JOIN countries c ON i.icountry_id = c.country_id
            WHERE wai.work_id = %s
        """, (work_id,))
        
        return list(cursor.fetchall())
    
    def _get_work_venues(self, cursor, work_id: int) -> List[Dict[str, Any]]:
        """获取论文的期刊信息"""
        cursor.execute("""
            SELECT 
                v.venue_id, v.vname as name, v.issn, v.publisher,
                v.indexing, v.impact_factor, wv.is_primary
            FROM works_venues wv
            JOIN venues v ON wv.venue_id = v.venue_id
            WHERE wv.work_id = %s
        """, (work_id,))
        rows = list(cursor.fetchall())

        # 将 is_primary 转换为布尔值
        for r in rows:
            try:
                r['is_primary'] = bool(r.get('is_primary'))
            except Exception:
                r['is_primary'] = False

        return rows
    
    def _get_work_concepts(self, cursor, work_id: int) -> List[Dict[str, Any]]:
        """获取论文的概念信息"""
        cursor.execute("""
            SELECT 
                c.concept_id, c.cname as name, c.level,
                wc.score
            FROM works_concepts wc
            JOIN concepts c ON wc.concept_id = c.concept_id
            WHERE wc.work_id = %s
            ORDER BY wc.score DESC
        """, (work_id,))
        
        return list(cursor.fetchall())
    
    # =====================================================
    # Authors索引文档构建
    # =====================================================
    
    def build_author_documents(self, author_ids: Optional[List[int]] = None,
                               incremental: bool = False,
                               last_sync_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """构建作者文档"""
        with self.mysql_conn.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            where_clauses = []
            if author_ids:
                ids_str = ','.join(str(id) for id in author_ids)
                where_clauses.append(f"a.author_id IN ({ids_str})")
            if incremental and last_sync_time:
                where_clauses.append(f"a.updated_at > '{last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}'")
            
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            cursor.execute(f"""
                SELECT 
                    a.author_id, a.aname as name, a.orcid,
                    a.created_at, a.updated_at,
                    COUNT(DISTINCT wai.work_id) as works_count,
                    (SELECT COUNT(*) FROM citations c 
                     JOIN works_authors_institutions wai2 ON c.cited_work_id = wai2.work_id
                     WHERE wai2.author_id = a.author_id) as cited_by_count
                FROM authors a
                LEFT JOIN works_authors_institutions wai ON a.author_id = wai.author_id
                {where_sql}
                GROUP BY a.author_id
            """)
            
            authors = cursor.fetchall()
            documents = []
            
            for author in authors:
                author_id = author['author_id']
                
                doc = {
                    'author_id': author_id,
                    'name': author['name'],
                    'orcid': author['orcid'],
                    'works_count': author['works_count'] or 0,
                    'cited_by_count': author['cited_by_count'] or 0,
                    'created_at': author['created_at'].isoformat() if author['created_at'] else None,
                    'updated_at': author['updated_at'].isoformat() if author['updated_at'] else None,
                    'is_deleted': False
                }
                
                # 添加当前机构
                doc['current_institution'] = self._get_author_current_institution(cursor, author_id)
                
                # 添加研究领域（基于发表论文的概念）
                doc['research_areas'] = self._get_author_research_areas(cursor, author_id)
                
                documents.append(doc)
            
            return documents
    
    def _get_author_current_institution(self, cursor, author_id: int) -> Optional[Dict[str, Any]]:
        """获取作者当前机构"""
        cursor.execute("""
            SELECT 
                i.ins_id, i.iname as name,
                c.eng_name as country
            FROM author_affiliations aa
            JOIN institutions i ON aa.ins_id = i.ins_id
            LEFT JOIN countries c ON i.icountry_id = c.country_id
            WHERE aa.author_id = %s AND aa.is_current = TRUE
            LIMIT 1
        """, (author_id,))
        
        result = cursor.fetchone()
        return dict(result) if result else None
    
    def _get_author_research_areas(self, cursor, author_id: int, top_n: int = 10) -> List[str]:
        """获取作者研究领域（基于论文概念）"""
        cursor.execute("""
            SELECT c.cname, SUM(wc.score) as total_score
            FROM works_authors_institutions wai
            JOIN works_concepts wc ON wai.work_id = wc.work_id
            JOIN concepts c ON wc.concept_id = c.concept_id
            WHERE wai.author_id = %s
            GROUP BY c.concept_id
            ORDER BY total_score DESC
            LIMIT %s
        """, (author_id, top_n))
        
        return [row['cname'] for row in cursor.fetchall()]
    
    # =====================================================
    # Venues索引文档构建
    # =====================================================
    
    def build_venue_documents(self, venue_ids: Optional[List[int]] = None,
                             incremental: bool = False,
                             last_sync_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """构建期刊文档"""
        with self.mysql_conn.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            where_clauses = []
            if venue_ids:
                ids_str = ','.join(str(id) for id in venue_ids)
                where_clauses.append(f"v.venue_id IN ({ids_str})")
            if incremental and last_sync_time:
                where_clauses.append(f"v.updated_at > '{last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}'")
            
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            cursor.execute(f"""
                SELECT 
                    v.venue_id, v.vname as name, v.issn, v.issn_print, v.issn_online,
                    v.publisher, v.indexing, v.impact_factor, v.is_open_access,
                    v.discipline, v.created_at, v.updated_at,
                    c.eng_name as country,
                    COUNT(DISTINCT wv.work_id) as works_count
                FROM venues v
                LEFT JOIN countries c ON v.country_id = c.country_id
                LEFT JOIN works_venues wv ON v.venue_id = wv.venue_id
                {where_sql}
                GROUP BY v.venue_id
            """)
            
            venues = cursor.fetchall()
            documents = []
            
            for venue in venues:
                doc = {
                    'venue_id': venue['venue_id'],
                    'name': venue['name'],
                    'issn': venue['issn'],
                    'issn_print': venue['issn_print'],
                    'issn_online': venue['issn_online'],
                    'publisher': venue['publisher'],
                    'indexing': venue['indexing'],
                    'impact_factor': float(venue['impact_factor']) if venue['impact_factor'] else None,
                    'is_open_access': bool(venue['is_open_access']),
                    'country': venue['country'],
                    'discipline': venue['discipline'],
                    'works_count': venue['works_count'] or 0,
                    'created_at': venue['created_at'].isoformat() if venue['created_at'] else None,
                    'updated_at': venue['updated_at'].isoformat() if venue['updated_at'] else None,
                    'is_deleted': False
                }
                documents.append(doc)
            
            return documents
    
    # =====================================================
    # Institutions索引文档构建
    # =====================================================
    
    def build_institution_documents(self, ins_ids: Optional[List[int]] = None,
                                   incremental: bool = False,
                                   last_sync_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """构建机构文档"""
        with self.mysql_conn.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            
            where_clauses = []
            if ins_ids:
                ids_str = ','.join(str(id) for id in ins_ids)
                where_clauses.append(f"i.ins_id IN ({ids_str})")
            if incremental and last_sync_time:
                where_clauses.append(f"i.updated_at > '{last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}'")
            
            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
            
            cursor.execute(f"""
                SELECT 
                    i.ins_id, i.iname as name, i.itype as type,
                    i.created_at, i.updated_at,
                    c.eng_name as country, c.country_code,
                    COUNT(DISTINCT wai.work_id) as works_count,
                    COUNT(DISTINCT wai.author_id) as authors_count
                FROM institutions i
                LEFT JOIN countries c ON i.icountry_id = c.country_id
                LEFT JOIN works_authors_institutions wai ON i.ins_id = wai.ins_id
                {where_sql}
                GROUP BY i.ins_id
            """)
            
            institutions = cursor.fetchall()
            documents = []
            
            for ins in institutions:
                doc = {
                    'ins_id': ins['ins_id'],
                    # 同步字段兼容: 使用更语义化的字段名 institution_id 以满足同步器的 _id 选择逻辑
                    'institution_id': ins['ins_id'],
                    'name': ins['name'],
                    'type': ins['type'],
                    'country': ins['country'],
                    'country_code': ins['country_code'],
                    'works_count': ins['works_count'] or 0,
                    'authors_count': ins['authors_count'] or 0,
                    'created_at': _format_datetime(ins['created_at']) if ins['created_at'] else None,
                    'updated_at': _format_datetime(ins['updated_at']) if ins['updated_at'] else None,
                    'is_deleted': False
                }
                documents.append(doc)
            
            return documents
