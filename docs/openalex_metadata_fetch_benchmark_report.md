# OpenAlex Metadata 获取路径调研与测速结论

## 1. 调研目的

本次调研围绕 `BigData_Sci` 项目中的 OpenAlex 数据获取模块展开，目标是判断以下几种路径是否适合作为“深度学习领域文献 metadata 本地缓存与后续 Neo4j 图数据库构建”的数据源：

1. **OpenAlex API cursor + JSONL**：通过 API 分页获取 metadata，并保存为单个 JSONL 文件。
2. **OpenAlex CLI filter/sample 模式**：通过官方 `openalex download` 命令按 filter 批量下载 metadata。
3. **OpenAlex CLI stdin/IDs 模式**：先收集 Work ID，再通过 `openalex download --stdin` 下载 metadata。
4. **现有 OpenAlex BFS**：从 seed work 或 query 出发做小规模探索，作为后续批量下载策略的启发式输入。

最终需要为项目确定一个可扩展的数据获取架构。

---

## 2. OpenAlex 官方能力与限制

### 2.1 API 分页能力

OpenAlex API 的 `per_page` 最大值为 100。普通 `page` 分页只能访问前 10,000 条结果，超过 10,000 条需要使用 cursor paging。

典型 cursor 请求形式：

```text
https://api.openalex.org/works?filter=publication_year:2024,type:article&per_page=100&cursor=*
```

之后根据响应中的 `meta.next_cursor` 继续分页。

### 2.2 API `select` 参数

API 支持通过 `select=` 控制返回字段，例如：

```text
select=id,doi,title,publication_year,publication_date,type,cited_by_count,authorships,primary_location,topics,referenced_works
```

这可以显著减少响应体积，也更适合直接落盘为 JSONL / Parquet。

### 2.3 OpenAlex CLI 能力

OpenAlex 官方 CLI 是用于下载 filtered subset 的命令行工具，支持：

- `--filter`：按 OpenAlex filter 批量下载。
- `--workers`：并发下载。
- `--nested`：嵌套目录保存，避免单目录文件过多。
- `--fresh`：忽略已有 checkpoint，重新下载。
- `--sample`：随机采样，最大 10,000 条。
- `--seed`：配合 `--sample` 实现可复现采样。
- `--stdin` / `--ids`：按给定 Work ID / DOI 下载。

本地环境中已确认支持：

```powershell
openalex download --help | findstr sample
```

输出显示：

```text
--sample INTEGER RANGE   Download a random sample of N works (max 10,000).
--seed INTEGER           Seed for reproducible random samples (use with --sample).
```

### 2.4 Rate Limit 与 429

OpenAlex API 在超过日额度或请求速率限制时会返回 `429 Too Many Requests`。在本次测试中，`workers=50` 时出现大量：

```text
429, message='Rate limited'
Credits exhausted
```

因此，对于免费 key 或普通个人环境，不建议一开始就使用过高并发。

---

## 3. 测试环境与前置修正

### 3.1 测试环境

测试环境大致为：

```text
OS          : Windows / PowerShell
Python env  : conda env = deeplearning
Project     : BigData_Sci
Package     : openalex-official 0.3.3
```

### 3.2 Windows 兼容性 patch

原始 `openalex_cli/downloader.py` 在 Windows 下存在：

```python
loop.add_signal_handler(sig, self._request_shutdown)
```

Windows 的 asyncio event loop 不支持 `add_signal_handler`，会触发：

```text
NotImplementedError
```

因此已通过 patch 修正：

```python
try:
    loop.add_signal_handler(sig, self._request_shutdown)
except (NotImplementedError, RuntimeError):
    pass
```

同时，metadata 获取失败后，原逻辑可能继续访问未定义的 `meta_content`，导致：

```text
UnboundLocalError: cannot access local variable 'meta_content'
```

因此 patch 中也修正为：metadata 获取失败时记录失败结果并 `continue`，不再进入 success 分支。

---

## 4. 测试结果汇总

### 4.1 API JSONL 模式

测试命令：

```powershell
python test_openalex_cli_10k.py `
  --mode api-jsonl `
  --target 10000 `
  --filter "publication_year:2024,type:article" `
  --output "data/openalex_api_jsonl_speed_10k" `
  --clean-output
```

测试结果：

```text
Records       : 10000
Bytes         : 187.2 MB
Elapsed       : 317.48s / 5.29min
Average speed : 31.50 records/s
Output JSONL  : data\openalex_api_jsonl_speed_10k\metadata_10000.jsonl
```

结论：

- 速度较快。
- 单文件 JSONL 更适合后续转 Parquet / DuckDB / MySQL / Neo4j CSV。
- 不会产生大量小文件。
- 适合作为项目 metadata 主采集通道。

---

### 4.2 OpenAlex CLI stdin/IDs 模式

测试流程：

```text
API collect_work_ids
    ↓
work_ids_10000.txt
    ↓
openalex download --stdin
    ↓
每篇 work 保存一个 JSON 文件
```

测试现象：

```text
Collecting Work IDs: 10000/10000 [01:46, 94.20id/s]
CLI metadata JSON: 19/10000 [00:26, 1.12s/file]
```

测试被中断。

原因分析：

- ID 收集阶段本身很快。
- CLI `--stdin` 模式在内部可能会对每个 Work ID 执行逐篇 metadata 请求。
- 从本地 `downloader.py` 的逻辑看，ID 模式可能存在 producer 阶段获取一次 metadata、worker 阶段保存完整 metadata 时再获取一次 metadata 的情况。
- 因此该模式对于 10,000+ metadata 批量下载效率较低。

结论：

```text
OpenAlex CLI stdin/IDs 模式不推荐用于大规模 metadata 获取。
它更适合少量 ID 补全、指定论文下载或后续 PDF/XML 内容下载。
```

---

### 4.3 OpenAlex CLI filter/sample 模式

为了避免 filter 命中过大导致无法自动终止，使用 `--sample 10000 --seed 42` 限制为随机采样 10,000 条。

测试命令：

```powershell
Measure-Command {
  openalex download `
    --api-key $env:OPENALEX_API_KEY `
    --output data/openalex_cli_filter_sample_10k_w10 `
    --filter "publication_year:2024,type:article" `
    --sample 10000 `
    --seed 42 `
    --workers 10 `
    --nested `
    --fresh
}
```

测试结果：

```text
Download complete:
  Downloaded: 10.0K files (215.5 MB)
  Failed: 0 files
  Duration: 739.6s
  Average speed: 298.4 KB/s

Measure-Command:
  TotalMinutes      : 12.3404729833333
  TotalSeconds      : 740.428379
```

换算：

```text
10000 / 740.43 ≈ 13.51 files/s
215.5 MB / 10000 ≈ 21.55 KB/file
```

结论：

- CLI filter/sample 模式可以正常工作。
- `workers=10` 下未出现失败文件。
- 速度约为 API JSONL 的 43%。
- 适合作为官方 CLI 通路验证、抽样下载或后续 PDF/XML 下载入口。
- 不如 API JSONL 适合作为 metadata 大规模主采集通道。

---

## 5. 横向对比

| 模式 | 10,000 条耗时 | 吞吐 | 数据大小 | 输出形态 | 是否推荐做主通道 |
|---|---:|---:|---:|---|---|
| API cursor + JSONL | 5.29 min | 31.50 records/s | 187.2 MB | 单个 JSONL | 推荐 |
| CLI filter + sample | 12.34 min | 13.51 files/s | 215.5 MB | 10,000 个 JSON | 可选 |
| CLI stdin / IDs | 明显偏慢 | 约 1 file/s 量级 | 多 JSON | 10,000 个 JSON | 不推荐 |

核心判断：

```text
OpenAlex API cursor + JSONL 是当前最适合作为 BigData_Sci metadata 主采集通道的方案。
OpenAlex CLI filter/sample 是可用的辅助方案。
OpenAlex CLI stdin/IDs 不适合大规模 metadata 下载。
```

---

## 6. 线性规模估计

基于当前测试结果进行近似线性估计，实际大规模运行会受到网络波动、API 限流、磁盘写入和 key 额度影响。

### 6.1 API JSONL 模式估计

| 规模 | 预计耗时 | 预计 JSONL 大小 |
|---:|---:|---:|
| 10,000 | 5.29 min | 187 MB |
| 100,000 | 约 52.9 min | 约 1.87 GB |
| 1,000,000 | 约 8.8 h | 约 18.7 GB |

### 6.2 CLI filter/sample 模式估计

| 规模 | 预计耗时 | 预计 JSON 文件总大小 |
|---:|---:|---:|
| 10,000 | 12.34 min | 215.5 MB |
| 100,000 | 约 2.06 h | 约 2.16 GB |
| 1,000,000 | 约 20.6 h | 约 21.55 GB |

注意：CLI 模式会产生大量小文件，Windows 下小文件写入、杀毒扫描、文件系统遍历可能进一步拖慢后续处理。

---

## 7. 对 BigData_Sci 的架构建议

### 7.1 数据获取模块建议拆分

建议将 OpenAlex 数据获取拆分为四类 provider / mode：

```text
openalex_bfs_scout
    从 query / seed work 出发做小规模 BFS 探索。
    用于识别领域边界、topic、source、year 分布和关键 seed。

openalex_api_bulk
    主采集通道。
    使用 API cursor + select + JSONL / Parquet 获取大规模 metadata。

openalex_cli_filter
    官方 CLI 辅助通道。
    使用 --filter / --sample / --workers / --nested 下载 subset。

openalex_cli_ids
    小规模补全通道。
    对指定 Work ID / DOI 进行补全，不用于 10万+ metadata 主采集。
```

### 7.2 推荐主流程

```text
阶段 A：BFS Scout
    输入 query / seed work
    小规模 BFS 探索
    输出 topic/year/source/citation 分布
    生成 download_plan.json

阶段 B：API Bulk Metadata
    根据 download_plan.json 构造 OpenAlex filter
    API cursor 分页
    select 必要字段
    保存 metadata.jsonl
    转 Parquet / DuckDB

阶段 C：清洗与入库
    JSONL / Parquet
        ↓
    BatchCleaner
        ↓
    MySQL 规范化存储
        ↓
    Neo4j CSV / Neo4j Sync

阶段 D：图算法分析
    citation graph
    co-author graph
    topic-year graph
    institution collaboration graph
```

---

## 8. 推荐字段选择

API JSONL 主通道建议保留：

```text
id
 doi
 title
 publication_year
 publication_date
 type
 cited_by_count
 authorships
 primary_location
 topics
 keywords
 referenced_works
 open_access
```

第一版不建议直接保留：

```text
abstract_inverted_index
locations 全量列表
best_oa_location 全量对象
大段 URL / PDF 链接
全文 PDF / TEI XML
```

如果后续需要摘要、全文或 embedding，可单独设计：

```text
metadata 主库：JSONL / Parquet / MySQL
图关系库：Neo4j
全文检索：Elasticsearch / Meilisearch
向量库：Qdrant / Milvus / FAISS
```

---

## 9. 命令模板

### 9.1 API JSONL 主路径测速

```powershell
python test_openalex_cli_10k.py `
  --mode api-jsonl `
  --target 10000 `
  --filter "publication_year:2024,type:article" `
  --output "data/openalex_api_jsonl_speed_10k" `
  --clean-output
```

### 9.2 CLI filter/sample 限量测速

```powershell
Measure-Command {
  openalex download `
    --api-key $env:OPENALEX_API_KEY `
    --output data/openalex_cli_filter_sample_10k_w10 `
    --filter "publication_year:2024,type:article" `
    --sample 10000 `
    --seed 42 `
    --workers 10 `
    --nested `
    --fresh
}
```

### 9.3 查看 sample 参数是否支持

```powershell
openalex download --help | findstr sample
```

### 9.4 查看 API 额度

```powershell
curl "https://api.openalex.org/rate-limit?api_key=$env:OPENALEX_API_KEY"
```

---

## 10. 后续实现建议

### 10.1 新增 `openalex_api_bulk` 模块

建议新增：

```text
src/research_agent/data/openalex_api_bulk_source.py
```

核心能力：

```text
- 支持 filter
- 支持 select 字段
- 支持 cursor paging
- 支持 tqdm
- 支持 retry / exponential backoff
- 支持写入 JSONL
- 支持按 batch 转 Parquet
- 支持断点续传 cursor checkpoint
```

### 10.2 保留 OpenAlex CLI provider

建议保留：

```text
src/research_agent/data/openalex_cli_source.py
```

但定位为辅助：

```text
- CLI filter/sample 验证
- 小规模官方 CLI 下载
- PDF/XML 下载
- Linux/WSL/服务器批处理
```

### 10.3 BFS 作为 Scout，而不是主爬虫

现有 BFS 的最佳定位：

```text
从某个 seed 或 query 做探索
生成领域边界与下载计划
而不是直接承担几十万 / 百万级数据下载
```

输出文件建议：

```json
{
  "query": "deep learning",
  "seed_work_ids": ["W..."],
  "selected_topic_ids": ["T...", "T..."],
  "year_range": [2012, 2026],
  "recommended_filter": "topics.id:Txxx|Tyyy,publication_year:2012-2026,type:article",
  "rationale": {
    "top_topics": [],
    "top_sources": [],
    "bfs_work_count": 0
  }
}
```

---

## 11. 最终结论

本次调研得到的核心结论是：

```text
1. API cursor + JSONL 是当前最适合 BigData_Sci 的 metadata 主采集方案。
2. OpenAlex CLI filter/sample 可以正常工作，但速度较慢，且会产生大量小文件。
3. OpenAlex CLI stdin/IDs 模式不适合大规模 metadata 下载。
4. BFS 应定位为 scout，用于生成 topic/year/source/download_plan，而不是主下载器。
5. 后续深度学习领域文献图谱建议采用：BFS scout → API bulk JSONL/Parquet → Cleaner → MySQL/Neo4j。
```

推荐最终架构：

```text
BFS Scout
    ↓
download_plan.json
    ↓
OpenAlex API Bulk JSONL / Parquet
    ↓
Cleaner + MySQL
    ↓
Neo4j 图数据库
    ↓
GDS / PageRank / Community / Bridge Score / Topic Evolution
```

---

## 12. 参考资料

- OpenAlex CLI 官方文档：`https://developers.openalex.org/download/openalex-cli`
- OpenAlex Download Overview：`https://developers.openalex.org/download/overview`
- OpenAlex API 分页文档：`https://developers.openalex.org/guides/page-through-results`
- OpenAlex Works API：`https://developers.openalex.org/api-reference/works/list-works`
- OpenAlex Authentication & Pricing：`https://developers.openalex.org/api-reference/authentication`
- OpenAlex 官方 CLI GitHub：`https://github.com/ourresearch/openalex-official`
