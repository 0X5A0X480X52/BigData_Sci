# OpenAlex API 预算估算与批次化 BFS 设计文档

> 适用项目：`BigData_Sci` / 文献调研 Agent / OpenAlex metadata 图谱构建  
> 目标：将 OpenAlex 的 API 预算、metadata 获取路径、Batch BFS 探索策略整理为可落地的工程设计方案。

---

## 1. 背景与核心结论

当前项目希望基于 OpenAlex 构建某一领域，尤其是深度学习方向的文献 metadata 数据集，并进一步同步到 MySQL / Neo4j，用于 PageRank、社区发现、桥接论文识别、主题演化等图算法分析。

经过前期测试和方案梳理，推荐将 OpenAlex 数据获取拆成三层：

```text
Batch BFS Scout
    小规模探索，从 seed query / seed work 出发识别领域边界
        ↓
OpenAlex API Bulk
    使用 filter + cursor + select 批量获取 metadata JSONL / Parquet
        ↓
Graph Build
    清洗后写入 MySQL / Neo4j，运行图算法和调研 Agent
```

核心判断：

```text
1. BFS 不应承担“大规模全量下载”任务。
2. BFS 更适合作为“领域侦察器 / scout”，用于生成下载计划。
3. 大规模 metadata 获取应优先使用 OpenAlex API cursor + JSONL/Parquet。
4. OpenAlex CLI filter/sample 可作为辅助路径，不建议用 stdin/IDs 模式做大规模 metadata 下载。
5. OpenAlex API 费用通常不是 metadata 项目的主要瓶颈，主要瓶颈是限流、存储、清洗、图数据库导入和 GDS 内存。
```

---

## 2. OpenAlex API 预算规则

根据 OpenAlex 当前开发者说明，免费计划提供：

```text
$1.00 / day included API budget
10,000 credits = $1
```

典型计费规则：

| 操作类型 | 单次成本 | 说明 |
|---|---:|---|
| 获取单条记录，如单篇 work / author / source | 免费 | 适合少量补全 |
| List + filter | 1 credit | 批量 metadata 获取主路径 |
| Search | 10 credits | 比 filter 贵 10 倍，适合 scout 阶段 |
| Facet | 1 credit | 可用于统计分布 |
| PDF / XML 内容下载 | 另算 | 第一版不建议纳入 |

免费额度大致可支持：

```text
1,000 次 search
10,000 次 filtered list
约 100k–1M exported results，取决于是 search 还是 filter
```

其中，批量 metadata 最关键的是：**每页最多 100 条结果**。因此如果使用 filter + cursor，每 100 条 metadata 大约消耗 1 credit。

---

## 3. Metadata 获取预算估算

### 3.1 使用 filter + cursor 的预算

如果采用：

```text
/works?filter=topics.id:Txxxx,publication_year:2012-2026,type:article
&select=...
&per_page=100
&cursor=*
```

则预算大致如下：

| 目标 metadata 数量 | API 请求数 | Credits | 折算美元 | 免费额度 |
|---:|---:|---:|---:|---|
| 10,000 条 | 100 次 | 100 | $0.01 | 足够 |
| 100,000 条 | 1,000 次 | 1,000 | $0.10 | 足够 |
| 1,000,000 条 | 10,000 次 | 10,000 | $1.00 | 约等于一天免费额度 |
| 5,000,000 条 | 50,000 次 | 50,000 | $5.00 | 可分多天或付费 |
| 10,000,000 条 | 100,000 次 | 100,000 | $10.00 | 需付费或分多天 |

结论：

```text
10万级 metadata：预算约 $0.10
100万级 metadata：预算约 $1.00
500万级 metadata：预算约 $5.00
千万级 metadata：预算约 $10.00
```

对于“深度学习领域核心 metadata”而言，API 预算通常不是核心瓶颈。

---

### 3.2 使用 search 的预算

如果使用：

```text
/works?search=deep learning
```

每页是 10 credits，而不是 1 credit。因此预算大约是 filter 的 10 倍：

| 目标 metadata 数量 | Search 请求数 | Credits | 折算美元 |
|---:|---:|---:|---:|
| 10,000 条 | 100 次 | 1,000 | $0.10 |
| 100,000 条 | 1,000 次 | 10,000 | $1.00 |
| 1,000,000 条 | 10,000 次 | 100,000 | $10.00 |

所以推荐策略是：

```text
Scout 阶段：
    可以用 search 找 seed works。

Bulk 阶段：
    尽量转成 filter，例如 topics.id / publication_year / type。
```

---

## 4. 前期测速结果总结

在 Windows + Anaconda 环境下，针对 `publication_year:2024,type:article` 做了 10,000 条 metadata 测试。

### 4.1 API JSONL 路径

命令形式：

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
Output JSONL  : metadata_10000.jsonl
```

结论：

```text
API cursor + JSONL 稳定、速度较快，适合作为 metadata 主采集路径。
```

---

### 4.2 OpenAlex CLI filter + sample 路径

命令形式：

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
Downloaded: 10.0K files
Size      : 215.5 MB
Duration  : 739.6s / 12.34min
Average   : 298.4 KB/s
Failed    : 0 files
```

换算：

```text
10000 / 740.43 ≈ 13.5 files/s
215.5 MB / 10000 ≈ 21.55 KB/file
```

结论：

```text
OpenAlex CLI filter + sample 可正常使用，但比 API JSONL 慢。
主要原因是 CLI 保存为一篇一个 JSON 文件，并且逐篇请求和写入。
```

---

### 4.3 OpenAlex CLI stdin / IDs 路径

测试流程：

```text
API collect IDs
    ↓
openalex download --stdin
    ↓
逐篇下载 metadata JSON
```

观察结果：

```text
Collecting Work IDs: 10000 IDs / 106s
CLI metadata JSON: 约 1 file/s 左右
```

结论：

```text
CLI stdin / IDs 模式不适合大规模 metadata 获取。
它更适合少量 ID 补全，不适合 10万 / 100万级 corpus 构建。
```

---

## 5. 推荐数据获取路径

### 5.1 项目主路径：API Bulk JSONL / Parquet

推荐：

```text
OpenAlex API cursor
    filter + select + per_page=100
        ↓
metadata.jsonl
        ↓
Parquet / DuckDB
        ↓
BatchCleaner
        ↓
MySQL / Neo4j
```

优势：

```text
1. 单文件 JSONL，适合后续转 Parquet。
2. API 请求数可控。
3. 避免大量小 JSON 文件。
4. 比 CLI filter/sample 更快。
5. 更适合接入项目内的清洗和图构建流程。
```

推荐 API 参数：

```text
filter=topics.id:Txxxx,publication_year:2012-2026,type:article
select=id,doi,title,publication_year,publication_date,type,cited_by_count,authorships,primary_location,topics,referenced_works,open_access
per_page=100
cursor=*
```

---

### 5.2 辅助路径：OpenAlex CLI filter / sample

适合：

```text
1. 快速验证官方 CLI。
2. 小规模 sample 下载。
3. 未来 PDF / XML / full-text 采集。
4. Linux / WSL / 服务器环境批处理。
```

示例：

```powershell
openalex download `
  --api-key $env:OPENALEX_API_KEY `
  --output data/openalex_cli_filter_sample_10k_w10 `
  --filter "publication_year:2024,type:article" `
  --sample 10000 `
  --seed 42 `
  --workers 10 `
  --nested `
  --fresh
```

注意：

```text
1. --sample 最大 10000。
2. --sample 是随机抽样，不是前 10000 条。
3. 高 workers 容易触发 429。
4. Windows 下大量小文件写入较慢。
5. 不建议 CLI stdin/IDs 做大规模 metadata。
```

---

## 6. Batch BFS 的定位

Batch BFS 不应定位为全量爬虫，而应定位为：

```text
领域侦察器 / Scout
```

职责：

```text
1. 从 seed query / seed work 出发探索局部文献邻域。
2. 识别高频 topic、source、year、author、institution。
3. 识别核心论文、桥接论文、引用路径。
4. 生成后续 bulk metadata 下载计划。
5. 输出可追溯的 openalex_download_plan.json。
```

完整流程：

```text
Seed Query / Seed Work
        ↓
Batch BFS Scout
        ↓
局部图与领域画像
        ↓
openalex_download_plan.json
        ↓
API Bulk JSONL / Parquet
        ↓
MySQL / Neo4j / GDS
```

---

## 7. 为什么 BFS 要按批次做

逐篇递归式 BFS 不适合 OpenAlex 场景，因为容易出现：

```text
1. API 请求过碎。
2. 去重困难。
3. frontier 爆炸。
4. 无法清晰控制预算。
5. 中断恢复困难。
6. 难以统计每层分布。
```

批次化 BFS 的优势：

```text
1. 每层统一去重。
2. 每层统一排序和剪枝。
3. 每批可落盘，支持 checkpoint。
4. 可按层统计 topic/source/year 分布。
5. 便于生成下载计划。
6. 便于估算 API 预算和数据规模。
```

---

## 8. Batch BFS 推荐结构

### 8.1 层级流程

```text
depth = 0:
    seed query 搜索得到 seed works

depth = 1:
    批量扩展 seed works 的 references / citing works

depth = 2:
    扩展上一层筛选后的 high-score works

每层结束后：
    去重
    打分
    截断 frontier
    保存 checkpoint
    更新局部图
    更新统计结果
```

---

### 8.2 伪代码

```python
frontier = seed_works
visited = set()
all_edges = []

for depth in range(max_depth + 1):
    # 1. 去重
    frontier = [w for w in frontier if w.id not in visited]
    for w in frontier:
        visited.add(w.id)

    # 2. 保存本层 works
    save_layer_works(depth, frontier)

    # 3. 统计领域画像
    layer_stats = analyze_layer(frontier)
    save_layer_stats(depth, layer_stats)

    # 4. 达到最大深度则停止
    if depth >= max_depth:
        break

    # 5. 批量扩展邻居
    next_candidates = []
    for batch in chunk(frontier, batch_size):
        refs = fetch_references_batch(batch)
        cites = fetch_citing_batch(batch)
        next_candidates.extend(refs)
        next_candidates.extend(cites)

    # 6. 对候选节点打分与剪枝
    frontier = rank_and_prune(
        next_candidates,
        query=query,
        top_k=max_frontier_per_depth,
    )
```

---

## 9. Batch BFS 参数设计

推荐新增 CLI 参数：

```bash
--bfs-batch-size 50
--max-depth 2
--max-frontier-per-depth 1000
--max-reference-fanout 50
--max-citing-fanout 50
--min-topic-score 0.3
--checkpoint-dir artifacts/openalex_bfs
--resume
```

参数解释：

| 参数 | 建议值 | 作用 |
|---|---:|---|
| `--bfs-batch-size` | 20–100 | 每批处理多少个 frontier works |
| `--max-depth` | 1–2 | BFS 最大深度 |
| `--max-frontier-per-depth` | 500–5000 | 每层最多保留多少节点继续扩展 |
| `--max-reference-fanout` | 20–100 | 每篇最多取多少条 references |
| `--max-citing-fanout` | 20–100 | 每篇最多取多少条 citing works |
| `--min-topic-score` | 0.2–0.5 | topic 相关性阈值 |
| `--resume` | - | 从 checkpoint 继续 |

---

## 10. 每层剪枝策略

如果不剪枝，BFS 会迅速爆炸：

```text
depth 0: 10 篇 seed
depth 1: 10 × 100 = 1,000 篇
depth 2: 1,000 × 100 = 100,000 篇
depth 3: 100,000 × 100 = 10,000,000 篇
```

因此每层必须做 scoring 和 pruning。

推荐综合分：

```text
score =
    topic_relevance
  + title_relevance
  + citation_score
  + year_weight
  + source_weight
  + bridge_score
  - noise_penalty
```

示例权重：

```python
score = (
    0.35 * topic_score
    + 0.25 * title_keyword_score
    + 0.20 * log_citation_score
    + 0.10 * recent_year_score
    + 0.10 * source_score
)
```

可以按任务调整：

| 任务 | 权重倾向 |
|---|---|
| 找经典基础论文 | 提高 citation_score |
| 找最新趋势 | 提高 recent_year_score |
| 找跨领域桥接论文 | 提高 bridge_score |
| 构建领域核心 corpus | 提高 topic_score |
| 找高质量论文 | 加 source_score / venue_score |

---

## 11. Batch BFS 输出格式

建议输出目录：

```text
artifacts/openalex_bfs/
├── bfs_config.json
├── seed_works.jsonl
├── layer_0_works.jsonl
├── layer_1_works.jsonl
├── layer_2_works.jsonl
├── edges_cites.jsonl
├── topics_stats.json
├── sources_stats.json
├── years_stats.json
├── frontier_scores.jsonl
├── checkpoint.json
└── openalex_download_plan.json
```

其中最关键的是：

```text
openalex_download_plan.json
```

示例：

```json
{
  "query": "deep learning",
  "selected_topic_ids": ["Txxxx", "Tyyyy", "Tzzzz"],
  "year_range": [2012, 2026],
  "type_filter": ["article", "preprint"],
  "recommended_filter": "topics.id:Txxxx|Tyyyy|Tzzzz,publication_year:2012-2026,type:article",
  "seed_work_ids": ["W123", "W456"],
  "bfs_depth": 2,
  "bfs_work_count": 3812,
  "rationale": {
    "top_topics": [
      {"id": "Txxxx", "name": "Deep Learning", "count": 320}
    ],
    "top_sources": [],
    "top_bridge_works": []
  }
}
```

---

## 12. 推荐项目模块划分

建议在 `BigData_Sci` 中形成如下数据获取 provider：

```text
provider=fixture
    离线 demo 数据

provider=openalex
    API seed search + Batch BFS Scout

provider=openalex_api_bulk
    API cursor + filter + JSONL/Parquet，主采集路径

provider=openalex_cli_filter
    官方 CLI filter/sample 下载，辅助路径

provider=local_jsonl
    从已下载 JSONL/Parquet 导入
```

对应流程：

```bash
# 1. Scout BFS
python scripts/openalex_elt_cli.py "deep learning" \
  --provider openalex \
  --mode bfs-scout \
  --max-depth 2 \
  --bfs-batch-size 50 \
  --max-frontier-per-depth 1000 \
  --max-reference-fanout 50 \
  --max-citing-fanout 50 \
  --write-download-plan artifacts/deep_learning_plan.json

# 2. Bulk metadata fetch
python scripts/openalex_elt_cli.py "deep learning" \
  --provider openalex_api_bulk \
  --download-plan artifacts/deep_learning_plan.json \
  --output data/openalex_deep_learning/metadata.jsonl

# 3. Local import + graph sync
python scripts/openalex_elt_cli.py "deep learning" \
  --provider local_jsonl \
  --input data/openalex_deep_learning/metadata.jsonl \
  --init-schema \
  --sync-neo4j
```

---

## 13. 深度学习领域推荐实施路线

### 阶段 1：小规模 scout

```text
目标：得到领域边界和下载计划
规模：1,000–10,000 works
预算：约 $0.01–$0.10
输出：openalex_download_plan.json
```

建议：

```text
max_depth=1 或 2
max_frontier_per_depth=500–1000
reference_fanout=50
citing_fanout=50
```

---

### 阶段 2：核心 corpus 下载

```text
目标：下载深度学习核心领域 metadata
规模：100,000–1,000,000 works
预算：约 $0.10–$1.00
路径：API cursor + JSONL/Parquet
```

建议：

```text
filter=topics.id:... + publication_year:2012-2026 + type:article|preprint
select=必要字段
per_page=100
cursor=*
```

---

### 阶段 3：图数据库构建

```text
目标：构建 Work / Author / Institution / Source / Topic / Citation 图
规模：百万级节点边
配置：64GB RAM 起，Neo4j/GDS 更建议 128GB
```

Neo4j 中建议存结构化图关系，完整 JSONL/Parquet 作为冷数据：

```text
Neo4j:
    Work, Author, Institution, Source, Topic, CITES, AUTHORED, HAS_TOPIC

Parquet / DuckDB:
    完整 OpenAlex metadata
```

---

### 阶段 4：1-hop citation 扩展

```text
目标：识别领域外知识来源和扩散路径
规模：2M–5M works
预算：约 $2–$5
风险：图规模和边数量显著上升
```

建议只对：

```text
high_pagerank works
bridge works
representative topic works
```

做定向扩展，而不是全量 1-hop。

---

## 14. 最终推荐

最终建议采用如下架构：

```text
Batch BFS = 领域侦察器
API Bulk = 主 metadata 采集器
OpenAlex CLI = 辅助下载器
Neo4j/GDS = 图算法分析层
```

其中：

```text
1. BFS 做小规模、可解释、可中断的探索。
2. BFS 的目标是生成下载计划，而不是全量爬取。
3. 大规模 metadata 用 API cursor + JSONL/Parquet。
4. CLI filter/sample 可用于抽样验证，不建议 CLI stdin/IDs 做大规模 metadata。
5. search 只在 scout 阶段使用，大规模阶段尽量转 filter。
6. API 预算不是主要瓶颈，工程瓶颈在存储、清洗、入库和图算法内存。
```

一句话总结：

```text
先用 Batch BFS 找准领域边界，再用 API Bulk 高效下载 metadata，最后进入 MySQL/Neo4j 做图分析。
```

---

## 参考资料

- OpenAlex Developers：Authentication & Pricing  
  `https://developers.openalex.org/api-reference/authentication`
- OpenAlex Developers：Page through Results  
  `https://developers.openalex.org/guides/page-through-results`
- OpenAlex Developers：OpenAlex CLI  
  `https://developers.openalex.org/download/openalex-cli`
- OpenAlex Official CLI GitHub  
  `https://github.com/ourresearch/openalex-official`

