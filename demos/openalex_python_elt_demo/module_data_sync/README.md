# MySQL 主从数据库同步系统

## 📖 项目概述

本项目实现了从 MySQL 主库到 Neo4j 图数据库和 Elasticsearch 检索引擎的单向数据同步系统。支持全量同步、增量同步、软删除处理，并为基于 CDC 的实时同步预留了扩展接口。

### 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    MySQL 主库                           │
│         (Scientific_Info_db - 学术信息数据库)            │
│  • 论文、作者、机构、期刊、概念、引用关系等              │
└────────────┬──────────────┬─────────────────────────────┘
             │              │
    手动同步 │              │ CDC实时同步（预留）
             │              │
    ┌────────▼──────┐   ┌───▼────────────────┐
    │ Neo4j 从库    │   │ Elasticsearch 从库 │
    │ (图结构分析)  │   │ (全文检索增强)      │
    └───────────────┘   └────────────────────┘
```

## 🗂️ 目录结构

```
主从数据库同步/
├── neo4j/                      # Neo4j同步模块
│   ├── config.py              # Neo4j连接配置
│   ├── models.py              # 图模型定义（节点、关系、Cypher模板）
│   ├── sync_manager.py        # 同步管理器
│   ├── sync_from_mysql.py     # CLI入口
│   └── test_connection.py     # 连接测试
│
├── ES/                         # Elasticsearch同步模块
│   ├── config.py              # ES连接配置
│   ├── indexer.py             # 文档构建器
│   ├── sync_manager.py        # 同步管理器
│   ├── sync_from_mysql.py     # CLI入口
│   ├── test_connection.py     # 连接测试
│   └── mappings/              # 索引映射定义
│       ├── works_mapping.json
│       ├── authors_mapping.json
│       ├── venues_mapping.json
│       └── institutions_mapping.json
│
├── cdc/                        # CDC接口预留层
│   ├── base_handler.py        # CDC基础处理器（抽象类）
│   └── README.md              # CDC集成方案说明
│
├── config.yaml                 # 统一配置文件
├── sync_all.py                 # 主入口脚本
├── requirements.txt            # Python依赖包
└── README.md                   # 本文档
```

## 🚀 快速开始

### 1. 环境准备

#### 1.1 安装依赖

```bash
cd 主从数据库同步
pip install -r requirements.txt
```

#### 1.2 配置数据库

编辑 `config.yaml`：

```yaml
mysql:
  host: localhost
  port: 3306
  user: root
  password: your_password
  database: Scientific_Info_db

neo4j:
  uri: bolt://localhost:7687
  username: neo4j
  password: your_password

elasticsearch:
  hosts:
    - localhost:9200
```

### 2. 测试连接

```bash
# 测试所有数据库连接
python sync_all.py --test-connections
```

> **说明**: 本 `python_backend` 版本已将 MySQL 连接器统一迁移至 `python_backend/common/DBConnector/MySQL_db`，模块会优先使用该共享实现以减少重复代码与维护成本。

### 3. 执行同步

#### 3.1 全量同步（首次使用）

```bash
# 同步到所有从库
python sync_all.py --mode full

# 只同步到Neo4j
python sync_all.py --mode full --target neo4j

# 只同步到Elasticsearch
python sync_all.py --mode full --target elasticsearch
```

#### 3.2 增量同步

```bash
# 从指定时间增量同步
python sync_all.py --mode incremental --since "2024-12-01 00:00:00"
```

## 📊 数据模型设计

### Neo4j 图数据库

#### 节点类型（8种）

| 节点标签 | MySQL表 | 主要属性 | 用途 |
|---------|---------|---------|------|
| `:Author` | authors | name, orcid | 作者节点 |
| `:Work` | works | title, doi, abstract | 论文节点 |
| `:Institution` | institutions | name, type | 机构节点 |
| `:Venue` | venues | name, issn, impact_factor | 期刊节点 |
| `:Concept` | concepts | name, level | 概念节点 |
| `:Country` | countries | country_code, eng_name | 国家节点 |
| `:Database` | databases | name | 检索库节点 |
| `:WorkType` | work_types | name | 论文类型节点 |

#### 关系类型（8种）

| 关系类型 | 起点→终点 | 属性 | 用途 |
|---------|----------|------|------|
| `:AUTHORED` | Author→Work | author_order, is_corresponding | 作者发表论文 |
| `:CITES` | Work→Work | - | 论文引用关系（核心）|
| `:PUBLISHED_IN` | Work→Venue | volume, issue, is_primary | 论文发表于期刊 |
| `:ABOUT` | Work→Concept | score | 论文主题概念 |
| `:WORKS_AT` | Author→Institution | start_year, end_year | 作者就职机构 |
| `:LOCATED_IN` | Institution→Country | - | 机构所在国家 |
| `:AFFILIATED_WITH` | Work→Institution | - | 论文所属机构 |
| `:HAS_TYPE` | Work→WorkType | - | 论文类型 |

#### 典型查询示例

```cypher
-- 查找某作者的合作者网络（2跳）
MATCH (a1:Author {name: "张三"})-[:AUTHORED]->(w:Work)<-[:AUTHORED]-(a2:Author)
WHERE a1 <> a2
RETURN a1, a2, count(w) as collaboration_count
ORDER BY collaboration_count DESC

-- 查找论文的3层引用传播路径
MATCH path = (w1:Work {doi: "10.1000/xyz"})-[:CITES*1..3]->(w2:Work)
RETURN path

-- 计算某作者的学术影响力（被引用次数）
MATCH (a:Author {name: "李四"})-[:AUTHORED]->(w1:Work)<-[:CITES*1..5]-(w2:Work)
RETURN count(DISTINCT w2) as influenced_papers
```

### Elasticsearch 检索引擎

#### 索引结构

**1. works_index（核心）**

```json
{
  "work_id": 12345,
  "doi": "10.1000/xyz",
  "title": "Deep Learning in Medical Imaging",
  "abstract": "This paper presents...",
  "publication_date": "2023-06-15",
  "cited_by_count": 150,
  "authors": [
    {"author_id": 1, "name": "Zhang San", "orcid": "..."}
  ],
  "institutions": [
    {"ins_id": 10, "name": "MIT", "country": "US"}
  ],
  "venues": [
    {"venue_id": 5, "name": "Nature", "issn": "0028-0836"}
  ],
  "concepts": [
    {"concept_id": 100, "name": "Machine Learning", "score": 0.95}
  ]
}
```

**2. authors_index**

```json
{
  "author_id": 1,
  "name": "Zhang San",
  "orcid": "0000-0001-1234-5678",
  "works_count": 50,
  "cited_by_count": 1200,
  "current_institution": {"name": "MIT", "country": "US"},
  "research_areas": ["Machine Learning", "Computer Vision"]
}
```

#### 典型查询示例

```json
// 多字段联合检索
{
  "query": {
    "multi_match": {
      "query": "deep learning medical imaging",
      "fields": ["title^3", "abstract^1", "concepts.name^2"]
    }
  }
}

// 复合过滤查询
{
  "query": {
    "bool": {
      "must": [{"match": {"title": "COVID-19"}}],
      "filter": [
        {"range": {"publication_date": {"gte": "2020-01-01"}}},
        {"term": {"venues.issn": "0028-0836"}}
      ]
    }
  }
}
```

## 🔧 命令行工具使用

### Neo4j 同步工具

```bash
cd neo4j

# 测试连接
python test_connection.py

# 全量同步所有数据
python sync_from_mysql.py --mode full

# 增量同步（从指定时间）
python sync_from_mysql.py --mode incremental --since "2024-12-01 00:00:00"

# 只同步特定节点类型
python sync_from_mysql.py --mode full --entity-type work

# 只同步特定关系类型
python sync_from_mysql.py --mode full --relationship-type cites

# 使用自定义Neo4j连接
python sync_from_mysql.py --mode full \
  --neo4j-uri bolt://192.168.1.100:7687 \
  --neo4j-user neo4j \
  --neo4j-password mypassword
```

### Elasticsearch 同步工具

```bash
cd ES

# 测试连接
python test_connection.py

# 初始化索引（首次使用）
python sync_from_mysql.py --init-indices

# 强制重建索引（会删除现有数据）
python sync_from_mysql.py --init-indices --force

# 全量同步
python sync_from_mysql.py --mode full

# 增量同步
python sync_from_mysql.py --mode incremental --since "2024-12-01 00:00:00"

# 只同步论文数据
python sync_from_mysql.py --mode full --entity-type work
```

## 🛠️ 高级功能

### 软删除处理

当 MySQL 中的记录被删除时，Neo4j 和 Elasticsearch 会标记为软删除（`is_deleted=true`），而不是物理删除，以保留图结构和历史数据。

```python
# Neo4j软删除
sync_manager.soft_delete_node('Work', work_id=123)

# Elasticsearch软删除
sync_manager.soft_delete_document('work', entity_id=123)
```

### 断点续传（预留）

系统设计了 `sync_checkpoints` 表记录同步进度，支持断点续传：

```sql
-- 查看同步检查点
SELECT * FROM sync_checkpoints;

-- 查看同步日志
SELECT * FROM sync_logs ORDER BY start_time DESC LIMIT 10;
```

### CDC实时同步（预留）

系统为 CDC 实时同步预留了完整接口，支持集成 Debezium/Maxwell/Canal：

```python
from cdc.base_handler import CDCCoordinator, Neo4jCDCHandler, ElasticsearchCDCHandler

# 创建CDC协调器
coordinator = CDCCoordinator()
coordinator.register_handler(Neo4jCDCHandler(neo4j_sync_manager))
coordinator.register_handler(ElasticsearchCDCHandler(es_sync_manager))

# 处理binlog事件
event = CDCEvent(
    operation=CDCOperation.INSERT,
    table='works',
    data={'work_id': 123, 'title': '...'}
)
coordinator.handle_event(event)
```

详见 `cdc/README.md` 了解CDC集成方案。

## 📝 配置说明

### config.yaml 完整配置

```yaml
# MySQL主库配置
mysql:
  host: localhost
  port: 3306
  user: root
  password: your_password
  database: Scientific_Info_db

# Neo4j从库配置
neo4j:
  uri: bolt://localhost:7687
  username: neo4j
  password: your_password
  database: neo4j

# Elasticsearch从库配置
elasticsearch:
  hosts:
    - localhost:9200
  username: null
  password: null

# 同步策略配置
sync:
  batch_size:
    neo4j: 500
    elasticsearch: 1000
  mode: full
  workers: 1  # 多进程并行（预留）

# 软删除策略
soft_delete:
  enabled: true

# CDC配置（预留）
cdc:
  enabled: false
  provider: debezium
```

## 🐛 故障排查

### 常见问题

**1. Neo4j连接失败**
```
✗ 连接失败: ServiceUnavailable
```
解决方法：
- 检查Neo4j服务是否启动：`neo4j status`
- 验证URI格式：`bolt://localhost:7687`
- 确认用户名密码正确

**2. Elasticsearch索引创建失败**
```
✗ 创建索引失败: resource_already_exists_exception
```
解决方法：
```bash
# 删除现有索引
curl -X DELETE http://localhost:9200/works_index

# 或使用强制重建
python sync_from_mysql.py --init-indices --force
```

**3. MySQL连接超时**
```
✗ 连接失败: Can't connect to MySQL server
```
解决方法：
- 检查MySQL服务状态
- 验证防火墙规则
- 确认用户权限：`GRANT SELECT ON Scientific_Info_db.* TO 'user'@'%';`

## 📚 技术栈

- **Python**: 3.8+
- **MySQL**: 8.0+（主库）
- **Neo4j**: 5.x（图数据库）
- **Elasticsearch**: 8.x（检索引擎）
- **依赖包**:
  - `mysql-connector-python`: MySQL驱动
  - `neo4j`: Neo4j Python驱动
  - `elasticsearch`: ES Python客户端
  - `PyYAML`: 配置文件解析

## 🔒 安全建议

1. **生产环境配置**：
   - 使用环境变量存储密码
   - 启用SSL/TLS连接
   - 限制数据库用户权限

2. **Neo4j安全**：
   ```
   # 修改默认密码
   neo4j-admin set-initial-password <new_password>
   ```

3. **Elasticsearch安全**：
   - 启用X-Pack安全插件
   - 配置基于角色的访问控制（RBAC）

## 📈 性能优化

### Neo4j优化

- 批次大小调整：`batch_size: 500`（默认）
- 创建索引和约束（已自动化）
- 使用 APOC 插件加速批量操作

### Elasticsearch优化

- 批次大小调整：`batch_size: 1000`（默认）
- 调整分片和副本数量
- 使用bulk API批量写入

### MySQL优化

- 开启查询缓存
- 优化JOIN查询
- 增加连接池大小

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！

## 📄 许可证

本项目采用 MIT 许可证。

## 📞 联系方式

如有问题，请联系项目维护者。
