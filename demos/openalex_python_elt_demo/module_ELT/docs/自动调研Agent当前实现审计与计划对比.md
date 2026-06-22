# 自动调研 Agent 当前实现审计与计划对比

## 1. 调研目标与结论

本文对照 `docs/agent_plan_v1.pdf` 的第一阶段设计目标，审计当前 `src/research_agent` 与相关 demo 的实现状态，重点判断当前 demo 是否已经支持一次真实完整调研：真实 Agent 调研、OpenAlex 数据获取、数据清洗、数据存储、摘要 embedding、Neo4j/ES/Qdrant 多源存储、图算法分析、skill/MCP 调用和最终报告撰写。

结论：当前实现不支持稳定完成真实端到端调研。代码中已经出现大量计划中的模块骨架和部分离线能力，但主运行链路仍没有把真实 LLM Agent、MySQL、OpenAlexSource、PDF pipeline、EmbeddingPipeline、Qdrant、Neo4j/ES 同步和报告生成稳定串起来。当前 demo 更适合作为“第一阶段工程骨架和局部能力展示”，不能宣称已经完成 `OpenAlex -> MySQL -> Neo4j/Qdrant -> Agent -> Evidence RAG -> Report` 的完整闭环。

## 2. 当前实现功能清单

| 分类 | 当前状态 | 说明 |
|---|---|---|
| Runtime / Agent | 模块存在但主流程不稳定 | 已有 `ResearchGraphAgent`、ReAct 与 Planner-Executor 双模式、budget/trace/planner 等文件；但共享状态传递、完成状态判定、结果收集和报告生成仍有断点。 |
| 真实 LLM Agent | 占位/不可用 | `llm_driven_react`、`llm_driven_plan` 是开关；实际 `_llm_think`、`_llm_generate_plan` 仍回退到规则/默认计划，没有 DeepSeek、DeepSeek Flash 或 OpenAI-compatible chat adapter。 |
| Skill 调用 | 存在但实测失败 | 七个 skill 的结构已经存在；Planner-Executor 路径中后续任务可能因缺少 `question`、`config`、`artifact_store`、`field_corpus` 等共享状态而失败。 |
| MCP | 模块存在但需补协议闭环 | 已改为文件夹式 MCP server，包含 common、scholarly_data、graph_analytics、evidence_rag，并有 stdio server 入口；但主流程更多是进程内 facade，stdio MCP 的完整兼容性、Inspector smoke test、大结果 artifact/resource 协议仍需验证。 |
| OpenAlex 数据获取 | 模块存在但真实链路未稳定接入 | 已有 `OpenAlexSource`、fixture client、HTTP/OpenAlex 相关入口；CLI 有 `--provider fixture|openalex`。但真实 provider 的分页、引用扩展、速率限制、ID 规范化和运行入口仍需真实 API 验证。 |
| 数据清洗 | 模块存在但未贯穿主流程 | 已有 cleaners，包括 Work/Author/Institution/Concept/Venue 等清洗思路；但 agent demo 主链路没有形成“获取 -> 清洗 -> 幂等入库 -> corpus snapshot”的稳定执行链。 |
| MySQL 持久化 | 模块存在但未接入主流程 | 已有 `ResearchRepository`、`MySQLResearchRepository`、DDL、migration、mysql inserter 等；但 `ResearchAgent` 构造 `ResearchGraphAgent` 时仍使用 `repository=None`，分析运行、任务、corpus、tool call、graph snapshot 未真正写入 MySQL。 |
| Neo4j 同步 | 模块存在但未接入主流程 | 已有 `Neo4jGraphSync`；CLI 暴露 `--sync-neo4j` 特征开关，但当前主流程没有完成从 corpus/graph snapshot 到 Neo4j 的可验收同步链路。 |
| ES 同步 | 模块存在但未接入主流程 | 已有 `ESSyncManager`；CLI 暴露 `--sync-es`，但尚未形成可运行的 denormalized 文档构建与 bulk indexing 演示闭环。 |
| 摘要 embedding | 模块存在但未接入主流程 | 已有 `EmbeddingPipeline`、`HashEmbedder`、`SentenceTransformerEmbedder`、`OpenAIEmbedder`；但 agent/CLI 默认没有执行 paper profile embedding，也没有将结果作为 graph/RAG/报告主输入。 |
| Qdrant / VectorStore | 模块存在但未接入主流程 | 已有 `VectorStore`、`LocalNumpyStore`、`QdrantStore`；但 Qdrant 未作为默认或可验收路径参与 evidence retrieval。 |
| PDF / Parser / Evidence RAG | 模块存在但链路未闭合 | 已有 `PDFManager`、`ParserAdapter`、`EvidenceRAGService` 的 materialization 入口；但主流程未注入 PDF manager/parser/vector store，ReAct 路径可能直接 build evidence bundle 而没有先 materialize PDF/child chunks。 |
| 图分析 | 部分可用 | `GraphAnalyticsService` 已支持 paper/author/topic 图、PageRank、topic/year stats、bridge score、key paper ranking；社区检测可尝试 Louvain，否则 fallback 到连通分量。Neo4j 持久化和高级社会网络分析产物仍未进入主流程。 |
| Artifact / Result | 部分可用但未统一收口 | 本地 artifact store 与 `MCPResult` 协议已经存在；但 `ResearchRun.results`、`task_results`、tool call 结果没有在实测 run 中稳定收集，可能出现 `results=[]`、`artifacts=[]`。 |
| UI | 基础可用但非工作台 | Streamlit UI 文件存在，可展示基础运行信息/JSON/报告；尚未形成计划、分析、证据卡片、图谱、下载 artifact 的完整工作台体验。 |
| 测试 | 覆盖离线 happy path 但当前失败 | 已有 pytest 测试；审计运行显示 1 failed, 3 passed，失败与 `field_guide.md` 未生成有关，说明 runtime/report 链路不稳定。 |

## 3. 与 agent_plan_v1.pdf 的逐项对比

| 计划能力 | 当前实现 | 是否可用于真实 demo | 主要证据 | 缺口 |
|---|---|---|---|---|
| 接入真实 Agent，例如 DeepSeek/DeepSeek Flash | 仅有 LLM-driven 开关与占位函数 | 否 | `_llm_think`、`_llm_generate_plan` 仍返回规则/默认计划 | 缺 OpenAI-compatible chat client、模型配置、tool calling、重试和 token 预算 |
| 输入陌生领域后自动调研 | 离线 fixture 可启动，但链路不稳定 | 否 | demo 可返回 completed，但 tool_calls=0，部分任务失败 | completed 判定失真；planner/shared state 断裂；报告缺失 |
| OpenAlex 数据获取 | 有 `OpenAlexSource` 与 `--provider openalex` | 部分 | 文件和 CLI 参数存在 | 需真实 API 验证分页、引用/被引扩展、缓存、限速、ID 规范化 |
| 数据清洗 | 有 cleaner 模块 | 部分 | `data/cleaners.py` 存在 | 未贯穿 corpus 构建和 MySQL 幂等入库 |
| Corpus 边界、snapshot、cutoff | 有部分模型和 repository DDL | 否 | MySQL schema 中有 corpus/crawl/frontier 表 | 主流程 repository=None，未写入真实 snapshot |
| MySQL 多表存储 | 有 repository、DDL、migration、inserter | 否 | `persistence/mysql_repository.py` 等存在 | CLI/agent 未实例化 MySQL repository，任务和 corpus 未入库 |
| 摘要 embedding 提取 | 有 embedding pipeline/adapter | 否 | `EmbeddingPipeline`、`OpenAIEmbedder`、`HashEmbedder` 存在 | 未接入 agent 主流程；未形成 paper profile embedding artifact |
| Qdrant 检索 | 有 QdrantStore | 否 | `data/vector_store.py` 存在 | 未注入 Evidence RAG；缺运行配置、collection 初始化和验收命令 |
| PDF 下载与解析 | 有 PDFManager/ParserAdapter | 否 | `data/pdf_manager.py`、`data/parser_adapter.py` 存在 | 主流程未执行下载、SHA-256 去重、页码级解析、section-aware chunk |
| Parent-Child Evidence RAG | 服务层有部分能力 | 否 | `EvidenceRAGService` 有 materialize/search/bundle 入口 | 未接入 vector store；ReAct 未先 materialize；证据可能为空 |
| Neo4j 图数据库 | 有 sync 模块与 CLI flag | 否 | `Neo4jGraphSync`、`--sync-neo4j` 存在 | 没有从主流程产出 graph snapshot 并同步 Neo4j 的稳定路径 |
| Elasticsearch | 有 sync 模块与 CLI flag | 否 | `ESSyncManager`、`--sync-es` 存在 | 不在第一阶段主检索链路；未形成可验收 demo |
| PageRank | 已有服务层实现 | 部分 | `GraphAnalyticsService` 可计算 PageRank | 需要和 corpus snapshot、artifact、report、UI 串联 |
| Louvain/Leiden 社区 | Louvain 尝试，fallback 连通分量 | 部分 | 可选依赖不可用时退化 | 缺稳定依赖声明、参数记录、算法运行 artifact |
| 社交网络分析/关键节点 | 有桥梁分数和 key paper ranking | 部分 | 服务层已有 ranking 逻辑 | 需要更明确的作者/机构网络、中心性指标、可复现实验 |
| MCP stdio server | 文件结构存在 | 部分 | 三类 server 均有 `server.py/tools.py/service_bridge.py` | 缺 MCP Inspector 验证、资源协议、大结果 artifact 化一致性 |
| Skill 与 MCP 调用 | 结构存在 | 否 | Planner 路径任务可创建 | 实测 tool_calls=0，后续任务失败；MCPResult 未统一进入 run results |
| 最终报告撰写 | 有 writer/field guide 目标 | 否 | pytest 失败显示 `field_guide.md` 未生成 | 报告生成触发条件、失败降级和 artifact 注册需修复 |
| UI 工作台 | 基础 UI 存在 | 部分 | `src/research_agent/ui/app.py` 存在 | 尚缺计划面板、图分析、证据卡、artifact 下载、run 对比 |
| 评测与消融 | 初步测试存在 | 否 | pytest 当前失败 | 缺真实 API 集成测试、预算违规、失败恢复、幂等、benchmark 数据集 |

## 4. 实测结果

审计时使用当前本地实现做了只读/轻量运行核对，结果如下：

| 命令/场景 | 结果 | 判断 |
|---|---|---|
| `python -m pytest -q --basetemp outputs\pytest_temp` | `1 failed, 3 passed` | 当前测试未通过，失败点与 `field_guide.md` 未生成有关。 |
| `python -B scripts/run_research_agent_demo.py "graph learning for scientific discovery" --artifact-root outputs\plan_audit_artifacts --max-field-corpus 10 --max-pdfs 2 --max-key-papers 5 --provider fixture --mode react` | 返回 `status=completed`，但 `tool_calls=0` | completed 不能代表真实调研闭环完成。 |
| `python -B scripts/run_research_agent_demo.py "graph learning for scientific discovery" --artifact-root outputs\plan_audit_artifacts --max-field-corpus 10 --max-pdfs 2 --max-key-papers 5 --provider fixture --mode planner_executor` | 返回 `status=completed`，但 `tool_calls=0` | Planner-Executor 主链路没有稳定调用 MCP/skills。 |
| 查看 `run.json` / `trace.json` | 可见 `results=[]`、`artifacts=[]`、部分任务失败 | 运行结果没有把 artifact、MCPResult、task result 稳定收口。 |
| Planner-Executor trace | T1 可完成，T2-T7 可能因缺少 `question`、`config`、`field_corpus`、`field_structure` 等失败 | 共享状态传递和失败状态判定需要作为 P0 修复。 |

这些结果说明：当前 demo 能启动，并且有较多工程组件，但不能以 `completed` 字段作为完整调研成功的证据。当前最关键的问题不是“文件是否存在”，而是“文件是否已经被主流程注入、执行、持久化、产出 artifact，并能被报告/UI 追溯”。

## 5. 缺失实现与风险分级

### P0 阻塞项

| 任务 | 风险 | 建议处理 |
|---|---|---|
| 修复 runtime 共享状态传递 | skill 链路断裂，后续任务失败 | 在 ReAct、Planner-Executor、DAG fallback 中统一 `question/config/artifact_store/run_id` 等上下文。 |
| 修复 completed 判定 | 失败运行被标记成功，误导验收 | 以 task status、required artifacts、field guide、MCP/tool result 为完成条件。 |
| 将 MySQL repository 接入主流程 | 数据不落库，无法恢复/追溯/幂等 | CLI/config 创建 repository，传入 `ResearchGraphAgent` 与 services。 |
| 接入真实 LLM agent adapter | 不能支持 DeepSeek/DeepSeek Flash 等真实 agent | 增加 OpenAI-compatible chat adapter，支持 DeepSeek base_url/api_key/model/tool call。 |
| 验证真实 OpenAlexSource | 真实数据获取不可确认 | 增加 OpenAlex smoke test，覆盖 search/get_work/references/citing/cutoff/cache。 |
| 接入 PDF/embedding/vector/Qdrant | Evidence RAG 无法生产化 | 将 PDFManager、ParserAdapter、EmbeddingPipeline、VectorStore 注入 EvidenceRAGService。 |
| 接入 Neo4j/ES 同步 | 多源数据库只是文件存在 | 从 graph snapshot/corpus artifacts 触发 sync，并记录同步结果。 |
| 修复报告生成和 artifact 注册 | 无最终可交付物 | 保证 fixture quick run 至少生成 field guide、trace、run、corpus、graph、evidence artifacts。 |
| 修复测试 | 当前无法作为可交付基线 | 先让离线 fixture 测试全绿，再补真实 provider smoke test。 |

### P1 增强项

| 任务 | 价值 |
|---|---|
| 完善 stdio MCP 协议兼容性 | 便于用 MCP Inspector 和外部 client 验证。 |
| 大结果 artifact/resource 化 | 避免 tool result 过大，统一追溯 artifact。 |
| PaperQA2 adapter 真实接线 | 作为增强合成能力，不阻塞核心 MVP。 |
| GPT Researcher facade 真实接线 | 作为 web research 增强，不阻塞核心 MVP。 |
| UI 证据卡片、图表、run 对比 | 让调研结果可读、可检查、可汇报。 |
| 图算法参数和 artifact 记录 | 提高 PageRank/community/key node 输出可复现性。 |

### P2 暂缓项

| 任务 | 暂缓原因 |
|---|---|
| LitStudy / bibliometrix 完整迁移 | 第一阶段主线已经足够复杂，先不扩大技术面。 |
| 完整 STORM runtime | 可先保留 perspective prompt/skill，不必迁移完整系统。 |
| 多 Agent 辩论 | 对 MVP 闭环不是必需。 |
| 大规模 Neo4j/ES 运维能力 | 先完成本地和小规模验收，再扩展生产部署。 |

## 6. 推荐后续实现顺序

| Step | 目标 | 验收标准 |
|---|---|---|
| 1 | 修 runtime 和测试基线 | fixture demo 真实产生 tool calls、corpus、graph、evidence、field_guide；pytest 全绿。 |
| 2 | 接 MySQL repository 和 migration 命令 | analysis_run/task/tool_call/corpus/corpus_membership/crawl_frontier 可幂等写入。 |
| 3 | 接真实 OpenAlex provider | `--provider openalex --email ...` 可完成小规模 corpus，重复运行不重复写入。 |
| 4 | 接 embedding/vector store/Qdrant | title+abstract embedding 可写入 LocalNumpy/Qdrant，并能检索 paper profile。 |
| 5 | 接 PDF materialization 和 Evidence RAG | `work_id -> PDF -> ParsedDocument -> Parent/Child -> VectorStore -> EvidenceBundle` 跑通，证据带 page 或 section。 |
| 6 | 接 Neo4j/ES sync 和图算法产物 | graph snapshot 可同步 Neo4j，PageRank/community/key paper 结果可追溯到 artifact。 |
| 7 | 接 DeepSeek/OpenAI-compatible agent | LLM 可基于 tool schema 做 plan/react/tool call/evaluate/replan，并受 budget 限制。 |
| 8 | 补 UI 和 benchmark | Streamlit 展示 Plan、Analysis、Evidence、Runs；固定测试领域输出 quick/standard 报告。 |

## 7. 当前 demo 可用性判断

当前 demo 可以用于演示模块结构、离线 fixture、局部图分析和 artifact/runtime 的设计方向；也可以作为后续 P0 修复的骨架。

当前 demo 不可宣称已支持真实完整调研，尤其不能宣称已经支持 DeepSeek/DeepSeek Flash 等真实 agent 自动完成调研，也不能宣称已经完成 OpenAlex 数据获取、清洗、MySQL 存储、摘要 embedding、Qdrant、Neo4j/ES、图算法分析、Evidence RAG、MCP/skill 调用和最终报告撰写的生产化闭环。

当前 demo 不可宣称已完成 `OpenAlex -> MySQL -> Neo4j/Qdrant -> Agent -> 报告` 全链路。下一步应优先收口 P0：让 fixture 端到端真实成功，再逐步接入真实 OpenAlex、MySQL、Qdrant、Neo4j/ES 和 DeepSeek/OpenAI-compatible LLM。
