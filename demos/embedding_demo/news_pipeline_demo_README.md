# Excel 对齐版新闻清洗与 embedding demo

本 demo 将原来的 `unique_texts.zip` 纯文本模式改为 **Excel/CSV 样本对齐模式**：直接从训练集的 `news*_text` 列读取新闻，并保留 `duty_id / sample_id / row_index / news_col / text_hash / child_id / embedding_row` 映射，便于后续接入 `dl_distill_rank_news_fusion_demo.py`。

## 1. 安装依赖

基础清洗：

```bash
pip install pandas openpyxl numpy tqdm requests python-dotenv
```

本地 BGE embedding：

```bash
pip install -U sentence-transformers torch
```

如需 LLM 事件抽取，复制 `.env.news_pipeline.example` 为 `.env` 并填写 OpenAI-compatible API 参数。

## 2. 只做文本分离、清洗、chunk

```bash
python news_pipeline_excel_aligned_demo.py ^
  --input "训练集.xlsx" ^
  --out_dir output/news_pipeline_aligned_debug ^
  --id_col duty_id ^
  --label_col is_replace ^
  --max_news_cells 200
```

输出包括：

- `news_cell_mapping.csv/jsonl`：每个非空 `news*_text` 单元格一行，记录样本和文本对应关系。
- `unique_news_texts.csv`：去重后的原始新闻文本统计。
- `sample_news_summary.csv`：样本级新闻数量和长度统计。
- `clean_texts.jsonl`：清洗后的文档。
- `parents.jsonl`：parent chunk，上下文块。
- `children.jsonl`：child chunk，中心句 + 前后窗口，用于候选召回和 embedding。
- `summary.json`：全局统计。

## 3. 使用 BGE 本地 embedding

```bash
python news_pipeline_demo_files_2/news_pipeline_excel_aligned_demo.py
  --input "对抗实验数据集/测试集.xlsx"
  --out_dir output/news_pipeline_aligned_bge_test
  --id_col duty_id
  --embed
  --embed_backend local
  --local_embed_model BAAI/bge-base-zh-v1.5
  --embed_scope all
  --embed_batch_size 64
```

如果 Hugging Face 下载较慢，可以先通过 ModelScope 或其他方式下载到本地，然后：

```bash
python news_pipeline_excel_aligned_demo.py ^
  --input "训练集.xlsx" ^
  --out_dir output/news_pipeline_aligned_bge ^
  --id_col duty_id ^
  --label_col is_replace ^
  --embed ^
  --embed_backend local ^
  --local_embed_model "C:/models/bge-base-zh-v1.5" ^
  --embed_scope candidates
```

## 4. 使用 LLM 事件抽取

目标企业名未知时，LLM prompt 不会要求模型判断“是否提到目标企业”，而是判断该新闻片段本身是否包含信用风险/经营风险/财务风险信息，并对多主体、名单、公示文本标记 `multi_company` 和 `ambiguous_subject`。

```bash
python news_pipeline_excel_aligned_demo.py ^
  --input "训练集.xlsx" ^
  --out_dir output/news_pipeline_aligned_llm_bge ^
  --id_col duty_id ^
  --label_col is_replace ^
  --llm ^
  --max_llm_chunks_per_doc 3 ^
  --embed ^
  --embed_backend local ^
  --local_embed_model BAAI/bge-base-zh-v1.5 ^
  --embed_scope candidates
```

## 5. 与 `dl_distill_rank_news_fusion_demo.py` 联动

第一步，生成新闻 embedding：

```bash
python news_pipeline_excel_aligned_demo.py ^
  --input output/eda_v2_result/model_data_clean.csv ^
  --out_dir output/news_pipeline_aligned_bge ^
  --id_col duty_id ^
  --label_col is_replace ^
  --embed ^
  --embed_backend local ^
  --local_embed_model BAAI/bge-base-zh-v1.5 ^
  --embed_scope candidates
```

第二步，运行新闻融合蒸馏实验：

```bash
python dl_distill_rank_news_fusion_demo.py ^
  --input output/eda_v2_result/model_data_clean.csv ^
  --output_dir output/dl_distill_news_fusion_bge ^
  --feature_set structured_news_stats ^
  --news_embeddings output/news_pipeline_aligned_bge/child_embeddings.npy ^
  --news_index output/news_pipeline_aligned_bge/child_embedding_index.jsonl ^
  --data_key_col duty_id ^
  --news_key_col duty_id ^
  --max_news_chunks 32 ^
  --news_select top_rule_score ^
  --news_require_candidate ^
  --fusion_modes structured,concat,gated,cross_attention ^
  --student_modes mlp_base,mlp_distill_rank ^
  --teacher_repeats 2 ^
  --student_repeats 2 ^
  --student_seeds 42,2024 ^
  --epochs 60
```

如果没有 `duty_id`，但你指定了其他 ID 列，例如 `company_id`，则两边都改成：

```bash
--id_col company_id
```

以及融合脚本：

```bash
--data_key_col company_id --news_key_col company_id
```

## 6. 输出对齐要求

`child_embedding_index.jsonl` 每条记录都会包含：

```json
{
  "embedding_row": 0,
  "duty_id": "样本ID",
  "sample_id": "样本ID",
  "row_index": 0,
  "news_col": "news1_text",
  "text_hash": "...",
  "child_id": "...",
  "parent_id": "...",
  "rule_score": 4.8,
  "is_candidate": true,
  "child_text": "..."
}
```

`dl_distill_rank_news_fusion_demo.py` 会根据 `--data_key_col` 和 `--news_key_col` 把这些 child embeddings 聚合回每个训练样本。

## 7. 企业名未知模式的变化

相比企业名已知版本，本脚本：

- 不使用 `company_name`。
- 不做企业名附近句子筛选。
- 主要依靠负面词、财务词、金额、公告/名单标记召回候选 child。
- 对长名单/公示/多企业文本不直接删除，而是标记 `is_multi_company_like=true`，并在规则分数上轻微降权。
- LLM prompt 明确目标企业名未知，要求输出 `multi_company` / `ambiguous_subject`，防止错误主体归因。

这仍然足以完成“已知哪些新闻属于哪个 duty_id”的样本级新闻 embedding 融合实验。
