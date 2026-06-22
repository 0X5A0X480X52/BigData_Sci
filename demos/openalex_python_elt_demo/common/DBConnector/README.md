# DBConnector (Common)

此目录存放共享的数据库连接器与配置加载器，供 `python_backend` 下的各个模块复用。

结构：

- `MySQL_db/`：MySQL 连接与配置（推荐使用 `load_mysql_config` 加载配置）
- `Neo4j_db/`：Neo4j 连接相关（占位）
- `ES_db/`：Elasticsearch 连接相关（占位）

使用示例：

```python
from python_backend.common.DBConnector.MySQL_db import MySQLConnection, load_mysql_config

# 从 YAML 或环境变量加载配置（可传入自定义 path）
cfg = load_mysql_config('path/to/config.yaml')
conn = MySQLConnection(**cfg.to_dict())
with conn.get_connection() as raw_conn:
    cursor = raw_conn.cursor()
    cursor.execute("SELECT 1")
```

兼容性：各旧项目的 `db` 模块已添加 shim，以优先使用此共享实现，确保向后兼容。
