"""
Neo4j同步管理器
实现从MySQL到Neo4j的数据同步逻辑，支持全量和增量同步
包含软删除处理和CDC接口预留
"""

import os
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from neo4j import GraphDatabase, Driver, Transaction
from neo4j.exceptions import Neo4jError

from python_backend.common.DBConnector.MySQL_db import MySQLConnection

from .config import Neo4jConfig
from .models import (
    CYPHER_MERGE_NODE, CYPHER_MERGE_RELATIONSHIP,
    CYPHER_CREATE_INDEXES, CYPHER_CREATE_CONSTRAINTS,
    CYPHER_SOFT_DELETE, SyncResult,
    get_node_sync_order, get_relationship_sync_order
)


class Neo4jSyncManager:
    """Neo4j同步管理器类"""
    
    def __init__(self, neo4j_config: Neo4jConfig, mysql_config: Optional[dict] = None):
        """
        初始化同步管理器
        
        Args:
            neo4j_config: Neo4j连接配置
            mysql_config: MySQL连接配置（如果为None，使用现有的MySQLConnection）
        """
        self.neo4j_config = neo4j_config
        self.driver: Optional[Driver] = None
        self.mysql_conn: Optional[MySQLConnection] = None
        self.mysql_config = mysql_config
        
        # CDC回调处理器（预留接口）
        self.cdc_handlers: List[Callable] = []
        
        # 批量同步参数
        self.batch_size = 500
        self.max_workers = 1  # 预留多进程接口，当前使用单进程
        
    def connect(self):
        """建立数据库连接"""
        # 连接Neo4j
        self.driver = GraphDatabase.driver(
            self.neo4j_config.uri,
            auth=(self.neo4j_config.username, self.neo4j_config.password),
            max_connection_lifetime=self.neo4j_config.max_connection_lifetime,
            max_connection_pool_size=self.neo4j_config.max_connection_pool_size,
            connection_acquisition_timeout=self.neo4j_config.connection_acquisition_timeout
        )
        
        # 连接MySQL
        if self.mysql_config:
            self.mysql_conn = MySQLConnection(
                host=self.mysql_config.get('host', 'localhost'),
                port=self.mysql_config.get('port', 3306),
                user=self.mysql_config.get('user', 'root'),
                password=self.mysql_config.get('password', ''),
                database=self.mysql_config.get('database', 'Scientific_Info_db')
            )
        else:
            # 使用默认配置
            self.mysql_conn = MySQLConnection()
        
        # 验证连接
        self.driver.verify_connectivity()
        print(f"✓ 已连接到 Neo4j: {self.neo4j_config.uri}")
        print(f"✓ 已连接到 MySQL: {self.mysql_conn.host}:{self.mysql_conn.port}")
        
    def close(self):
        """关闭数据库连接"""
        if self.driver:
            self.driver.close()
        if self.mysql_conn:
            self.mysql_conn.close()
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # =====================================================
    # 初始化：创建索引和约束
    # =====================================================
    
    def initialize_schema(self):
        """初始化Neo4j schema（索引和约束）"""
        print("\n=== 初始化 Neo4j Schema ===")
        
        with self.driver.session(database=self.neo4j_config.database) as session:
            # 创建约束
            for constraint_cypher in CYPHER_CREATE_CONSTRAINTS:
                try:
                    session.run(constraint_cypher)
                    print(f"✓ 创建约束: {constraint_cypher[:50]}...")
                except Neo4jError as e:
                    if "already exists" in str(e).lower():
                        print(f"⊙ 约束已存在，跳过")
                    else:
                        print(f"✗ 创建约束失败: {e}")
            
            # 创建索引
            for index_cypher in CYPHER_CREATE_INDEXES:
                try:
                    session.run(index_cypher)
                    print(f"✓ 创建索引: {index_cypher[:50]}...")
                except Neo4jError as e:
                    if "already exists" in str(e).lower():
                        print(f"⊙ 索引已存在，跳过")
                    else:
                        print(f"✗ 创建索引失败: {e}")
        
        print("✓ Schema 初始化完成\n")
    
    # =====================================================
    # 节点同步
    # =====================================================
    
    def sync_nodes(self, entity_type: str, incremental: bool = False,
                   last_sync_time: Optional[datetime] = None) -> SyncResult:
        """
        同步节点数据
        
        Args:
            entity_type: 实体类型 ('author', 'work', 'institution'等)
            incremental: 是否增量同步
            last_sync_time: 上次同步时间（仅增量同步时使用）
        
        Returns:
            SyncResult: 同步结果
        """
        print(f"\n--- 同步节点: {entity_type} ({'增量' if incremental else '全量'}) ---")
        
        # 从MySQL查询数据
        data = self._fetch_nodes_from_mysql(entity_type, incremental, last_sync_time)
        
        if not data:
            print(f"⊙ 无数据需要同步")
            return SyncResult(entity_type, 'node', 0, 0, [])
        
        print(f"→ 从MySQL获取了 {len(data)} 条记录")
        
        # 批量写入Neo4j
        success_count = 0
        failed_count = 0
        error_messages = []
        
        with self.driver.session(database=self.neo4j_config.database) as session:
            # 分批处理
            for i in range(0, len(data), self.batch_size):
                batch = data[i:i + self.batch_size]
                try:
                    cypher = CYPHER_MERGE_NODE[entity_type]
                    result = session.run(cypher, nodes=batch)
                    count = result.single()['created_count']
                    success_count += count
                    print(f"  → 批次 {i // self.batch_size + 1}: {count} 条记录")
                except Neo4jError as e:
                    failed_count += len(batch)
                    error_messages.append(f"批次 {i // self.batch_size + 1} 失败: {str(e)[:100]}")
                    print(f"  ✗ 批次 {i // self.batch_size + 1} 失败: {e}")
        
        result = SyncResult(entity_type, 'node', success_count, failed_count, error_messages)
        print(result)
        return result
    
    def _fetch_nodes_from_mysql(self, entity_type: str, incremental: bool,
                                 last_sync_time: Optional[datetime]) -> List[Dict[str, Any]]:
        """从MySQL获取节点数据"""
        
        # 构建SQL查询（根据实体类型）
        table_mapping = {
            'author': 'authors',
            'work': 'works',
            'institution': 'institutions',
            'venue': 'venues',
            'concept': 'concepts',
            'country': 'countries',
            'database': 'databases',
            'work_type': 'work_types'
        }
        
        table_name = table_mapping.get(entity_type)
        if not table_name:
            raise ValueError(f"未知的实体类型: {entity_type}")
        
        # 构建WHERE子句
        where_clause = ""
        if incremental and last_sync_time:
            where_clause = f"WHERE updated_at > '{last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}'"
        
        # 为避免表名可能为 MySQL 保留字（如 `databases`）导致语法错误，使用反引号引用表名
        sql = f"SELECT * FROM `{table_name}` {where_clause}"
        
        with self.mysql_conn.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            rows = cursor.fetchall()
            
            # 转换datetime为字符串（Neo4j兼容）
            for row in rows:
                for key, value in row.items():
                    if isinstance(value, datetime):
                        row[key] = value.isoformat()
            
            return rows
    
    # =====================================================
    # 关系同步
    # =====================================================
    
    def sync_relationships(self, relationship_type: str, incremental: bool = False,
                          last_sync_time: Optional[datetime] = None) -> SyncResult:
        """
        同步关系数据
        
        Args:
            relationship_type: 关系类型 ('authored', 'cites', 'published_in'等)
            incremental: 是否增量同步
            last_sync_time: 上次同步时间
        
        Returns:
            SyncResult: 同步结果
        """
        print(f"\n--- 同步关系: {relationship_type} ({'增量' if incremental else '全量'}) ---")
        
        # 从MySQL查询关系数据
        data = self._fetch_relationships_from_mysql(relationship_type, incremental, last_sync_time)
        
        if not data:
            print(f"⊙ 无数据需要同步")
            return SyncResult(relationship_type, 'relationship', 0, 0, [])
        
        print(f"→ 从MySQL获取了 {len(data)} 条关系")
        
        # 批量写入Neo4j
        success_count = 0
        failed_count = 0
        error_messages = []
        
        with self.driver.session(database=self.neo4j_config.database) as session:
            for i in range(0, len(data), self.batch_size):
                batch = data[i:i + self.batch_size]
                try:
                    cypher = CYPHER_MERGE_RELATIONSHIP[relationship_type]
                    result = session.run(cypher, relationships=batch)
                    count = result.single()['created_count']
                    success_count += count
                    print(f"  → 批次 {i // self.batch_size + 1}: {count} 条关系")
                except Neo4jError as e:
                    failed_count += len(batch)
                    error_messages.append(f"批次 {i // self.batch_size + 1} 失败: {str(e)[:100]}")
                    print(f"  ✗ 批次 {i // self.batch_size + 1} 失败: {e}")
        
        result = SyncResult(relationship_type, 'relationship', success_count, failed_count, error_messages)
        print(result)
        return result
    
    def _fetch_relationships_from_mysql(self, relationship_type: str, incremental: bool,
                                        last_sync_time: Optional[datetime]) -> List[Dict[str, Any]]:
        """从MySQL获取关系数据"""
        
        # 关系类型到MySQL表的映射
        sql_mapping = {
            'authored': """
                SELECT work_id, author_id, author_order, is_corresponding
                FROM works_authors_institutions
            """,
            'cites': """
                SELECT citing_work_id, cited_work_id
                FROM citations
            """,
            'published_in': """
                SELECT work_id, venue_id, volumn_issue, page_nums, is_core, is_primary
                FROM works_venues
            """,
            'about': """
                SELECT work_id, concept_id, score, is_original_keyword
                FROM works_concepts
            """,
            'works_at': """
                SELECT author_id, ins_id, start_year, end_year, is_current, from_source
                FROM author_affiliations
            """,
            'located_in': """
                SELECT ins_id, icountry_id as country_id
                FROM institutions
                WHERE icountry_id IS NOT NULL
            """,
            'affiliated_with': """
                SELECT DISTINCT work_id, ins_id, author_id
                FROM works_authors_institutions
            """,
            'has_type': """
                SELECT work_id, type_id
                FROM works
                WHERE type_id IS NOT NULL
            """
        }
        
        sql = sql_mapping.get(relationship_type)
        if not sql:
            raise ValueError(f"未知的关系类型: {relationship_type}")
        
        # 增量同步逻辑（针对有updated_at字段的表）
        # 注意: citations等表没有updated_at，只能全量同步
        
        with self.mysql_conn.get_connection() as conn:
            cursor = conn.cursor(dictionary=True)
            cursor.execute(sql)
            return cursor.fetchall()
    
    # =====================================================
    # 完整同步流程
    # =====================================================
    
    def sync_all(self, incremental: bool = False, last_sync_time: Optional[datetime] = None) -> List[SyncResult]:
        """
        执行完整同步流程（所有节点和关系）
        
        Args:
            incremental: 是否增量同步
            last_sync_time: 上次同步时间
        
        Returns:
            List[SyncResult]: 所有同步结果
        """
        print("\n" + "="*60)
        print(f"  开始同步: {'增量模式' if incremental else '全量模式'}")
        if incremental and last_sync_time:
            print(f"  上次同步时间: {last_sync_time}")
        print("="*60)
        
        start_time = datetime.now()
        results = []
        
        # 1. 初始化schema（仅全量同步时）
        if not incremental:
            self.initialize_schema()
        
        # 2. 同步节点（按依赖顺序）
        print("\n【阶段 1/2】 同步节点")
        for entity_type in get_node_sync_order():
            try:
                result = self.sync_nodes(entity_type, incremental, last_sync_time)
                results.append(result)
            except Exception as e:
                print(f"✗ 同步 {entity_type} 节点失败: {e}")
                results.append(SyncResult(entity_type, 'node', 0, 0, [str(e)]))
        
        # 3. 同步关系（按依赖顺序）
        print("\n【阶段 2/2】 同步关系")
        for relationship_type in get_relationship_sync_order():
            try:
                result = self.sync_relationships(relationship_type, incremental, last_sync_time)
                results.append(result)
            except Exception as e:
                print(f"✗ 同步 {relationship_type} 关系失败: {e}")
                results.append(SyncResult(relationship_type, 'relationship', 0, 0, [str(e)]))
        
        # 4. 汇总统计
        elapsed_time = (datetime.now() - start_time).total_seconds()
        self._print_summary(results, elapsed_time)
        
        return results
    
    def _print_summary(self, results: List[SyncResult], elapsed_time: float):
        """打印同步汇总信息"""
        print("\n" + "="*60)
        print("  同步完成 - 汇总统计")
        print("="*60)
        
        total_success = sum(r.success_count for r in results)
        total_failed = sum(r.failed_count for r in results)
        
        print(f"\n总记录数: {total_success + total_failed}")
        print(f"  ✓ 成功: {total_success}")
        print(f"  ✗ 失败: {total_failed}")
        print(f"\n耗时: {elapsed_time:.2f} 秒")
        
        # 显示失败详情
        failed_results = [r for r in results if r.failed_count > 0]
        if failed_results:
            print("\n失败详情:")
            for result in failed_results:
                print(f"  - {result}")
                for msg in result.error_messages[:3]:  # 最多显示3条错误
                    print(f"    {msg}")
    
    # =====================================================
    # 软删除处理
    # =====================================================
    
    def soft_delete_node(self, label: str, mysql_id: int) -> bool:
        """
        软删除节点（标记is_deleted=true）
        
        Args:
            label: 节点标签 (Author, Work等)
            mysql_id: MySQL主键ID
        
        Returns:
            bool: 是否成功
        """
        cypher = CYPHER_SOFT_DELETE['node'].format(label=label)
        
        with self.driver.session(database=self.neo4j_config.database) as session:
            try:
                result = session.run(cypher, mysql_id=mysql_id)
                return result.single() is not None
            except Neo4jError as e:
                print(f"✗ 软删除节点失败 ({label}:{mysql_id}): {e}")
                return False
    
    # =====================================================
    # CDC接口预留（用于实时同步）
    # =====================================================
    
    def register_cdc_handler(self, handler: Callable[[Dict[str, Any]], None]):
        """
        注册CDC事件处理器（预留接口）
        
        Args:
            handler: 事件处理回调函数，接收binlog事件字典
                    格式: {"operation": "INSERT|UPDATE|DELETE", 
                           "table": "works", 
                           "data": {...}}
        """
        self.cdc_handlers.append(handler)
        print(f"✓ 已注册CDC处理器: {handler.__name__}")
    
    def handle_binlog_event(self, event: Dict[str, Any]):
        """
        处理MySQL binlog事件（预留接口）
        
        Args:
            event: binlog事件字典
        
        Note:
            实际CDC实现需要集成Debezium/Maxwell/Canal等工具
            此方法定义了统一的事件处理接口
        """
        operation = event.get('operation')  # INSERT, UPDATE, DELETE
        table = event.get('table')
        data = event.get('data')
        
        print(f"→ CDC事件: {operation} on {table}")
        
        # 根据操作类型处理
        if operation == 'INSERT' or operation == 'UPDATE':
            # 增量同步新增/更新的记录
            # TODO: 实现细粒度的单条记录同步
            pass
        elif operation == 'DELETE':
            # 软删除
            # TODO: 根据table和data确定节点类型和ID，调用soft_delete_node
            pass
        
        # 调用所有注册的处理器
        for handler in self.cdc_handlers:
            try:
                handler(event)
            except Exception as e:
                print(f"✗ CDC处理器 {handler.__name__} 执行失败: {e}")
    
    # =====================================================
    # 错误恢复机制预留
    # =====================================================
    
    def save_checkpoint(self, entity_type: str, last_synced_id: int, 
                       last_synced_time: datetime):
        """
        保存同步检查点（预留接口）
        
        Args:
            entity_type: 实体类型
            last_synced_id: 最后同步的记录ID
            last_synced_time: 最后同步时间
        
        Note:
            实际实现需要在MySQL创建sync_checkpoints表
        """
        # TODO: 将检查点写入MySQL的sync_checkpoints表
        # INSERT INTO sync_checkpoints (target_db, entity_type, last_synced_id, last_synced_time)
        # VALUES ('neo4j', entity_type, last_synced_id, last_synced_time)
        # ON DUPLICATE KEY UPDATE last_synced_id=VALUES(last_synced_id), ...
        pass
    
    def load_checkpoint(self, entity_type: str) -> Optional[Dict[str, Any]]:
        """
        加载同步检查点（预留接口）
        
        Args:
            entity_type: 实体类型
        
        Returns:
            Optional[Dict]: 检查点数据 {'last_synced_id': int, 'last_synced_time': datetime}
        
        Note:
            用于断点续传
        """
        # TODO: 从MySQL的sync_checkpoints表读取
        # SELECT last_synced_id, last_synced_time 
        # FROM sync_checkpoints
        # WHERE target_db='neo4j' AND entity_type=entity_type
        pass
