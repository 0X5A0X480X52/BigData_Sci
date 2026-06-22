# 数据获取与清洗（OpenAlex ETL）

这是用于从 OpenAlex 获取学术论文数据、执行清洗并插入到 MySQL 的轻量级 ETL 模块。

## 目标

- 从 OpenAlex 获取 Works/Authors/Institutions/Concepts/Venues
- 使用单一职责的 Cleaner 做字段提取与标准化
- 使用 Pipeline 组合清洗逻辑（兼容不同数据源）
- 支持缓存（本地 JSON）和 MySQL 插入
- 支持递归抓取引用关系并带进度显示（tqdm）

## 快速上手（示例）

1. 安装运行依赖（建议使用虚拟环境）

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

1. 运行例子：仅获取、清洗并跳过数据库写入

```powershell
python -m 数据获取与清洗.main -q "machine learning" -m 10 --email your@email.com --no-db
```

1. 递归抓取引用（深度 1：抓取直接被引论文）；带进度条

```powershell
python -m 数据获取与清洗.main -q "machine learning" -m 5 -d 1 --max-citations 50 --email your@email.com --no-db
```

1. 将清洗结果写入 MySQL

```powershell
python -m 数据获取与清洗.main -q "deep learning" -m 50 --email your@email.com --db-host localhost --db-user root --db-pass yourpass --db-name Scientific_Info_db
```

1. 从缓存读取并入库

```powershell
python -m 数据获取与清洗.main --from-cache --no-db
```

## 重要 CLI 参数

- `--query`, `-q`: 搜索关键词
- `--year`, `-y`: 发表年份过滤
- `--max`, `-m`: 最大获取数量（默认 100）
- `--email`, `-e`: OpenAlex polite poll 邮箱（必填用于 API）
- `--no-db`: 跳过数据库写入
- `--no-cache`: 禁用缓存
- `--from-cache`: 从缓存读取数据执行清洗/入库
- `--citation-depth`, `-d`: 引用递归深度（0: 不递归，1: 抓取直接引用）
- `--max-citations`: 每篇论文最多处理的引用数量（防止爆炸式增长）

## 进度与体验

- `get_works_by_ids` 支持 tqdm 进度条（需安装 `tqdm`）
- 缓存优先，可加快重复运行

## 建议与限制

- 递归抓取引用会导致抓取范围快速增长，建议设置合理的 `--max-citations` 或 `--max` 值
- 当插入引用表时，要求 `works` 已存在（默认只插入在数据库中存在的引用）；可以开启递归功能把更多论文抓取并入库

## 开发者提示

- 新增数据源时，添加 `sources/*` 和对应的 `pipelines/*`，保持 Cleaner 的单一职责原则
- `cache/` 提供文件缓存和 MongoDB 接口（后续实现）
- `db/inserter.py` 可根据需求添加 `openalex_id` 字段在 `works/authors/institutions/venues/concepts` 表中以改善去重能力

---

更多细节见 `docs/数据清洗与获取模块设计.md`。
