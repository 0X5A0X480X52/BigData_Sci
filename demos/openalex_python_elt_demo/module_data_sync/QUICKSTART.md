# MySQL主从数据库同步系统 - 快速开始指南

## 🎯 系统完成情况

✅ **已完成的功能：**

1. **Neo4j图数据库同步模块**
   - 7种节点类型（Author, Work, Institution, Venue, Concept, Country, Database, WorkType）
   - 8种关系类型（AUTHORED, CITES, PUBLISHED_IN, ABOUT, WORKS_AT, LOCATED_IN, AFFILIATED_WITH, HAS_TYPE）
   - 支持全量/增量同步
   - 批量MERGE操作（batch_size=500）
   - 软删除处理
   - 自动创建索引和约束

2. **Elasticsearch检索引擎同步模块**
   - 4个核心索引（works_index, authors_index, venues_index, institutions_index）
   - 中英文混合分析器（ik_max_word + english）
   - 嵌套对象设计（支持多维度过滤）
   - 批量bulk API（batch_size=1000）
   - 软删除处理

3. **CDC实时同步接口预留**
   - 抽象基类 `BaseCDCHandler`
   - 示例实现 `Neo4jCDCHandler` 和 `ElasticsearchCDCHandler`
   - 事件协调器 `CDCCoordinator`
   - Debezium/Maxwell/Canal集成方案文档

4. **统一管理工具**
   - 配置文件 `config.yaml`
   - 主入口脚本 `sync_all.py`
   - 连接测试工具
   - 详细文档和使用说明

5. **MySQL扩展**
   - 同步检查点表 `sync_checkpoints`（断点续传预留）
   - 同步日志表 `sync_logs`（审计追踪）

## 📂 完整文件列表

```
主从数据库同步/
├── neo4j/
│   ├── __init__.py
│   ├── config.py                    # Neo4j连接配置
│   ├── models.py                    # 图模型（507行，含完整Cypher模板）
│   ├── sync_manager.py              # 同步管理器（386行，核心逻辑）
│   ├── sync_from_mysql.py           # CLI入口（175行）
│   └── test_connection.py           # 连接测试（84行）
│
├── ES/
│   ├── __init__.py
│   ├── config.py                    # ES连接配置
│   ├── indexer.py                   # 文档构建器（352行，包含4种实体）
│   ├── sync_manager.py              # 同步管理器（327行）
│   ├── sync_from_mysql.py           # CLI入口（172行）
│   ├── test_connection.py           # 连接测试（88行）
│   └── mappings/
│       ├── works_mapping.json       # 论文索引映射（120行）
│       ├── authors_mapping.json     # 作者索引映射
│       ├── venues_mapping.json      # 期刊索引映射
│       └── institutions_mapping.json # 机构索引映射
│
├── cdc/
│   ├── __init__.py
│   ├── base_handler.py              # CDC基础处理器（382行）
│   └── README.md                    # CDC集成方案（343行）
│
├── config.yaml                       # 统一配置文件（107行）
├── sync_all.py                       # 主入口脚本（224行）
├── requirements.txt                  # Python依赖包
├── README.md                         # 主文档（568行）
└── QUICKSTART.md                     # 本文档
```

**统计：**

- Python代码文件：16个
- JSON配置文件：4个
- 文档文件：3个
- 总代码量：约3000+行

## 🚀 5分钟快速部署

### Step 1: 安装依赖

```powershell
cd 任务三\主从数据库同步
pip install -r requirements.txt
```

### Step 2: 修改配置

编辑 `config.yaml`，修改数据库连接信息：

```yaml
mysql:
  password: your_mysql_password

neo4j:
  password: your_neo4j_password

elasticsearch:
  hosts:
    - localhost:9200
```

### Step 3: 测试连接

```powershell
python sync_all.py --test-connections
```

预期输出：

```
============================================================
  数据库连接测试
============================================================

【MySQL主库】
  ✓ 连接成功: MySQL 8.0.35

【Neo4j从库】
  ✓ 连接成功: bolt://localhost:7687

【Elasticsearch从库】
  ✓ 连接成功: localhost:9200 (v8.11.0)

============================================================
  ✓ 所有连接测试通过
============================================================
```

### Step 4: 执行同步

```powershell
# 全量同步到所有从库
python sync_all.py --mode full
```

## 📋 常用命令速查

### 测试相关

```powershell
# 测试所有连接
python sync_all.py --test-connections

# 测试Neo4j连接
cd neo4j
python test_connection.py

# 测试Elasticsearch连接
cd ES
python test_connection.py
```

### 同步相关

```powershell
# 全量同步到所有从库
python sync_all.py --mode full

# 只同步到Neo4j
python sync_all.py --mode full --target neo4j

# 只同步到Elasticsearch
python sync_all.py --mode full --target elasticsearch

# 增量同步（从指定时间）
python sync_all.py --mode incremental --since "2024-12-01 00:00:00"
```

### 独立模块使用

```powershell
# Neo4j独立同步
cd neo4j
python sync_from_mysql.py --mode full

# Elasticsearch独立同步
cd ES
python sync_from_mysql.py --mode full

# 初始化ES索引
cd ES
python sync_from_mysql.py --init-indices
```

## 🎓 典型使用场景

### 场景1: 首次部署

```powershell
# 1. 创建MySQL检查点表
mysql -u root -p Scientific_Info_db < ../基本表.sql  # 包含sync_checkpoints表

# 2. 测试连接
python sync_all.py --test-connections

# 3. 初始化ES索引
cd ES
python sync_from_mysql.py --init-indices

# 4. 全量同步
cd ..
python sync_all.py --mode full
```

### 场景2: 定期增量同步

```powershell
# 每天凌晨执行（通过Windows计划任务）
python sync_all.py --mode incremental --since "2024-12-13 00:00:00"
```

### 场景3: 单独同步某个实体

```powershell
# 只同步论文数据到Neo4j
cd neo4j
python sync_from_mysql.py --mode full --entity-type work

# 只同步作者数据到ES
cd ES
python sync_from_mysql.py --mode full --entity-type author
```

### 场景4: 重建索引

```powershell
# 强制重建ES索引（会删除现有数据）
cd ES
python sync_from_mysql.py --init-indices --force
python sync_from_mysql.py --mode full
```

## 🔍 验证同步结果

### Neo4j验证

打开Neo4j Browser（<http://localhost:7474），执行：>

```cypher
// 查看节点统计
MATCH (n) RETURN labels(n)[0] as label, count(n) as count ORDER BY count DESC

// 查看关系统计
MATCH ()-[r]->() RETURN type(r) as rel_type, count(r) as count ORDER BY count DESC

// 查看某个作者的论文
MATCH (a:Author {name: "张三"})-[:AUTHORED]->(w:Work)
RETURN a, w LIMIT 10
```

### Elasticsearch验证

```powershell
# 查看所有索引
curl http://localhost:9200/_cat/indices?v

# 查看works_index的文档数
curl http://localhost:9200/works_index/_count

# 搜索测试
curl -X POST "http://localhost:9200/works_index/_search" -H 'Content-Type: application/json' -d'
{
  "query": {
    "match": {
      "title": "deep learning"
    }
  },
  "size": 5
}'
```

### MySQL验证

```sql
-- 查看同步检查点
SELECT * FROM sync_checkpoints;

-- 查看同步日志
SELECT * FROM sync_logs ORDER BY start_time DESC LIMIT 10;
```

## ⚠️ 注意事项

1. **首次同步时间**：全量同步可能需要较长时间，取决于数据量
   - 1万条论文 ≈ 2-5分钟
   - 10万条论文 ≈ 20-50分钟

2. **内存占用**：
   - Neo4j批次大小默认500条，可根据内存调整
   - ES批次大小默认1000条

3. **数据一致性**：
   - 使用软删除策略，不会物理删除数据
   - 增量同步基于`updated_at`字段

4. **错误处理**：
   - 批次失败不会中断整个同步过程
   - 查看日志文件了解详细错误信息

## 🐛 常见问题

**Q1: Neo4j连接超时**

```
A: 检查Neo4j服务状态：neo4j status
   或增加超时时间：config.yaml中修改timeout参数
```

**Q2: Elasticsearch索引已存在错误**

```
A: 使用强制重建：python sync_from_mysql.py --init-indices --force
```

**Q3: MySQL连接被拒绝**

```
A: 检查MySQL用户权限：
   GRANT SELECT ON Scientific_Info_db.* TO 'user'@'%';
```

**Q4: Python导入模块失败**

```
A: 确保在正确目录执行：
   cd 任务三\主从数据库同步
   python sync_all.py
```

## 🔮 后续扩展方向

1. **实现CDC实时同步**
   - 参考 `cdc/README.md`
   - 集成Debezium + Kafka

2. **添加Web监控界面**
   - 显示同步状态
   - 可视化统计图表

3. **实现断点续传**
   - 基于`sync_checkpoints`表
   - 自动恢复中断的同步

4. **性能优化**
   - 多进程并行同步
   - 增加缓存层

## 📞 技术支持

如遇问题，请检查：

1. `README.md` - 详细文档
2. `cdc/README.md` - CDC集成方案
3. 日志文件 - 错误详情

---

**祝使用愉快！** 🎉
