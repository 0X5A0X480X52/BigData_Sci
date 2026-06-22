# Research Agent Demo 使用说明

本文档说明当前可运行的第一阶段 demo：输入一个研究问题，系统会在离线 fixture 数据上完成领域 scope、OpenAlex 风格 corpus 构建、图分析、关键论文识别、摘要级 Parent-Child evidence RAG、证据验证和 field guide 报告输出。

## 当前可用能力

当前 demo 已经能在无网络、无数据库的情况下跑通完整离线闭环：

- 使用 fixture OpenAlex 风格论文数据构建 corpus。
- 生成本地 artifact，包括 corpus、graph snapshot、graph metrics、parent/child chunks、evidence bundle、trace、run metadata 和 field guide。
- 通过 MCP facade 调用 `scholarly-data`、`graph-analytics`、`evidence-rag` 三类工具。
- 执行图分析，包括 topic/year statistics、PageRank、社区检测 fallback、bridge score 和 key paper ranking。
- 对关键论文使用标题和摘要作为 fallback full text，生成 Parent-Child chunks，并用本地 hash embedding 检索证据。
- 生成最终 `reports/field_guide.md`。

当前 demo 的默认可验收目标是离线闭环可用。真实 OpenAlex、MySQL、Qdrant、Neo4j、Elasticsearch、DeepSeek/OpenAI-compatible LLM agent 已有部分模块或入口，但还不是默认已验收链路。

## 运行离线 Demo

在仓库根目录执行：

```powershell
python scripts/run_research_agent_demo.py "retrieval augmented generation for scientific discovery" --artifact-root outputs\demo_run --max-field-corpus 15 --max-pdfs 2 --max-key-papers 5 --provider fixture --mode react
```

成功时会看到类似输出：

```text
[provider] fixture (offline/synthetic data)
run_id=AR_xxxxxxxxxxxx
status=completed
agent_mode=react
artifacts=outputs\demo_run/AR_xxxxxxxxxxxx
trace_events=27
tool_calls=6
```

Planner-Executor 模式也可运行：

```powershell
python scripts/run_research_agent_demo.py "graph learning for scientific discovery" --artifact-root outputs\demo_run_pe --max-field-corpus 12 --max-pdfs 2 --max-key-papers 5 --provider fixture --mode planner_executor
```

如果当前环境未安装 LangGraph，`react` 和 `planner_executor` 都会自动走确定性 DAG fallback，但输出仍包含完整 artifact 和 tool trace。

## 产物目录

每次运行会在 `{artifact-root}/{run_id}/` 下生成：

```text
corpora/
  field_*.json                  # OpenAlex 风格 corpus snapshot
graph/
  graph_*.json                  # graph snapshot
  graph_*_metrics.json          # PageRank/community/bridge/key papers
evidence/
  *_parents.jsonl               # parent chunks
  *_children.jsonl              # child chunks
  EB_*.json                     # verified evidence bundle
reports/
  trace.json                    # agent/tool/skill trace
  run.json                      # run metadata, artifact refs, MCP results, task results
  field_guide.md                # 最终领域指南
```

最重要的交付文件是：

- `reports/field_guide.md`：给人阅读的调研报告。
- `reports/trace.json`：每一步 skill/MCP 调用轨迹。
- `reports/run.json`：本次运行的结构化元数据、artifact refs、MCPResult 和 TaskResult。
- `graph/*_metrics.json`：关键论文排序、PageRank、community 和 bridge 结果。
- `evidence/EB_*.json`：带 `support_status` 的证据包。



## Streamlit 工作台

启动 UI：

```powershell
streamlit run src\research_agent\ui\app.py
```

UI 现在与 CLI 共用 `ResearchRunOptions` / `run_research_workflow` 入口，可在侧边栏配置：

- fixture 或真实 OpenAlex provider。
- ReAct 或 Planner-Executor 模式。
- DeepSeek/OpenAI-compatible LLM plan/react 开关。
- local artifact 或 MySQL 持久化，并可选择初始化 schema。
- 摘要 fallback 或 PDF 下载解析。
- local_numpy 或 Qdrant vector store。
- Python graph 分析或 Neo4j graph snapshot 同步。
- best-effort Elasticsearch sync 标记。

运行完成后，页面会展示 Report、Plan、Corpus、Graph、Evidence、Artifacts 和 Run Trace。外部服务不可用时，UI 会在顶部和 Run Trace 中显示 warning，并继续使用本地 artifact/fixture/fallback 完成报告。
## 可选真实 Agent / DeepSeek

当前 demo 已提供 OpenAI-compatible chat adapter，可用于 DeepSeek、OpenAI 或兼容 `/chat/completions` 的模型服务。默认离线模式不需要 API key；开启 LLM 后，如果没有配置 key 或接口不可用，会自动降级到 deterministic workflow，并继续产出完整 artifacts。

使用 DeepSeek 生成 Planner-Executor 计划：

```powershell
$env:DEEPSEEK_API_KEY="your-api-key"
python scripts/run_research_agent_demo.py "retrieval augmented generation for scientific discovery" --provider fixture --mode planner_executor --llm-plan --llm-base-url https://api.deepseek.com --llm-model deepseek-chat --artifact-root outputs\deepseek_plan_demo --max-field-corpus 15 --max-pdfs 2
```

使用 DeepSeek 驱动 ReAct action selection，需要当前环境安装 LangGraph；未安装时 demo 会回到稳定 DAG fallback：

```powershell
$env:DEEPSEEK_API_KEY="your-api-key"
python scripts/run_research_agent_demo.py "graph learning for scientific discovery" --provider fixture --mode react --llm-react --llm-base-url https://api.deepseek.com --llm-model deepseek-chat --artifact-root outputs\deepseek_react_demo --max-field-corpus 15 --max-pdfs 2
```

相关环境变量：

```text
RA_LLM_API_KEY      # 优先使用；也可用 DEEPSEEK_API_KEY 或 OPENAI_API_KEY
RA_LLM_BASE_URL     # 默认 https://api.deepseek.com
RA_LLM_MODEL        # 默认 deepseek-chat
RA_LLM_TIMEOUT      # 默认 30
```
## 可选真实 OpenAlex

CLI 暴露了真实 OpenAlex provider：

```powershell
python scripts/run_research_agent_demo.py "retrieval augmented generation" --provider openalex --email your@email.com --artifact-root outputs\openalex_demo --max-field-corpus 20 --max-pdfs 2
```

注意：真实 OpenAlex 路径依赖 `pyalex`。如果未安装，会回退到 fixture。当前真实 OpenAlex 路径建议作为 smoke test 使用，不应直接宣称已完成生产级数据获取、清洗和持久化闭环。

## 验证命令

运行测试：

```powershell
python -m pytest -q --basetemp outputs\pytest_temp_all
```

当前应通过核心测试：

```text
4 passed
```

检查 demo 产物：

```powershell
Get-ChildItem -Recurse outputs\demo_run\<run_id>
Get-Content -Encoding utf8 outputs\demo_run\<run_id>\reports\field_guide.md -TotalCount 80
rg -n "tool_call|support_status|field_guide|PageRank|key_papers" outputs\demo_run\<run_id>
```

## 当前边界

- 真实 LLM agent 已有 OpenAI-compatible/DeepSeek 接入路径；默认离线 demo 不依赖 API key，未配置或调用失败时会降级到 deterministic workflow。
- MySQL、Neo4j、Qdrant、PDF/parser 和 OpenAlex 已可通过 CLI/Streamlit runner 配置；默认离线 demo 仍不强依赖这些外部服务，不可用时会显示 warning 并降级。
- PDF 不可用时，Evidence RAG 使用标题和摘要作为 fallback full text；因此证据是摘要级证据，不是页码级 PDF 证据。
- MCP server 文件夹和 stdio 入口存在，但当前 demo 使用进程内 MCP facade 调用。
- 当前目标是保证一个完整可用、可追溯、可离线复现的 demo，后续再把真实外部服务逐步接入验收链路。


