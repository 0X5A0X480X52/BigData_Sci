# CDC（Change Data Capture）实时数据同步方案

本目录包含CDC实时数据同步的接口定义和集成方案说明。

## 📋 目录结构

```
cdc/
├── base_handler.py          # CDC基础处理器和接口定义
└── README.md               # 本文档
```

## 🎯 CDC方案概述

CDC（Change Data Capture）是一种实时捕获数据库变更的技术，通过监听MySQL binlog实现增量数据同步，相比定时轮询具有以下优势：

- ✅ **实时性高**：毫秒级延迟，数据变更即时同步
- ✅ **资源消耗低**：无需定时全表扫描，减轻数据库压力
- ✅ **数据一致性**：保证主从库数据最终一致性
- ✅ **支持回放**：binlog可持久化，支持故障恢复

## 🏗️ 架构设计

```
┌─────────────┐
│ MySQL 主库  │
│ (binlog)    │
└──────┬──────┘
       │ binlog事件
       ↓
┌─────────────────┐
│  CDC工具层      │
│ (Debezium/      │
│  Maxwell/Canal) │
└──────┬──────────┘
       │ 标准化事件
       ↓
┌─────────────────┐
│ CDCCoordinator  │  ← base_handler.py
│  (事件分发器)   │
└──────┬──────────┘
       ├────────────┐
       ↓            ↓
┌──────────┐  ┌─────────────┐
│ Neo4j    │  │ Elasticsearch│
│ Handler  │  │   Handler    │
└──────────┘  └─────────────┘
```

## 📝 接口定义

### 1. CDCEvent 类

统一的CDC事件数据结构：

```python
class CDCEvent:
    operation: CDCOperation  # INSERT/UPDATE/DELETE
    table: str              # 表名
    data: Dict[str, Any]    # 变更数据
    old_data: Optional[Dict[str, Any]]  # 旧数据（UPDATE时）
    timestamp: datetime     # 事件时间戳
```

### 2. BaseCDCHandler 抽象类

所有CDC处理器的基类，需实现三个方法：

```python
class BaseCDCHandler(ABC):
    @abstractmethod
    def handle_insert(self, event: CDCEvent) -> bool:
        """处理INSERT事件"""
        pass
    
    @abstractmethod
    def handle_update(self, event: CDCEvent) -> bool:
        """处理UPDATE事件"""
        pass
    
    @abstractmethod
    def handle_delete(self, event: CDCEvent) -> bool:
        """处理DELETE事件"""
        pass
```

### 3. CDCCoordinator 协调器

管理多个处理器，统一分发事件：

```python
coordinator = CDCCoordinator()
coordinator.register_handler(Neo4jCDCHandler(neo4j_sync_manager))
coordinator.register_handler(ElasticsearchCDCHandler(es_sync_manager))

# 处理事件
event = CDCEvent(CDCOperation.INSERT, 'works', {...})
coordinator.handle_event(event)
```

## 🔧 CDC工具选型

### 方案一：Debezium（推荐）

**优点**：
- 企业级CDC平台，功能完整
- 支持多种数据库（MySQL、PostgreSQL、MongoDB等）
- 与Kafka深度集成，支持分布式部署
- 数据格式标准化，易于解析

**缺点**：
- 依赖Kafka Connect，部署复杂
- 资源消耗较大

**适用场景**：大规模生产环境，需要高可用和横向扩展

**配置示例**：
```json
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
    "transforms": "route",
    "transforms.route.type": "org.apache.kafka.connect.transforms.RegexRouter"
  }
}
```

### 方案二：Maxwell

**优点**：
- 轻量级，部署简单
- 输出格式简洁（JSON）
- 支持多种输出目标（Kafka、RabbitMQ、Kinesis等）
- 资源消耗低

**缺点**：
- 功能相对简单，缺少高级特性
- 社区活跃度不如Debezium

**适用场景**：中小规模项目，快速部署

**启动命令**：
```bash
bin/maxwell --user='maxwell' --password='password' \
    --host='localhost' --producer=kafka \
    --kafka.bootstrap.servers=localhost:9092 \
    --filter='include:Scientific_Info_db.works,Scientific_Info_db.authors'
```

**输出格式**：
```json
{
  "database": "Scientific_Info_db",
  "table": "works",
  "type": "insert",
  "ts": 1702546800,
  "data": {"work_id": 123, "title": "Deep Learning"},
  "old": null
}
```

### 方案三：Canal（国内推荐）

**优点**：
- 阿里巴巴开源，国内文档丰富
- 支持中文社区，问题响应快
- 支持多种客户端（Java、Go、Python）
- 适配国内云服务（阿里云RDS等）

**缺点**：
- 主要面向Java生态
- 国际化支持较弱

**适用场景**：国内项目，团队熟悉Java技术栈

**配置示例**：
```properties
canal.instance.master.address=127.0.0.1:3306
canal.instance.dbUsername=canal
canal.instance.dbPassword=canal
canal.instance.filter.regex=Scientific_Info_db\\.works,Scientific_Info_db\\.authors
```

## 🚀 集成步骤（以Debezium为例）

### Step 1: 准备MySQL环境

```sql
-- 1. 开启binlog（修改my.cnf）
[mysqld]
server-id = 1
log_bin = mysql-bin
binlog_format = ROW
binlog_row_image = FULL

-- 2. 创建CDC用户
CREATE USER 'debezium'@'%' IDENTIFIED BY 'password';
GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT 
ON *.* TO 'debezium'@'%';
FLUSH PRIVILEGES;
```

### Step 2: 部署Debezium + Kafka

```bash
# 1. 启动Zookeeper
docker run -d --name zookeeper -p 2181:2181 zookeeper:3.6

# 2. 启动Kafka
docker run -d --name kafka -p 9092:9092 \
  --link zookeeper:zookeeper \
  -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  wurstmeister/kafka:2.13-2.8.1

# 3. 启动Kafka Connect + Debezium
docker run -d --name connect -p 8083:8083 \
  --link zookeeper:zookeeper \
  --link kafka:kafka \
  -e GROUP_ID=1 \
  -e CONFIG_STORAGE_TOPIC=my_connect_configs \
  -e OFFSET_STORAGE_TOPIC=my_connect_offsets \
  -e STATUS_STORAGE_TOPIC=my_connect_statuses \
  -e BOOTSTRAP_SERVERS=kafka:9092 \
  debezium/connect:2.4
```

### Step 3: 注册MySQL Connector

```bash
curl -X POST http://localhost:8083/connectors \
  -H "Content-Type: application/json" \
  -d @mysql-connector-config.json
```

### Step 4: 实现Python消费者

```python
from kafka import KafkaConsumer
import json
from cdc.base_handler import CDCEvent, CDCOperation, CDCCoordinator

# 创建Kafka消费者
consumer = KafkaConsumer(
    'scientific_info.Scientific_Info_db.works',
    bootstrap_servers=['localhost:9092'],
    value_deserializer=lambda m: json.loads(m.decode('utf-8'))
)

# 创建CDC协调器
coordinator = CDCCoordinator()
coordinator.register_handler(Neo4jCDCHandler(neo4j_sync_manager))
coordinator.register_handler(ElasticsearchCDCHandler(es_sync_manager))

# 消费消息并处理
for message in consumer:
    payload = message.value['payload']
    
    # 解析Debezium事件
    operation_map = {'c': 'INSERT', 'u': 'UPDATE', 'd': 'DELETE'}
    operation = CDCOperation(operation_map[payload['op']])
    
    event = CDCEvent(
        operation=operation,
        table=payload['source']['table'],
        data=payload['after'] or payload['before'],
        old_data=payload.get('before'),
        timestamp=datetime.fromtimestamp(payload['ts_ms'] / 1000)
    )
    
    coordinator.handle_event(event)
```

## 📊 监控与运维

### 性能指标

- **延迟时间**：事件从MySQL产生到同步完成的时间
- **吞吐量**：每秒处理的事件数量
- **成功率**：成功处理的事件占比

### 故障处理

1. **Binlog丢失**：定期备份binlog，设置保留时间
2. **消费积压**：增加Kafka分区数，部署多个消费者
3. **重复消费**：使用幂等性设计，避免重复写入

## 📖 参考资源

- [Debezium官方文档](https://debezium.io/documentation/)
- [Maxwell GitHub](https://github.com/zendesk/maxwell)
- [Canal GitHub](https://github.com/alibaba/canal)
- [MySQL Binlog详解](https://dev.mysql.com/doc/refman/8.0/en/binary-log.html)

## ⚠️ 注意事项

1. **MySQL权限**：确保CDC用户具有REPLICATION权限
2. **Binlog格式**：必须设置为ROW格式
3. **网络延迟**：跨机房部署时注意网络延迟
4. **数据一致性**：初次同步时建议先全量导入再启用CDC
5. **资源预留**：Kafka和Zookeeper需要足够的磁盘空间

## 🔮 未来扩展

- [ ] 实现CDC事件过滤器（支持自定义规则）
- [ ] 添加事件重试机制
- [ ] 集成Prometheus监控指标
- [ ] 实现CDC消费者的自动故障转移
- [ ] 支持Schema变更的自动适配
