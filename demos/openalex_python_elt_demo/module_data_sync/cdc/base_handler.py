"""
CDC（Change Data Capture）基础处理器
定义统一的CDC事件处理接口，用于实时数据同步
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime
from enum import Enum


class CDCOperation(Enum):
    """CDC操作类型枚举"""
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class CDCEvent:
    """CDC事件数据类"""
    
    def __init__(self, operation: CDCOperation, table: str, data: Dict[str, Any],
                 old_data: Optional[Dict[str, Any]] = None,
                 timestamp: Optional[datetime] = None):
        """
        初始化CDC事件
        
        Args:
            operation: 操作类型（INSERT/UPDATE/DELETE）
            table: 表名
            data: 新数据（UPDATE/INSERT时）或被删除的数据（DELETE时）
            old_data: 旧数据（仅UPDATE时有值）
            timestamp: 事件时间戳
        """
        self.operation = operation
        self.table = table
        self.data = data
        self.old_data = old_data
        self.timestamp = timestamp or datetime.now()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式"""
        return {
            'operation': self.operation.value,
            'table': self.table,
            'data': self.data,
            'old_data': self.old_data,
            'timestamp': self.timestamp.isoformat()
        }
    
    @classmethod
    def from_dict(cls, event_dict: Dict[str, Any]) -> 'CDCEvent':
        """从字典创建事件"""
        return cls(
            operation=CDCOperation(event_dict['operation']),
            table=event_dict['table'],
            data=event_dict['data'],
            old_data=event_dict.get('old_data'),
            timestamp=datetime.fromisoformat(event_dict['timestamp'])
        )
    
    def __str__(self):
        return f"CDCEvent({self.operation.value} on {self.table} at {self.timestamp})"


class BaseCDCHandler(ABC):
    """CDC处理器抽象基类"""
    
    def __init__(self, name: str):
        """
        初始化处理器
        
        Args:
            name: 处理器名称
        """
        self.name = name
        self.processed_count = 0
        self.failed_count = 0
    
    @abstractmethod
    def handle_insert(self, event: CDCEvent) -> bool:
        """
        处理INSERT事件
        
        Args:
            event: CDC事件
        
        Returns:
            bool: 是否处理成功
        """
        pass
    
    @abstractmethod
    def handle_update(self, event: CDCEvent) -> bool:
        """
        处理UPDATE事件
        
        Args:
            event: CDC事件
        
        Returns:
            bool: 是否处理成功
        """
        pass
    
    @abstractmethod
    def handle_delete(self, event: CDCEvent) -> bool:
        """
        处理DELETE事件
        
        Args:
            event: CDC事件
        
        Returns:
            bool: 是否处理成功
        """
        pass
    
    def handle_event(self, event: CDCEvent) -> bool:
        """
        统一的事件处理入口
        
        Args:
            event: CDC事件
        
        Returns:
            bool: 是否处理成功
        """
        try:
            if event.operation == CDCOperation.INSERT:
                success = self.handle_insert(event)
            elif event.operation == CDCOperation.UPDATE:
                success = self.handle_update(event)
            elif event.operation == CDCOperation.DELETE:
                success = self.handle_delete(event)
            else:
                print(f"✗ 未知的操作类型: {event.operation}")
                return False
            
            if success:
                self.processed_count += 1
            else:
                self.failed_count += 1
            
            return success
            
        except Exception as e:
            print(f"✗ [{self.name}] 处理事件失败: {event} - {e}")
            self.failed_count += 1
            return False
    
    def get_stats(self) -> Dict[str, int]:
        """获取处理统计信息"""
        return {
            'processed': self.processed_count,
            'failed': self.failed_count
        }
    
    def reset_stats(self):
        """重置统计信息"""
        self.processed_count = 0
        self.failed_count = 0


class Neo4jCDCHandler(BaseCDCHandler):
    """Neo4j CDC处理器（示例实现）"""
    
    def __init__(self, neo4j_sync_manager):
        """
        初始化Neo4j CDC处理器
        
        Args:
            neo4j_sync_manager: Neo4jSyncManager实例
        """
        super().__init__("Neo4j CDC Handler")
        self.sync_manager = neo4j_sync_manager
    
    def handle_insert(self, event: CDCEvent) -> bool:
        """处理INSERT - 同步新节点到Neo4j"""
        # TODO: 实现细粒度的单条记录同步
        # 例如: 如果是works表插入，创建Work节点
        print(f"→ [{self.name}] INSERT: {event.table}")
        return True
    
    def handle_update(self, event: CDCEvent) -> bool:
        """处理UPDATE - 更新Neo4j中的节点属性"""
        # TODO: 根据updated_at增量更新节点
        print(f"→ [{self.name}] UPDATE: {event.table}")
        return True
    
    def handle_delete(self, event: CDCEvent) -> bool:
        """处理DELETE - 软删除Neo4j节点"""
        table_to_label = {
            'authors': 'Author',
            'works': 'Work',
            'institutions': 'Institution',
            'venues': 'Venue'
        }
        
        label = table_to_label.get(event.table)
        if label:
            entity_id = event.data.get(f"{event.table[:-1]}_id")  # 去掉's'
            if entity_id:
                return self.sync_manager.soft_delete_node(label, entity_id)
        
        return False


class ElasticsearchCDCHandler(BaseCDCHandler):
    """Elasticsearch CDC处理器（示例实现）"""
    
    def __init__(self, es_sync_manager):
        """
        初始化Elasticsearch CDC处理器
        
        Args:
            es_sync_manager: ESSyncManager实例
        """
        super().__init__("Elasticsearch CDC Handler")
        self.sync_manager = es_sync_manager
    
    def handle_insert(self, event: CDCEvent) -> bool:
        """处理INSERT - 索引新文档到ES"""
        # TODO: 构建单个文档并索引
        print(f"→ [{self.name}] INSERT: {event.table}")
        return True
    
    def handle_update(self, event: CDCEvent) -> bool:
        """处理UPDATE - 更新ES文档"""
        # TODO: 使用update API更新部分字段
        print(f"→ [{self.name}] UPDATE: {event.table}")
        return True
    
    def handle_delete(self, event: CDCEvent) -> bool:
        """处理DELETE - 软删除ES文档"""
        table_to_entity = {
            'works': 'work',
            'authors': 'author',
            'venues': 'venue',
            'institutions': 'institution'
        }
        
        entity_type = table_to_entity.get(event.table)
        if entity_type:
            entity_id = event.data.get(f"{entity_type}_id")
            if entity_id:
                return self.sync_manager.soft_delete_document(entity_type, entity_id)
        
        return False


class CDCCoordinator:
    """CDC协调器 - 管理多个处理器"""
    
    def __init__(self):
        self.handlers: List[BaseCDCHandler] = []
    
    def register_handler(self, handler: BaseCDCHandler):
        """注册处理器"""
        self.handlers.append(handler)
        print(f"✓ 已注册CDC处理器: {handler.name}")
    
    def handle_event(self, event: CDCEvent):
        """分发事件到所有处理器"""
        for handler in self.handlers:
            handler.handle_event(event)
    
    def get_all_stats(self) -> Dict[str, Dict[str, int]]:
        """获取所有处理器的统计信息"""
        return {
            handler.name: handler.get_stats()
            for handler in self.handlers
        }


# =====================================================
# CDC集成方案说明（预留）
# =====================================================

class DebeziumIntegration:
    """
    Debezium集成方案（预留）
    
    Debezium是一个开源的CDC平台，可以监听MySQL binlog
    并将变更事件发送到Kafka
    
    集成步骤:
    1. 部署Debezium MySQL Connector
    2. 配置Kafka Connect
    3. 订阅Kafka Topic
    4. 解析Debezium事件格式并转换为CDCEvent
    5. 调用CDCCoordinator处理事件
    
    示例配置:
    {
        "name": "mysql-connector",
        "config": {
            "connector.class": "io.debezium.connector.mysql.MySqlConnector",
            "database.hostname": "localhost",
            "database.port": "3306",
            "database.user": "debezium",
            "database.password": "password",
            "database.server.id": "184054",
            "database.server.name": "scientific_info",
            "database.include.list": "Scientific_Info_db",
            "table.include.list": "Scientific_Info_db.works,Scientific_Info_db.authors",
            "database.history.kafka.bootstrap.servers": "kafka:9092",
            "database.history.kafka.topic": "schema-changes.scientific_info"
        }
    }
    """
    pass


class MaxwellIntegration:
    """
    Maxwell集成方案（预留）
    
    Maxwell是一个轻量级的MySQL binlog解析工具
    可以将binlog输出为JSON格式到Kafka/RabbitMQ
    
    集成步骤:
    1. 安装Maxwell
    2. 配置config.properties
    3. 启动Maxwell守护进程
    4. 消费Kafka/RabbitMQ消息
    5. 解析Maxwell JSON格式并转换为CDCEvent
    
    Maxwell JSON格式:
    {
        "database": "Scientific_Info_db",
        "table": "works",
        "type": "insert",
        "ts": 1702546800,
        "data": {"work_id": 123, "title": "..."},
        "old": null
    }
    """
    pass


class CanalIntegration:
    """
    Canal集成方案（预留）
    
    Canal是阿里巴巴开源的MySQL binlog增量订阅&消费组件
    
    集成步骤:
    1. 部署Canal Server
    2. 配置instance.properties
    3. 使用Canal Client订阅
    4. 解析Canal协议并转换为CDCEvent
    
    适用场景: 国内项目，文档丰富，社区活跃
    """
    pass
