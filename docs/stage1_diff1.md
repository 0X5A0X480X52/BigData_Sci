# 自动调研 Agent 第一阶段差距分析与实现计划

## 1. 调研范围

本文对照 `docs/agent_plan_v1.pdf` 中的第一阶段目标与当前 `src/research_agent` 实现，整理已经完成的能力、与原计划的差距、未完成任务和后续实现路线。

本次调研覆盖：

- 第一阶段计划：自动构建领域文献集合、图结构分析、关键论文识别、少量 PDF Parent-Child RAG、可追溯 Artifact 和问答式工作台。
- 当前实现：`src/research_agent`、`scripts/run_research_agent_demo.py`、`tests/*`、已生成的 `outputs/ra_demo_artifacts/*` demo artifacts。
- 参考原型：`demos/agent_min_demo`、`demos/embedding_demo`、`demos/openalex_python_elt_demo/module_ELT`、`demos/openalex_python_elt_demo/module_data_sync`。

结论：当前实现已经形成一个可离线运行的 MVP scaffold，但离原计划中的生产级 P0 闭环仍有明显差距。现阶段更接近“协议、骨架、离线演示和测试样例已搭好”，还不是“真实 OpenAlex + 持久化 + Neo4j/Qdrant + PDF 证据 + MCP stdio + 完整评测”的交付形态。

## 2. 当前实现状态

### 2.1 核心协议与 Artifact

已实现：

- `RunConfig`：包含 corpus、graph、key paper、PDF、chunk、tool call 等上限。
- `FeatureFlags`：包含 `storm_perspective_skill`、`paperqa2_synthesis`、`gpt_researcher_mcp` 等开关字段。
- `MCPResult`：定义了工具调用结果的统一协议。
- `ArtifactStore`：支持本地文件系统写入 JSON、JSONL、CSV、文本。
- `EvidenceBundle`、`EvidenceRecord`、`ParentChunk`、`ChildChunk`：已经具备证据包和 Parent-Child 数据结构。
- demo run 会产出 `plan.json`、`trace.json`、`run.json`、`field_guide.md`、corpus、graph、evidence artifacts。

主要限制：

- `MCPResult` 虽已定义，但 runtime 没有把各工具调用结果统一收集进 `ResearchRun.results`。
- Artifact 只有本地文件系统版本，没有 ObjectStorage 抽象层、内容寻址、版本管理或长期索引。
- 缺少 schema 校验、artifact manifest、artifact 与 tool call 的强关联。

### 2.2 Scholarly Data

已实现：

- `FixtureOpenAlexClient`：提供离线、确定性的 OpenAlex-like 数据。
- `HttpOpenAlexClient`：有一个基于 `urllib` 的极简 OpenAlex HTTP client。
- `ScholarlyDataService`：支持 `create_field_corpus`、`create_seed_lineage_corpus`、`expand_references`、`expand_citing_works`、`get_work`、`list_candidate_papers`。
- OpenAlex ID 规范化、abstract inverted index 还原、去重逻辑已有基础实现。

主要限制：

- 默认仍是 `FixtureOpenAlexClient`，真实 OpenAlex 没有接入 CLI/UI 的运行入口。
- 没有 MySQL 状态表，缺少 `analysis_corpora`、`corpus_membership`、`crawl_jobs`、`crawl_frontier` 等持久化。
- 没有真实 Topics 查询、Author/Institution/Topic ID disambiguation、OpenAlex polite pool 配置入口。
- Corpus 幂等目前依赖内存和 artifact 文件，不具备跨进程、跨运行的数据库级幂等。
- BFS 有深度和数量限制，但没有可恢复 frontier、失败重试、数据截止时间审计。

### 2.3 Graph Analytics

已实现：

- `GraphAnalyticsService` 可基于 corpus 构建 paper citation graph snapshot。
- 支持年度趋势、Topic 统计、PageRank、社区划分、桥梁分数、关键论文排序。
- 会写出 graph snapshot 和 graph metrics artifact。

主要限制：

- 社区发现当前是无向连通分量，不是计划中的 Leiden/Louvain。
- 图节点目前主要是 paper，没有完整建模 Author、Topic、Institution、Venue 等节点。
- 没有 Neo4j schema、Graph Snapshot 持久化、参数版本化和可复现任务记录。
- 桥梁论文是简化跨连通分量启发式，不是更稳健的跨社区连接或 betweenness 近似。
- 没有局部 corpus 警告、图规模采样策略、图算法耗时和质量统计。

### 2.4 Evidence RAG

已实现：

- `EvidenceRAGService` 支持 Parent-Child chunk、hash embedding、本地 cosine 检索、Evidence Bundle 构建。
- 可读取 `.txt/.md`，可选依赖 `pypdf` 读取 PDF。
- PDF 不可用时会降级为 `title + abstract` fallback evidence。
- `verify_claim_support` 有基于词重合和检索分的简化 support 判断。

主要限制：

- 没有 PDF 发现、下载、SHA-256 文件去重、ObjectStorage 存储和 materialization job。
- 页码、章节、char span、token span 的准确性不足；当前 page 多为 `None`。
- 没有 Qdrant；向量索引是内存 hash embedding，不能支撑真实规模检索。
- 没有 paper profile embedding、内容 hash 跳过重新 embedding、embedding run 记录。
- 没有解析器 adapter、OCR fallback、解析失败降级策略和 Evidence Recall 指标。

### 2.5 Agent Runtime 与 Skills

已实现：

- `ResearchAgent` 串联七个 skills：`scope_new_field`、`discover_research_perspectives`、`build_research_corpus`、`map_field_structure`、`identify_key_papers`、`analyze_key_papers`、`generate_field_guide`。
- `MCPManager` 提供 in-process MCP facade 调用、预算计数和 trace 记录。
- `BudgetTracker` 支持工具调用数和 retry 上限。
- `TraceRecorder` 记录 plan、task、tool call、tool result 和失败事件。
- `scripts/run_research_agent_demo.py` 可以离线跑完整闭环。

主要限制：

- Runtime 是确定性 DAG，不是计划中的 planner/evaluator/replanner/checkpointer 完整 LangGraph runtime。
- MCP 是进程内 facade，不是 stdio MCP server，不能被 MCP Inspector 发现。
- 预算只有 tool call 和 retry，缺少 token、cost、latency、PDF 数、chunk 数等预算 enforcement。
- 失败恢复目前主要是异常记录，没有自动降级执行策略。
- `FeatureFlags` 已有字段，但 PaperQA2/GPT Researcher/STORM 降级逻辑没有完整接线。

### 2.6 UI 与测试

已实现：

- Streamlit 基础 UI：Chat、Plan、Analysis、Evidence、Runs tabs。
- UI 能展示 field guide、plan JSON、graph JSON、evidence JSON、trace。
- 测试覆盖核心 contract、离线 corpus/graph/RAG pipeline、runtime happy path。

主要限制：

- UI 仍偏 artifact browser，不是面向研究者的问答式工作台。
- 缺少年份趋势图、Topic 排名图、PageRank 表、社区图、证据卡片、Artifact 下载入口的完整体验。
- 测试主要覆盖离线 happy path，缺少真实 OpenAlex、Qdrant、PDF、失败恢复、预算违规、幂等和评测集。

## 3. 与原计划的主要差距

| 计划能力 | 当前状态 | 差距等级 | 风险 | 建议处理 |
|---|---|---:|---|---|
| 自研 Agent Runtime：intake/planner/executor/evaluator/replanner/writer/checkpointer | 有固定 DAG runtime 和七个 skills | 高 | 复杂任务失败后无法自动恢复或改计划 | P0：补 evaluator、replanner、checkpoint、run/task 状态 |
| stdio MCP Server：scholarly-data、graph-analytics、evidence-rag | 只有 in-process facade | 高 | 无法用 MCP Inspector 或外部 Agent 调用 | P0：实现 stdio MCP 包装和工具 schema |
| 真实 OpenAlex corpus | 有 fixture 和极简 HTTP client | 高 | demo 与真实领域覆盖差异大 | P0：接入真实 OpenAlex、缓存、重试、polite email |
| MySQL 状态表和幂等写入 | 未实现 | 高 | 无法恢复中断、无法审计 corpus 边界 | P0：设计并实现最小 SQLite/MySQL repository |
| Neo4j 图和 PageRank/Leiden/Louvain | 只有内存 paper graph、PageRank 和连通分量 | 高 | 图结构解释力不足 | P0：先 NetworkX/Louvain，后接 Neo4j snapshot |
| 摘要级 embedding 与 Qdrant | 未实现真实 embedding/Qdrant | 高 | 语义召回不可用 | P0：接 embedding adapter、本地 numpy fallback、Qdrant writer |
| PDF materialization | 仅支持本地文本/PDF 读取和 abstract fallback | 高 | 证据无法定位页码或章节 | P0：PDF 下载、SHA-256、parser adapter、page/section chunk |
| Evidence Bundle 证据综合 | 有简化 evidence bundle | 中 | 支持/反驳判断过粗 | P0/P1：先规范证据字段，再接 PaperQA2 adapter |
| STORM 式多视角 skill | 有固定模板 perspectives | 中 | 检索覆盖不够 | P1：接 LLM prompt 和失败降级 |
| GPT Researcher web supplement | 未实现 | 中 | 缺少项目/数据集/生态补充 | P1：加 facade 和 web evidence 隔离 |
| Streamlit 工作台 | 有基础 tabs 和 JSON 展示 | 中 | 难以用于真实分析 | P0/P1：补图表、证据卡、runs 浏览和下载 |
| 评测与消融 | 只有 4 个单元/集成测试 | 高 | 无法判断质量和可靠性 | P0：建立三领域 quick/standard benchmark |
| P2 外部工具 | 未实现 | 低 | 不阻塞 MVP | 暂缓 |

## 4. 未完成任务清单

### 4.1 P0 必做

1. 真实 OpenAlex 接入
   - 在 CLI/UI/config 中增加 `FixtureOpenAlexClient` 与 `HttpOpenAlexClient` 选择。
   - 支持 OpenAlex polite email、速率控制、缓存目录、重试和错误记录。
   - 补 Topics、Works、Authors、Institutions 的最小字段清洗。

2. 最小持久化层
   - 先用 SQLite 或 MySQL repository 抽象实现状态表。
   - 覆盖 `analysis_runs`、`analysis_corpora`、`corpus_membership`、`crawl_jobs`、`crawl_frontier`、`graph_snapshots`、`materialization_jobs`、`paper_files`、`chunk_runs`、`embedding_runs`、`mcp_tool_calls`、`analysis_artifacts`。
   - 支持幂等 upsert、run resume 和 data cutoff。

3. stdio MCP Server
   - 把当前三个 in-process facade 包装为可独立启动的 stdio MCP server。
   - 为每个工具暴露 JSON schema、错误协议和大结果 artifact 化。
   - 用 MCP Inspector 或等价 smoke test 验证工具可发现。

4. 图分析生产化
   - 增加 Author、Topic、Institution 节点和 `CITES`、`AUTHORED_BY`、`HAS_TOPIC` 边。
   - 用 NetworkX/igraph 实现 Louvain 或 Leiden fallback。
   - 保存 graph snapshot、算法参数和版本。
   - Neo4j 先作为可选持久图后端，不阻塞本地图算法。

5. PDF 与 Evidence RAG
   - 实现 PDF URL 发现、下载、SHA-256 去重、本地 ObjectStorage。
   - 引入 parser adapter，记录页码、章节、char span、token span。
   - 实现 title+abstract paper profile embedding、child embedding、内容 hash 跳过重算。
   - 接入 Qdrant，保留本地 numpy/hash fallback。

6. Runtime 协议收口和失败恢复
   - 每个工具调用生成并保存 `MCPResult`，写入 `ResearchRun.results`。
   - 增加 evaluator/replanner：OpenAlex 失败缩小范围，Qdrant 失败用本地检索，PDF 失败用摘要级分析。
   - 增加 checkpointer，支持中断恢复和重复运行幂等。

7. 评测和验收集
   - 固定三个领域：机器人、图学习、信息系统/管理。
   - 每个领域 quick/standard 两种模式。
   - 输出 corpus 覆盖、图结果、证据召回、结论证据覆盖、预算违规、失败恢复等指标。

### 4.2 P1 增强

1. PaperQA2 Adapter
   - 实现 `synthesize_evidence`、`compare_papers`、`summarize_paper`、`detect_conflicting_evidence`。
   - 只消费 `EvidenceBundle`，不绕过自研检索。

2. GPT Researcher Facade
   - 实现 `research_technology_ecosystem`、`find_official_project_resources`、`research_dataset_and_benchmark`、`supplement_non_paper_context`。
   - 所有结果标记 `evidence_type = web_research`，不混入论文证据。

3. STORM 多视角 skill 强化
   - 用 LLM 生成理论、方法、数据集、应用、局限、趋势视角。
   - 保留固定模板作为失败降级。

4. UI 体验升级
   - 增加年度趋势图、Topic 排名、PageRank 表、社区图、关键论文表、证据卡片、Artifact 下载。
   - Runs 页面支持历史运行浏览和 trace 过滤。

### 4.3 P2 暂缓

- LitStudy MCP。
- bibliometrix R sidecar。
- Head Start 深度集成。
- OpenScholar。
- 完整 STORM runtime。
- DeerFlow 或 ScienceClaw 整体迁移。
- GNN/HGT/GraphMAE。
- 动态主题模型、全局共被引、全图 betweenness、热点预测。
- 多 Agent 辩论和自动修改 skills。

## 5. 分阶段实现计划

### Phase 1：协议和 runtime 收口

目标：把当前 scaffold 从“能跑 demo”提升到“每个步骤可审计、可恢复、可测试”。

实施内容：

- `MCPManager.call` 返回统一 `MCPResult` 或把原始结果包装为 `MCPResult`。
- `ResearchRun.results` 写入所有工具调用结果。
- 增加 `agent_runs`、`agent_tasks`、`mcp_tool_calls` 的 repository 抽象。
- 增加 evaluator/replanner 的最小状态机。
- 清理 `__pycache__` 和测试产生的临时目录策略。

验收：

- 离线 demo 仍能跑通。
- `run.json` 中包含完整 results、trace、artifacts。
- 工具失败时不会直接丢失 run 状态。

### Phase 2：真实 OpenAlex corpus + 幂等持久化

目标：让系统能用真实 OpenAlex 数据构建可追溯 corpus。

实施内容：

- CLI/UI 增加 `--provider fixture|openalex`、OpenAlex email、cache dir。
- 完善 OpenAlex client：分页、重试、速率控制、Topics、works by ID、citing works。
- 实现 corpus repository：corpus 边界、成员来源、crawl frontier、data cutoff。
- 支持重复运行跳过已抓取数据。

验收：

- 输入一个主题可获得 2,000 到 3,000 篇真实文献。
- BFS 深度不超过 2。
- 重复运行无重复记录。

### Phase 3：图分析生产化

目标：补齐原计划中的图结构分析能力。

实施内容：

- 构建 Paper、Author、Topic、Institution 节点。
- 构建 CITES、AUTHORED_BY、HAS_TOPIC、AFFILIATED_WITH 边。
- 本地实现 PageRank、Louvain/Leiden、社区代表、桥梁论文。
- 保存 graph snapshot、参数、算法版本和 warnings。
- Neo4j 作为可选同步后端接入。

验收：

- 相同 snapshot 和参数结果可复现。
- 输出年度趋势、Topic 排名、PageRank、社区、关键论文。
- 图节点和边数不超过配置上限。

### Phase 4：PDF/Qdrant Evidence RAG

目标：从摘要级 fallback 过渡到可定位页码/章节的全文证据。

实施内容：

- PDF discovery/download/materialization job。
- SHA-256 去重和本地 ObjectStorage。
- Parser adapter：优先 `pypdf` 或 PyMuPDF，失败降级。
- 章节感知 Parent、token 控制 Child。
- 接 embedding adapter 和 Qdrant writer；保留本地 fallback。
- Evidence Bundle 记录 page、section、span、retrieval score、support status。

验收：

- `work_id -> PDF -> ParsedDocument -> Parent-Child -> Qdrant -> EvidenceBundle` 跑通。
- Parent 回溯成功率 100%。
- 页码或章节覆盖率达到可评测水平。

### Phase 5：UI、评测、演示冻结

目标：形成可展示、可复现、可比较的第一阶段交付。

实施内容：

- Streamlit 工作台补齐 Chat、Plan、Analysis、Evidence、Runs。
- 增加图表、证据卡、关键论文表、Artifact 下载。
- 固定三个测试领域，跑 quick/standard。
- 输出基础版与增强版消融报告。

验收：

- 每个测试领域生成完整 artifacts 和 field guide。
- 关闭 PaperQA2/GPT Researcher 后核心闭环仍可运行。
- 评测报告包含 corpus、graph、RAG、Agent、成本/延迟、失败率指标。

## 6. 验收标准

### 6.1 文档和协议

- `MCPResult`、`EvidenceBundle`、`ArtifactRef` 都有稳定 schema。
- 每个 artifact 可追溯到 run、task、tool call、输入 hash、创建时间。
- 第三方原始返回不会直接进入 Agent prompt。

### 6.2 数据层

- OpenAlex ID 去重正确率 100%。
- corpus membership 可追踪率 100%。
- 重复运行幂等通过。
- 中断恢复通过。

### 6.3 图分析

- 相同 graph snapshot 和参数可复现。
- PageRank、Topic 统计、社区代表论文都有 artifact。
- 图规模超限时有明确 warnings 和采样策略。

### 6.4 RAG

- PDF 不可获取时降级摘要级分析。
- Qdrant 不可用时降级本地检索。
- Evidence Bundle 至少包含 work_id、child_id、parent_id、page/section、retrieval score。
- 无证据时拒答或标记不确定。

### 6.5 Agent

- Plan schema 合法率达到 95% 以上。
- MCP 参数合法率达到 95% 以上。
- 预算违规率为 0。
- 失败恢复率达到可评测标准。

### 6.6 UI 和评测

- UI 可查看 plan、analysis、evidence、runs、trace、artifact。
- 三个固定领域 quick/standard 均能生成报告。
- 消融报告能比较基础、STORM、PaperQA2、GPT Researcher 增强。

## 7. 默认假设

- 保留离线 fixture 作为测试后备，不让真实 API 成为单元测试阻塞项。
- 优先补齐 P0 闭环，不先投入 P2 外部工具。
- 默认 ObjectStorage 使用本地文件系统，接口设计上保留替换 MinIO/S3 的余地。
- Neo4j、Qdrant、PaperQA2、GPT Researcher 都通过 feature flag 控制；任一外部服务失败都不能阻塞核心闭环。
- 当前 `src/research_agent` 代码可继续作为 scaffold 演进，不需要推倒重写。
