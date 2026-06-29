# BigData_Sci：OpenAlex BFS 探索与 OpenAlex CLI 批量采集模式设计文档

## 1. 背景与目标

当前项目 `BigData_Sci` 已经具备基于 OpenAlex 的文献数据获取、清洗、MySQL 入库、Neo4j 同步与图算法分析能力。现有 OpenAlex 获取流程更偏向于从用户输入的研究问题或 seed work 出发，通过 OpenAlex API 进行局部 BFS 扩展，得到一批与主题相关的文献节点及其引用关系。

但如果目标是构建“深度学习领域”这类规模较大的领域文献图谱，单纯依赖 API BFS 并不适合作为大规模数据下载器。原因包括：

1. API 单次列表请求返回数量有限，需要持续分页。
2. BFS 容易被高引用综述、跨领域应用论文带偏，导致领域边界失控。
3. BFS 更适合探索局部文献邻域，而不是稳定、可复现地构建大规模 corpus。
4. 大规模领域数据获取更适合使用 OpenAlex 官方 CLI 的 filter-based bulk download 能力。

因此，本文档建议将当前 OpenAlex BFS 明确定位为：

> **领域侦察器 / scout：从 query 或 seed work 出发，探索局部文献邻域，生成 OpenAlex CLI 批量下载计划。**

OpenAlex CLI 则定位为：

> **工程化批量采集器：根据 BFS 生成的 topic/year/type/source/ids 等启发式输入，批量下载 works metadata，并复用现有清洗、MySQL、Neo4j 与图算法流程。**

---

## 2. 总体架构

推荐形成如下两阶段或三阶段流程：

```text
用户输入研究问题 / seed work
        ↓
OpenAlex API BFS Scout
        ↓
局部领域画像分析
        ↓
生成 OpenAlex CLI Download Plan
        ↓
OpenAlex CLI 批量下载 works metadata
        ↓
BatchCleaner 清洗
        ↓
MySQL 规范化存储
        ↓
Neo4j 图数据库同步
        ↓
GDS / PageRank / Community / Bridge Score / 可视化分析
```

从系统职责上可以拆分为：

| 模块 | 主要职责 | 适合规模 |
|---|---|---:|
| `provider=fixture` | 离线样例数据，用于测试 pipeline | 小规模 |
| `provider=openalex` | API 搜索 + seed BFS，探索局部文献邻域 | 1,000–10,000 works |
| `provider=openalex_cli` | 官方 CLI 批量下载 filtered subset metadata | 10 万–100 万+ works |

---

## 3. 为什么 BFS 更适合作为 Scout

当前 BFS 的价值不在于“直接爬完整领域”，而在于帮助 Agent 快速判断领域边界。

BFS 可以从 seed work 附近提取：

1. 高频 `topics.id` / `primary_topic.id`。
2. 年份分布，例如深度学习相关文献主要集中于 2012 年以后。
3. 高频 source / venue，例如 NeurIPS、ICML、ICLR、CVPR、ACL、EMNLP 等。
4. 高被引文献、桥接文献、综述文献。
5. seed neighborhood 中的核心引用路径。
6. 哪些 topic 应纳入、谨慎纳入或排除。

因此 BFS 的推荐参数不宜过大：

```text
seed_search_limit: 20-100
max_depth: 1
max_reference_fanout: 50-100
max_citing_fanout: 50-100
target_works: 1,000-10,000
```

这样可以让 BFS 保持为低成本探索工具，而不是变成不可控的大规模爬取器。

---

## 4. OpenAlex CLI 的定位

OpenAlex 官方 CLI 适合下载某个过滤条件下的大量 OpenAlex works metadata。它的优势是：

1. 支持按 OpenAlex filter 批量下载。
2. 支持并行下载。
3. 支持 checkpoint / resume。
4. 支持 rate limiting。
5. 支持按 IDs 或 stdin 输入下载指定 works。
6. 默认保存 work metadata JSON。
7. 可选下载 PDF / TEI XML，但第一版不建议开启。

对本项目而言，第一版应只使用 CLI 下载 metadata JSON，不下载 PDF / XML。

推荐命令形态：

```bash
openalex download \
  --api-key YOUR_KEY \
  --output ./data/openalex_cli/deep_learning \
  --filter "topics.id:Txxxxx|Tyyyyy|Tzzzzz,publication_year:2012-2026,type:article" \
  --workers 80 \
  --nested
```

如果 BFS 已经生成了一批高置信 work IDs，也可以使用 stdin 模式：

```bash
cat work_ids.txt | openalex download \
  --api-key YOUR_KEY \
  --output ./data/openalex_cli/seed_neighborhood \
  --stdin \
  --nested
```

---

## 5. 推荐新增运行模式

建议将项目的数据获取模式扩展为：

```text
provider=fixture
    离线 demo 数据源。

provider=openalex
    使用 OpenAlex API 搜索 seed，并进行 BFS 局部探索。

provider=openalex_cli
    调用官方 OpenAlex CLI 批量下载 works metadata，并复用现有清洗入库流程。
```

进一步可以引入 agent mode：

```text
agent_mode=scout
    使用 OpenAlex API BFS 进行领域侦察，输出 download_plan.json。

agent_mode=bulk_build
    读取 download_plan.json，调用 OpenAlex CLI 下载并入库。
```

---

## 6. BFS 输出给 CLI 的启发式输入

### 6.1 Topic Filter

这是最重要的输入。BFS 对局部文献中的 topics 进行统计，选出高频且与 query 语义一致的 topics。

示例输出：

```json
{
  "selected_topic_ids": ["Txxxxx", "Tyyyyy", "Tzzzzz"],
  "recommended_filter": "topics.id:Txxxxx|Tyyyyy|Tzzzzz,publication_year:2012-2026,type:article"
}
```

需要注意的是，高频 topic 不一定都应纳入，应分为三类：

```text
must_include_topics:
    高频且与研究问题高度一致。

candidate_topics:
    高频但较泛化，需要人工或规则确认。

exclude_topics:
    高频但偏离主题，可能是应用领域或噪声领域。
```

以“深度学习”为例：

```text
建议保留：
- Deep Learning
- Neural Networks
- Representation Learning
- Transformers
- Graph Neural Networks
- Computer Vision
- Natural Language Processing
- Reinforcement Learning

谨慎纳入：
- Artificial Intelligence
- Machine Learning
- Data Mining

建议排除或单独分支：
- Medical Imaging
- Remote Sensing
- Bioinformatics
- Finance
```

### 6.2 Year Range

BFS 可以根据局部文献年份分布生成下载范围。

深度学习领域推荐初始范围：

```text
publication_year:2012-2026
```

如果目标是更关注 Transformer、LLM、Diffusion、Agent 等新近方向，可以收窄为：

```text
publication_year:2017-2026
```

### 6.3 Source / Venue Filter

BFS 可以统计高频来源，例如：

```text
NeurIPS
ICML
ICLR
CVPR
ACL
EMNLP
KDD
AAAI
IJCAI
JMLR
TPAMI
```

但第一版不建议过度依赖 source filter。原因是：

1. OpenAlex 对会议、期刊、repository 的 source 归属可能存在不一致。
2. source 过滤可能漏掉 arXiv、workshop、跨学科期刊中的关键论文。
3. 深度学习领域的关键文献经常跨 source 分布。

因此推荐第一版以：

```text
topic + year + type
```

作为主过滤条件，source 只作为可选二级过滤或分析字段。

### 6.4 IDs / stdin 输入

如果 BFS 得到一批高置信文献，可以输出：

```text
work_ids.txt
```

然后交给 OpenAlex CLI 精确下载：

```bash
cat work_ids.txt | openalex download \
  --api-key YOUR_KEY \
  --output ./data/openalex_cli/seed_neighborhood \
  --stdin \
  --nested
```

该方式适合复现 BFS 结果，但不适合扩展领域覆盖。

---

## 7. Download Plan 文件设计

建议新增一个可追溯的下载计划文件，例如：

```text
artifacts/openalex_cli_plan.json
```

推荐结构：

```json
{
  "query": "deep learning",
  "seed_work_ids": ["W123", "W456"],
  "selected_topic_ids": ["Txxxxx", "Tyyyyy", "Tzzzzz"],
  "year_range": [2012, 2026],
  "type_filter": ["article", "preprint"],
  "recommended_filter": "topics.id:Txxxxx|Tyyyyy|Tzzzzz,publication_year:2012-2026,type:article",
  "ids_file": "artifacts/work_ids.txt",
  "rationale": {
    "bfs_work_count": 3812,
    "seed_count": 42,
    "top_topics": [
      {"id": "Txxxxx", "name": "Deep Learning", "count": 320},
      {"id": "Tyyyyy", "name": "Neural Networks", "count": 210}
    ],
    "top_sources": [
      {"id": "Sxxxxx", "name": "NeurIPS", "count": 120}
    ],
    "year_distribution": {
      "2017": 120,
      "2018": 180,
      "2019": 260,
      "2020": 310,
      "2021": 420,
      "2022": 500,
      "2023": 620,
      "2024": 700
    }
  }
}
```

这个文件的价值是：

1. 让 Agent 的探索过程可追溯。
2. 让下载参数可复现。
3. 方便人工审核 topic 边界。
4. 便于后续比较不同策略生成的 corpus 差异。

---

## 8. 代码改造建议

### 8.1 新增依赖

建议在 `pyproject.toml` 中扩展 optional dependency：

```toml
[project.optional-dependencies]
openalex = [
    "pyalex>=0.15",
    "openalex-official>=0.3.3",
]
```

或者先在文档中要求用户手动安装：

```bash
pip install openalex-official
```

### 8.2 新增文件：`openalex_cli_source.py`

建议新增：

```text
src/research_agent/data/openalex_cli_source.py
```

职责：

1. 调用 `openalex download`。
2. 管理输出目录。
3. 支持跳过下载，直接读取已有 JSON。
4. 遍历下载目录下的 works metadata JSON。
5. 将 JSON 转成现有 cleaner 可处理的 raw works。

核心类设计：

```python
class OpenAlexCliSource:
    def __init__(
        self,
        api_key: str,
        output_dir: str,
        workers: int = 50,
        nested: bool = True,
        content: str = "",
    ) -> None:
        ...

    def download_by_filter(
        self,
        filter_expr: str,
        *,
        fresh: bool = False,
        quiet: bool = False,
    ) -> None:
        ...

    def iter_metadata_files(self) -> Iterable[Path]:
        ...

    def load_raw_works(self, limit: int = 0) -> list[dict]:
        ...
```

第一版可以一次性加载 JSON；后续大规模数据建议改为分批流式处理。

### 8.3 新增文件：`openalex_cli_planner.py`

建议新增：

```text
src/research_agent/data/openalex_cli_planner.py
```

职责：

1. 接收 BFS 得到的 `raw_works`。
2. 统计 topic、year、source、citation hub。
3. 生成 `download_plan.json`。
4. 生成 `work_ids.txt`。
5. 生成 OpenAlex CLI 可直接使用的 filter string。

核心类设计：

```python
class OpenAlexCliPlanBuilder:
    def build_from_bfs(
        self,
        raw_works: list[dict],
        query: str,
        top_k_topics: int = 10,
        min_topic_count: int = 5,
    ) -> dict:
        ...
```

核心伪代码：

```python
from collections import Counter


def build_cli_plan(raw_works, query, min_topic_count=5, top_k_topics=10):
    topic_counter = Counter()
    year_counter = Counter()
    source_counter = Counter()

    for w in raw_works:
        year = w.get("publication_year")
        if year:
            year_counter[year] += 1

        source = (w.get("primary_location") or {}).get("source") or {}
        if source.get("id"):
            source_counter[source["id"]] += 1

        for t in w.get("topics") or []:
            topic_id = t.get("id")
            score = t.get("score", 1.0)
            if topic_id:
                topic_counter[topic_id] += score

    selected_topics = [
        topic_id
        for topic_id, score in topic_counter.most_common(top_k_topics)
        if score >= min_topic_count
    ]

    start_year = max(2012, min(year_counter) if year_counter else 2012)
    end_year = max(year_counter) if year_counter else 2026

    filter_expr = (
        f"topics.id:{'|'.join(selected_topics)},"
        f"publication_year:{start_year}-{end_year},"
        f"type:article"
    )

    return {
        "query": query,
        "selected_topic_ids": selected_topics,
        "year_range": [start_year, end_year],
        "recommended_filter": filter_expr,
        "top_topics": topic_counter.most_common(20),
        "top_sources": source_counter.most_common(20),
    }
```

---

## 9. `scripts/openalex_elt_cli.py` 参数扩展建议

### 9.1 扩展 provider choices

```python
parser.add_argument(
    "--provider",
    choices=["openalex", "fixture", "openalex_cli"],
    default="openalex",
    help="Data provider. Use openalex_cli for official CLI bulk metadata download.",
)
```

### 9.2 新增 OpenAlex CLI 参数

```python
parser.add_argument(
    "--openalex-api-key",
    default=os.getenv("OPENALEX_API_KEY", ""),
    help="OpenAlex API key for official CLI mode.",
)

parser.add_argument(
    "--openalex-cli-filter",
    default="",
    help="OpenAlex filter string for official CLI mode.",
)

parser.add_argument(
    "--openalex-cli-plan",
    default="",
    help="Path to download_plan.json generated by BFS scout mode.",
)

parser.add_argument(
    "--openalex-cli-output",
    default="data/openalex_cli_downloads",
    help="Output directory used by `openalex download`.",
)

parser.add_argument(
    "--openalex-cli-workers",
    type=int,
    default=50,
    help="Concurrent workers for official OpenAlex CLI.",
)

parser.add_argument(
    "--openalex-cli-content",
    default="",
    choices=["", "pdf", "xml", "pdf,xml"],
    help="Optional content download. Empty means metadata only.",
)

parser.add_argument(
    "--openalex-cli-fresh",
    action="store_true",
    help="Ignore existing OpenAlex CLI checkpoint and start fresh.",
)

parser.add_argument(
    "--openalex-cli-skip-download",
    action="store_true",
    help="Skip `openalex download` and only ingest existing JSON files.",
)

parser.add_argument(
    "--openalex-cli-ingest-limit",
    type=int,
    default=0,
    help="Max number of downloaded JSON works to ingest. 0 means no limit.",
)

parser.add_argument(
    "--write-openalex-cli-plan",
    default="",
    help="In openalex BFS mode, write a recommended OpenAlex CLI download plan.",
)
```

---

## 10. 运行示例

### 10.1 BFS Scout：从 query 探索并生成下载计划

```powershell
python scripts/openalex_elt_cli.py "deep learning" `
  --provider openalex `
  --max-depth 1 `
  --max-citing-fanout 80 `
  --max-reference-fanout 80 `
  --seed-search-limit 50 `
  --write-openalex-cli-plan artifacts/deep_learning_cli_plan.json `
  --no-sync-neo4j
```

输出：

```text
artifacts/deep_learning_cli_plan.json
artifacts/deep_learning_work_ids.txt
```

### 10.2 人工检查计划

检查：

```text
selected_topic_ids
recommended_filter
year_range
top_topics
top_sources
exclude_candidates
```

如发现 topic 过宽或偏题，应人工修改 `download_plan.json`。

### 10.3 使用 OpenAlex CLI 批量下载并入库

```powershell
python scripts/openalex_elt_cli.py "deep learning" `
  --provider openalex_cli `
  --openalex-api-key $env:OPENALEX_API_KEY `
  --openalex-cli-plan artifacts/deep_learning_cli_plan.json `
  --openalex-cli-output data/openalex_cli/deep_learning `
  --openalex-cli-workers 80 `
  --init-schema `
  --mysql-host localhost `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent `
  --sync-neo4j `
  --neo4j-user neo4j `
  --neo4j-password <password> `
  --neo4j-database neo4j
```

### 10.4 已经下载过，只重新导入

```powershell
python scripts/openalex_elt_cli.py "deep learning" `
  --provider openalex_cli `
  --openalex-cli-output data/openalex_cli/deep_learning `
  --openalex-cli-skip-download `
  --openalex-cli-ingest-limit 10000 `
  --mysql-host localhost `
  --mysql-user root `
  --mysql-password <password> `
  --mysql-database research_agent
```

---

## 11. 数据规模建议

对于“深度学习领域”图谱，建议不要一开始追求完整覆盖，而是分阶段扩大：

| 阶段 | 目标 | Work 数量 | 建议用途 |
|---|---|---:|---|
| Pilot | 验证 pipeline | 1,000–10,000 | 检查字段、清洗、Neo4j schema |
| Core | 构建核心领域图 | 100,000–500,000 | PageRank、社区发现、主题演化 |
| Extended | 加入更多相关 topic | 500,000–1,000,000 | 跨子领域扩散分析 |
| 1-hop | 加入核心论文一阶引用扩展 | 1,000,000+ | 知识流动、范式转移分析 |

硬件建议：

```text
10万 works：32GB RAM + 200GB SSD
50万-100万 works：64GB RAM + 500GB-1TB SSD
1-hop 扩展：128GB RAM + 1TB-2TB SSD
```

---

## 12. Neo4j 建模建议

建议第一版只将适合图查询的实体关系写入 Neo4j：

```text
(:Work {id, doi, title, year, cited_by_count})
(:Author {id, name, orcid})
(:Institution {id, name, country_code})
(:Source {id, name, type, issn_l})
(:Topic {id, name})
```

关系：

```text
(:Author)-[:AUTHORED {position, is_corresponding}]->(:Work)
(:Author)-[:AFFILIATED_WITH]->(:Institution)
(:Work)-[:PUBLISHED_IN]->(:Source)
(:Work)-[:HAS_TOPIC {score}]->(:Topic)
(:Work)-[:CITES]->(:Work)
```

更严谨的 authorship 建模方式：

```text
(:Work)-[:HAS_AUTHORSHIP]->(:Authorship)
(:Authorship)-[:AUTHOR]->(:Author)
(:Authorship)-[:AFFILIATED_WITH]->(:Institution)
```

这样可以保存作者顺序、通讯作者、论文发表时机构等信息。

---

## 13. 图算法设计

建议分别构建不同的图投影：

### 13.1 Citation Graph

```text
(:Work)-[:CITES]->(:Work)
```

适合算法：

```text
PageRank
HITS
Louvain / Leiden
Weakly Connected Components
Citation backbone extraction
```

用途：

```text
识别核心论文
识别知识流动路径
识别范式转移节点
识别不同子领域的引用社区
```

### 13.2 Co-author Graph

```text
(:Author)-[:CO_AUTHOR]->(:Author)
```

或由二部图投影得到：

```text
(:Author)-[:AUTHORED]->(:Work)<-[:AUTHORED]-(:Author)
```

用途：

```text
研究合作网络
识别核心研究团队
识别机构间合作结构
```

### 13.3 Topic-Work Bipartite Graph

```text
(:Work)-[:HAS_TOPIC]->(:Topic)
```

用途：

```text
主题共现
主题演化
领域边界识别
topic drift 分析
```

---

## 14. 风险与限制

### 14.1 BFS 容易被高引用论文带偏

解决方案：

```text
限制 fanout
控制 depth
区分 must_include / candidate / exclude topic
对高引用综述降低权重
```

### 14.2 Topic 过滤可能过宽

解决方案：

```text
先生成 plan，再人工或规则审核
不要盲目纳入所有高频 topic
必要时按子领域拆分多个 corpus
```

### 14.3 Source 过滤可能漏数据

解决方案：

```text
第一版不要强依赖 source filter
topic + year + type 作为主过滤
source 作为分析字段或二级筛选字段
```

### 14.4 大规模 JSON 一次性加载会占内存

解决方案：

```text
第一版可一次性加载 1万-10万 works
后续改为每 5000 或 10000 篇分批清洗入库
```

### 14.5 PDF / TEI XML 不适合第一版

解决方案：

```text
第一版只下载 metadata JSON
全文下载单独作为 RAG / 全文挖掘模块
```

---

## 15. 推荐迭代路线

### V1：最小可用版本

目标：增加 `provider=openalex_cli`。

内容：

```text
新增 openalex_cli_source.py
支持 --openalex-cli-filter
支持 --openalex-cli-output
支持 --openalex-cli-skip-download
复用现有 cleaner / MySQL / Neo4j 流程
```

### V2：BFS 生成下载计划

目标：让 BFS 成为 scout。

内容：

```text
新增 openalex_cli_planner.py
支持 --write-openalex-cli-plan
输出 download_plan.json
输出 work_ids.txt
```

### V3：Plan 驱动 CLI 下载

目标：让 `provider=openalex_cli` 可以直接读取计划。

内容：

```text
支持 --openalex-cli-plan
从 plan 中读取 recommended_filter
自动生成 openalex download 命令
记录下载日志和版本信息
```

### V4：流式清洗入库

目标：支持 50万-100万 works。

内容：

```text
iter_raw_works()
clean_and_insert_streaming()
按 batch 提交 MySQL
按 batch 写 Neo4j CSV 或同步 Neo4j
```

### V5：Agent 化闭环

目标：自动探索、生成计划、下载、评估 corpus 质量。

内容：

```text
Agent 运行 BFS scout
生成多个候选 download plan
评估 topic purity / citation density / year coverage
选择最优 plan
调用 openalex_cli bulk build
运行图算法并生成分析报告
```

---

## 16. 最终结论

当前 OpenAlex BFS 不应定位为大规模领域数据下载器，而应定位为：

```text
从 seed 出发的局部探索器 / 领域侦察器 / OpenAlex CLI 下载计划生成器
```

OpenAlex CLI 则应定位为：

```text
根据 BFS 生成的启发式 filter 或 ids，批量下载 works metadata 的工程化采集器
```

这种分工的优势是：

1. BFS 保持轻量、可解释、可控。
2. CLI 负责高吞吐、可恢复、可复现的批量采集。
3. 下载计划可以被人工审核和版本化。
4. 现有 BatchCleaner、MySQLInserter、Neo4jGraphSync 可以复用。
5. 后续易于扩展为 Agent 自动调研系统。

一句话概括：

> **BFS 做侦察兵，OpenAlex CLI 做批量采集器，MySQL/Neo4j 做结构化知识底座，图算法和 Agent 做领域规律发现。**

---

## 17. 参考资料

1. OpenAlex Developers：OpenAlex CLI 文档。  
   https://developers.openalex.org/download/openalex-cli

2. OpenAlex Developers：下载方式总览。  
   https://developers.openalex.org/download/overview

3. OpenAlex Developers：API 分页与 cursor paging。  
   https://developers.openalex.org/guides/page-through-results

4. OpenAlex Developers：认证、限制与 per_page 约束。  
   https://developers.openalex.org/api-reference/authentication

5. OpenAlex Official CLI GitHub 仓库。  
   https://github.com/ourresearch/openalex-official
