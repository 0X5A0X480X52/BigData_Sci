"""
Elasticsearch同步管理器
实现从MySQL到Elasticsearch的数据同步逻辑
支持全量和增量同步、软删除处理、CDC接口预留
"""

import os
import json
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime, date
from decimal import Decimal
from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import ConnectionError, AuthenticationException, NotFoundError

from python_backend.common.DBConnector.MySQL_db import MySQLConnection

from .config import ElasticsearchConfig
from .indexer import DocumentIndexer


class ESSyncManager:
    """Elasticsearch同步管理器类"""
    
    # 索引名称映射
    INDEX_NAMES = {
        'work': 'works_index',
        'author': 'authors_index',
        'venue': 'venues_index',
        'institution': 'institutions_index'
    }
    
    # 映射文件路径
    MAPPING_FILES = {
        'work': 'mappings/works_mapping.json',
        'author': 'mappings/authors_mapping.json',
        'venue': 'mappings/venues_mapping.json',
        'institution': 'mappings/institutions_mapping.json'
    }
    
    def __init__(self, es_config: ElasticsearchConfig, mysql_config: Optional[dict] = None):
        """
        初始化同步管理器
        
        Args:
            es_config: Elasticsearch连接配置
            mysql_config: MySQL连接配置
        """
        self.es_config = es_config
        self.es_client: Optional[Elasticsearch] = None
        self.mysql_conn: Optional[MySQLConnection] = None
        self.mysql_config = mysql_config
        self.indexer: Optional[DocumentIndexer] = None
        
        # CDC回调处理器（预留接口）
        self.cdc_handlers: List[Callable] = []
        
        # 批量同步参数
        self.batch_size = 1000
        self.max_workers = 1  # 预留多进程接口
        
    def connect(self):
        """建立数据库连接"""
        # 连接Elasticsearch
        if self.es_config.username and self.es_config.password:
            self.es_client = Elasticsearch(
                self.es_config.hosts,
                basic_auth=(self.es_config.username, self.es_config.password),
                verify_certs=self.es_config.verify_certs,
                ca_certs=self.es_config.ca_certs,
                timeout=self.es_config.timeout,
                max_retries=self.es_config.max_retries,
                retry_on_timeout=self.es_config.retry_on_timeout
            )
        else:
            self.es_client = Elasticsearch(
                self.es_config.hosts,
                verify_certs=self.es_config.verify_certs,
                timeout=self.es_config.timeout,
                max_retries=self.es_config.max_retries,
                retry_on_timeout=self.es_config.retry_on_timeout
            )
        
        # 验证连接
        if not self.es_client.ping():
            raise ConnectionError("无法连接到Elasticsearch")
        
        print(f"✓ 已连接到 Elasticsearch: {self.es_config.hosts}")
        
        # 连接MySQL
        if self.mysql_config:
            self.mysql_conn = MySQLConnection(**self.mysql_config)
        else:
            self.mysql_conn = MySQLConnection()
        
        print(f"✓ 已连接到 MySQL: {self.mysql_conn.host}:{self.mysql_conn.port}")
        
        # 初始化文档构建器
        self.indexer = DocumentIndexer(self.mysql_conn)
    
    def close(self):
        """关闭数据库连接"""
        if self.es_client:
            self.es_client.close()
        if self.mysql_conn:
            self.mysql_conn.close()
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # =====================================================
    # 索引初始化
    # =====================================================
    
    def initialize_indices(self, force_recreate: bool = False):
        """
        初始化所有索引（创建索引和映射）
        
        Args:
            force_recreate: 是否强制重建索引（会删除现有数据）
        """
        print("\n=== 初始化 Elasticsearch 索引 ===")
        
        mappings_dir = os.path.join(os.path.dirname(__file__), 'mappings')
        
        for entity_type, index_name in self.INDEX_NAMES.items():
            print(f"\n--- 处理索引: {index_name} ---")

            # 读取映射文件（先载入，以便比较）
            mapping_file = os.path.join(mappings_dir, f"{entity_type}s_mapping.json")
            try:
                with open(mapping_file, 'r', encoding='utf-8') as f:
                    mapping = json.load(f)
            except FileNotFoundError:
                print(f"  ✗ 映射文件未找到: {mapping_file}")
                continue
            except Exception as e:
                print(f"  ✗ 读取映射文件失败: {e}")
                continue

            # 检查索引是否存在
            exists = self.es_client.indices.exists(index=index_name)

            if exists:
                # 如果强制重建，直接删除
                if force_recreate:
                    print(f"  → 删除现有索引...")
                    self.es_client.indices.delete(index=index_name)
                else:
                    # 比较当前索引 mapping 与文件 mapping；若不同则自动删除并重建以应用新格式（尤其是 date format）
                    try:
                        existing = self.es_client.indices.get(index=index_name)
                        existing_mapping = existing.get(index_name, {}).get('mappings', {})
                        # 仅比较映射结构是否一致（把 mapping 文件的 mappings 部分与现有进行比较）
                        file_mappings_part = mapping.get('mappings', {})
                        if json.dumps(existing_mapping, sort_keys=True) != json.dumps(file_mappings_part, sort_keys=True):
                            print(f"  ⚠ 检测到索引 mapping 与定义不一致，尝试删除并重建索引以应用新的 mapping")
                            self.es_client.indices.delete(index=index_name)
                        else:
                            print(f"  ⊙ 索引已存在且 mapping 一致，跳过")
                            continue
                    except Exception:
                        # 若获取 mapping 失败，则跳过或在下一步创建时触发错误
                        print(f"  ⊙ 索引已存在（无法比较 mapping），跳过")
                        continue

            # 创建索引
            try:
                try:
                    self.es_client.indices.create(index=index_name, body=mapping)
                    print(f"  ✓ 索引创建成功")
                except Exception as e_inner:
                    # 常见原因: 集群未安装 IK 分词器，mapping 中使用了 ik_* 分词器或 tokenizer
                    msg = str(e_inner).lower()
                    if 'ik_max_word' in msg or 'ik_smart' in msg or 'tokenizer' in msg:
                        print(f"  ⚠ 检测到自定义 IK 分词器不可用，尝试使用内置分词器回退: {e_inner}")
                        # 在内存中替换 mapping 中的 ik 分词器为 standard（保留原 mapping 文件不变）
                        mapping_str = json.dumps(mapping)
                        mapping_str = mapping_str.replace('ik_max_word', 'standard')
                        mapping_str = mapping_str.replace('ik_smart', 'standard')
                        mapping_fallback = json.loads(mapping_str)
                        try:
                            self.es_client.indices.create(index=index_name, body=mapping_fallback)
                            print(f"  ✓ 索引创建成功（使用 fallback analyzer=standard）")
                        except Exception as e_fallback:
                            print(f"  ✗ 回退创建索引仍然失败: {e_fallback}")
                    else:
                        print(f"  ✗ 创建索引失败: {e_inner}")
            except Exception as e:
                print(f"  ✗ 创建索引失败: {e}")
        
        print("\n✓ 索引初始化完成\n")
    
    # =====================================================
    # 数据同步
    # =====================================================
    
    def sync_entity(self, entity_type: str, incremental: bool = False,
                   last_sync_time: Optional[datetime] = None) -> Dict[str, int]:
        """
        同步指定实体类型的数据
        
        Args:
            entity_type: 实体类型 ('work', 'author', 'venue', 'institution')
            incremental: 是否增量同步
            last_sync_time: 上次同步时间
        
        Returns:
            Dict: 同步统计 {'success': int, 'failed': int}
        """
        print(f"\n--- 同步实体: {entity_type} ({'增量' if incremental else '全量'}) ---")
        
        index_name = self.INDEX_NAMES.get(entity_type)
        if not index_name:
            raise ValueError(f"未知的实体类型: {entity_type}")
        
        # 构建文档
        print(f"→ 从MySQL构建文档...")
        documents = self._build_documents(entity_type, incremental, last_sync_time)
        
        if not documents:
            print(f"⊙ 无数据需要同步")
            return {'success': 0, 'failed': 0}
        
        print(f"→ 获取了 {len(documents)} 条记录")
        
        # 批量索引到ES
        success_count = 0
        failed_count = 0
        
        # 使用bulk helper批量写入
        actions = []
        for doc in documents:
            # 兼容不同实体的 id 字段命名（e.g., ins_id vs institution_id）
            id_field = f"{entity_type}_id"
            entity_id = doc.get(id_field) or doc.get('ins_id') or doc.get('venue_id') or doc.get('work_id') or doc.get('author_id')

            # 在插入时对文档做格式化/类型转换（不修改 mapping 文件）
            normalized_doc = self._normalize_for_es(doc)

            action = {
                '_index': index_name,
                '_source': normalized_doc
            }

            if entity_id is not None:
                action['_id'] = entity_id
            actions.append(action)
            
            # 达到批次大小时执行
            if len(actions) >= self.batch_size:
                success, failed = self._bulk_index(actions)
                success_count += success
                failed_count += failed
                actions = []
        
        # 处理剩余数据
        if actions:
            success, failed = self._bulk_index(actions)
            success_count += success
            failed_count += failed
        
        print(f"\n✓ 同步完成: 成功 {success_count}, 失败 {failed_count}")
        return {'success': success_count, 'failed': failed_count}
    
    def _build_documents(self, entity_type: str, incremental: bool,
                        last_sync_time: Optional[datetime]) -> List[Dict[str, Any]]:
        """构建文档"""
        if entity_type == 'work':
            return self.indexer.build_work_documents(
                incremental=incremental, last_sync_time=last_sync_time
            )
        elif entity_type == 'author':
            return self.indexer.build_author_documents(
                incremental=incremental, last_sync_time=last_sync_time
            )
        elif entity_type == 'venue':
            return self.indexer.build_venue_documents(
                incremental=incremental, last_sync_time=last_sync_time
            )
        elif entity_type == 'institution':
            return self.indexer.build_institution_documents(
                incremental=incremental, last_sync_time=last_sync_time
            )
        else:
            raise ValueError(f"未知的实体类型: {entity_type}")
    
    def _bulk_index(self, actions: List[Dict[str, Any]]) -> tuple:
        """批量索引文档"""
        try:
            # 不使用 stats_only，以便获取完整的错误列表（兼容不同 elasticsearch-py 版本）
            bulk_result = helpers.bulk(
                self.es_client,
                actions,
                stats_only=False,
                raise_on_error=False
            )

            # helpers.bulk 可能返回 (success_count, errors_list)
            # 也有可能在某些版本或调用下返回 (success_count, failed_count)
            success = 0
            errors = []

            if isinstance(bulk_result, tuple) and len(bulk_result) == 2:
                success, errors = bulk_result
            elif isinstance(bulk_result, int):
                success = bulk_result
                errors = []
            else:
                # 兜底解析
                try:
                    success = int(bulk_result)
                except Exception:
                    success = 0
                errors = []

            # 兼容 errors 为 int（表示失败数）的情况
            failed_count = 0
            if isinstance(errors, list):
                failed_count = len(errors)
            elif isinstance(errors, int):
                failed_count = errors
            else:
                failed_count = 0

            # Debug: if no successes, print diagnostics to help trace why
            if success == 0:
                try:
                    print(f"  ⚠ bulk returned success=0 for {len(actions)} actions")
                    print(f"    -> errors type: {type(errors)}, value repr: {repr(errors)[:200]}")
                    if actions:
                        print(f"    -> sample action keys: {list(actions[0].keys())}")
                        print(f"    -> sample action _id: {actions[0].get('_id')}")
                    if isinstance(errors, list) and errors:
                        # 打印第一条失败的详细错误以帮助定位问题
                        try:
                            print(f"    -> sample error: {errors[0]}")
                        except Exception:
                            pass
                except Exception:
                    pass

            # 尝试刷新对应索引以便文档立即可见（等效于单条索引时的 refresh='true'）
            try:
                index_name = actions[0].get('_index') if actions else None
                if index_name:
                    # 使用 refresh='wait_for' 更安全，等待后台刷新完成后返回
                    self.es_client.indices.refresh(index=index_name)
                    print(f"  → 已刷新索引: {index_name}")
            except Exception as e_refresh:
                print(f"  ⚠ 刷新索引失败: {e_refresh}")

            print(f"  → 批次: {success} 成功, {failed_count} 失败")
            return success, failed_count
        except Exception as e:
            print(f"  ✗ 批量索引失败: {e}")
            return 0, len(actions)

    def _normalize_for_es(self, obj: Any) -> Any:
        """递归将对象转换为 Elasticsearch 可接受的基本类型。

        - datetime -> ISO 字符串 (yyyy-MM-dd'T'HH:mm:ss)
        - date -> ISO 日期 (yyyy-MM-dd)
        - Decimal -> float
        - bytes -> utf-8 解码后的字符串
        - set/tuple -> list
        其它不可序列化类型会被转换为字符串
        """
        # 避免修改原始对象，构造新的返回值
        if obj is None:
            return None
        if isinstance(obj, dict):
            return {k: self._normalize_for_es(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self._normalize_for_es(v) for v in obj]
        if isinstance(obj, (datetime, date)):
            try:
                if isinstance(obj, datetime):
                    return obj.strftime("%Y-%m-%dT%H:%M:%S")
                else:
                    return obj.strftime('%Y-%m-%d')
            except Exception:
                return str(obj)
        if isinstance(obj, Decimal):
            try:
                return float(obj)
            except Exception:
                return str(obj)
        if isinstance(obj, bytes):
            try:
                return obj.decode('utf-8')
            except Exception:
                return str(obj)

        # 常见可序列化类型，直接返回
        if isinstance(obj, str):
            # 尝试将 ISO 格式的日期/时间字符串规范为带 'T' 的 ISO 格式
            try:
                # 处理 full ISO datetime (e.g., 2023-02-10T00:00:00)
                dt = datetime.fromisoformat(obj)
                return dt.strftime("%Y-%m-%dT%H:%M:%S")
            except Exception:
                try:
                    # 处理日期字符串 (e.g., 2023-02-10)
                    d = date.fromisoformat(obj)
                    return d.strftime('%Y-%m-%d')
                except Exception:
                    return obj

        if isinstance(obj, (int, float, bool)):
            return obj

        # 兜底：尝试简单转换，否则转为字符串
        try:
            return obj
        except Exception:
            return str(obj)
    
    # =====================================================
    # 完整同步流程
    # =====================================================
    
    def sync_all(self, incremental: bool = False,
                 last_sync_time: Optional[datetime] = None) -> Dict[str, Dict[str, int]]:
        """
        执行完整同步流程（所有实体类型）
        
        Args:
            incremental: 是否增量同步
            last_sync_time: 上次同步时间
        
        Returns:
            Dict: 所有实体的同步统计
        """
        print("\n" + "="*60)
        print(f"  开始同步: {'增量模式' if incremental else '全量模式'}")
        if incremental and last_sync_time:
            print(f"  上次同步时间: {last_sync_time}")
        print("="*60)
        
        start_time = datetime.now()
        results = {}
        
        # 1. 初始化索引（仅全量同步时）
        if not incremental:
            self.initialize_indices(force_recreate=False)
        
        # 2. 同步各实体类型
        for entity_type in ['work', 'author', 'venue', 'institution']:
            try:
                result = self.sync_entity(entity_type, incremental, last_sync_time)
                results[entity_type] = result
            except Exception as e:
                print(f"✗ 同步 {entity_type} 失败: {e}")
                results[entity_type] = {'success': 0, 'failed': 0, 'error': str(e)}
        
        # 3. 汇总统计
        elapsed_time = (datetime.now() - start_time).total_seconds()
        self._print_summary(results, elapsed_time)
        
        return results
    
    def _print_summary(self, results: Dict[str, Dict[str, int]], elapsed_time: float):
        """打印同步汇总信息"""
        print("\n" + "="*60)
        print("  同步完成 - 汇总统计")
        print("="*60)
        
        total_success = sum(r['success'] for r in results.values())
        total_failed = sum(r['failed'] for r in results.values())
        
        print(f"\n各实体统计:")
        for entity_type, result in results.items():
            print(f"  {entity_type}: {result['success']} 成功, {result['failed']} 失败")
        
        print(f"\n总计: {total_success + total_failed} 条记录")
        print(f"  ✓ 成功: {total_success}")
        print(f"  ✗ 失败: {total_failed}")
        print(f"\n耗时: {elapsed_time:.2f} 秒")
    
    # =====================================================
    # 软删除处理
    # =====================================================
    
    def soft_delete_document(self, entity_type: str, entity_id: int) -> bool:
        """
        软删除文档（更新is_deleted字段）
        
        Args:
            entity_type: 实体类型
            entity_id: 实体ID
        
        Returns:
            bool: 是否成功
        """
        index_name = self.INDEX_NAMES.get(entity_type)
        if not index_name:
            return False
        
        try:
            self.es_client.update(
                index=index_name,
                id=entity_id,
                body={
                    'doc': {
                        'is_deleted': True,
                        'deleted_at': datetime.now().isoformat()
                    }
                }
            )
            print(f"✓ 软删除文档: {entity_type}:{entity_id}")
            return True
        except NotFoundError:
            print(f"⊙ 文档不存在: {entity_type}:{entity_id}")
            return False
        except Exception as e:
            print(f"✗ 软删除失败: {e}")
            return False
    
    # =====================================================
    # CDC接口预留
    # =====================================================
    
    def register_cdc_handler(self, handler: Callable[[Dict[str, Any]], None]):
        """
        注册CDC事件处理器（预留接口）
        
        Args:
            handler: 事件处理回调函数
        """
        self.cdc_handlers.append(handler)
        print(f"✓ 已注册CDC处理器: {handler.__name__}")
    
    def handle_binlog_event(self, event: Dict[str, Any]):
        """
        处理MySQL binlog事件（预留接口）
        
        Args:
            event: binlog事件字典
                  格式: {"operation": "INSERT|UPDATE|DELETE",
                        "table": "works",
                        "data": {...}}
        """
        operation = event.get('operation')
        table = event.get('table')
        data = event.get('data')
        
        print(f"→ CDC事件: {operation} on {table}")
        
        # 映射表名到实体类型
        table_to_entity = {
            'works': 'work',
            'authors': 'author',
            'venues': 'venue',
            'institutions': 'institution'
        }
        
        entity_type = table_to_entity.get(table)
        if not entity_type:
            return
        
        # 根据操作类型处理
        if operation in ['INSERT', 'UPDATE']:
            # TODO: 实现细粒度的单条记录更新
            pass
        elif operation == 'DELETE':
            # 软删除
            entity_id = data.get(f'{entity_type}_id')
            if entity_id:
                self.soft_delete_document(entity_type, entity_id)
        
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
        """保存同步检查点（预留接口）"""
        # TODO: 将检查点写入MySQL的sync_checkpoints表
        pass
    
    def load_checkpoint(self, entity_type: str) -> Optional[Dict[str, Any]]:
        """加载同步检查点（预留接口）"""
        # TODO: 从MySQL的sync_checkpoints表读取
        pass
