# `scripts/export_mysql_tables_to_csv.py` 使用说明

`scripts/export_mysql_tables_to_csv.py` 是一个只读 MySQL 导出工具，用于把本项目相关数据表分别导出为 CSV 文件，并在同一输出目录生成数据字典 Markdown。

它不会执行 OpenAlex ELT，不会抓取数据，不会清洗数据，也不会写入 MySQL。

## 快速开始

先确保已安装 MySQL 驱动：

```powershell
pip install pymysql
```

导出默认相关表：

```powershell
python scripts\export_mysql_tables_to_csv.py `
  --mysql-host localhost `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent `
  --output-dir outputs\mysql_csv_export\research_agent
```

执行完成后，输出目录会包含：

```text
outputs/mysql_csv_export/research_agent/
  works.csv
  authors.csv
  institutions.csv
  work_authors.csv
  citations.csv
  ...
  manifest.json
  schema_description.md
```

其中：

- `*.csv`：每张 MySQL 表对应一个 CSV 文件。
- `manifest.json`：导出元数据，包括导出表、行数、跳过表、总行数、导出时间。
- `schema_description.md`：自动生成的数据字典，说明每张表的用途和每个导出字段的含义。

## 常用命令

### 导出全部默认表

```powershell
python scripts\export_mysql_tables_to_csv.py `
  --mysql-host localhost `
  --mysql-port 3306 `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent
```

如果不指定 `--output-dir`，默认输出到：

```text
outputs/mysql_csv_export/<timestamp>/
```

### 只导出指定表

```powershell
python scripts\export_mysql_tables_to_csv.py `
  --mysql-host localhost `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent `
  --tables works,authors,institutions,work_authors,citations `
  --output-dir outputs\mysql_csv_export\selected_tables
```

### 缺表时直接失败

默认情况下，如果某张表不存在，脚本会跳过并写入 `manifest.json`。如果希望缺表直接失败：

```powershell
python scripts\export_mysql_tables_to_csv.py `
  --mysql-host localhost `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent `
  --fail-on-missing-table
```

### 调整批量读取大小

大表导出时可以调大或调小 `--batch-size`：

```powershell
python scripts\export_mysql_tables_to_csv.py `
  --mysql-host localhost `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent `
  --batch-size 5000
```

## 参数说明

### MySQL 连接参数

- `--mysql-host`
  - MySQL 主机地址，默认 `localhost`。
- `--mysql-port`
  - MySQL 端口，默认 `3306`。
- `--mysql-user`
  - MySQL 用户名，默认 `research_agent`。
- `--mysql-password`
  - MySQL 密码，默认空。
- `--mysql-database`
  - MySQL 数据库名，默认 `research_agent`。

### 导出参数

- `--output-dir`
  - CSV 和说明文件输出目录。
  - 不传时默认输出到 `outputs/mysql_csv_export/<timestamp>/`。
- `--tables`
  - 逗号分隔的表名列表。
  - 不传时导出默认 allowlist 中的项目相关表。
- `--batch-size`
  - 每次从 MySQL 读取的行数，默认 `1000`。
- `--encoding`
  - CSV 编码，默认 `utf-8-sig`，便于 Excel 打开中文。
- `--fail-on-missing-table`
  - 开启后，如果请求导出的表不存在，脚本会直接失败。

## 默认导出的表

### 运行与任务

- `analysis_runs`
- `analysis_tasks`
- `mcp_tool_calls`

### OpenAlex 实体

- `works`
- `authors`
- `institutions`
- `venues`
- `concepts`
- `countries`
- `work_types`

### OpenAlex 关系

- `work_authors`
- `author_institutions`
- `work_institutions`
- `work_author_affiliations`
- `work_concepts`
- `work_venues`
- `citations`
- `external_work_refs`

### Corpus 与抓取

- `analysis_corpora`
- `corpus_membership`
- `crawl_frontier`
- `crawl_jobs`

### 图与算法

- `graph_snapshots`
- `graph_nodes`
- `graph_edges`
- `graph_algorithm_runs`

### PDF / Embedding

- `materialization_jobs`
- `paper_files`
- `chunk_runs`
- `embedding_runs`

## 输出文件说明

### CSV 文件

每张表会导出为一个独立 CSV：

```text
<table_name>.csv
```

字段顺序来自 MySQL 查询结果，首行为表头。

### `manifest.json`

记录本次导出的机器可读元数据：

- `database`
- `host`
- `port`
- `exported_at`
- `output_dir`
- `exported_tables`
- `skipped_tables`
- `total_rows`
- `warnings`

### `schema_description.md`

记录本次导出的人工可读数据字典：

- 每张表的含义。
- 每张表对应的 CSV 文件名。
- 每张表导出的行数。
- 每个实际导出字段的含义。
- 跳过的表及原因。

这个文件基于实际导出的列生成，因此如果数据库结构后续变化，说明文件也会随导出结果更新。

## 安全说明

- 脚本只读 MySQL，不执行 `INSERT`、`UPDATE`、`DELETE`、`CREATE TABLE`。
- 脚本不会调用 `openalex_elt_cli.py`。
- 脚本不会访问 OpenAlex。
- 脚本不会执行清洗或 Neo4j 同步。
- 命令示例使用 `<password>` 占位符，不要把真实密码写入文档或提交到仓库。

## 常见问题

### `pymysql is required`

说明当前环境未安装 MySQL 驱动：

```powershell
pip install pymysql
```

### 导出的 CSV 为空

可能原因：

- 目标 MySQL 数据库中还没有运行过 OpenAlex ELT。
- 指定了错误的 database。
- 表存在但尚无数据。

可以先在 MySQL 中检查：

```sql
SHOW TABLES;
SELECT COUNT(*) FROM works;
```

### Excel 打开中文乱码

默认编码是 `utf-8-sig`，通常可被 Excel 正确识别。如果你手动改了 `--encoding utf-8` 后乱码，可以恢复默认值。

### 某些表被跳过

如果数据库 schema 不是最新版本，部分表可能不存在。脚本默认跳过缺失表，并在 `manifest.json` 和 `schema_description.md` 中记录原因。
