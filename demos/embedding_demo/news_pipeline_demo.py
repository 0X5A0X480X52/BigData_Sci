#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
news_pipeline_excel_aligned_demo.py

Excel/CSV 训练集 -> news*_text 非空单元格分离 -> 文档清洗 -> 中文 parent-child chunk ->
可选 LLM 事件抽取 -> 可选 child embedding。

设计目标：
1) 直接从训练集 Excel 中读取 news*_text 列，记录 row_index/sample_id/label/news_col/text_hash 的对应关系。
2) 假定目标企业名未知；不做“企业名附近句子”筛选，仅做样本相关新闻的弱监督风险句召回。
3) 输出 child_embeddings.npy + child_embedding_index.jsonl，可直接接入 dl_distill_rank_news_fusion_demo.py。

典型命令：
python news_pipeline_excel_aligned_demo.py \
  --input "训练集.xlsx" \
  --out_dir output/news_pipeline_aligned_bge \
  --id_col duty_id \
  --label_col is_replace \
  --embed \
  --embed_backend local \
  --local_embed_model BAAI/bge-base-zh-v1.5 \
  --embed_scope candidates
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import random
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import requests

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):
        return False

try:
    from tqdm import tqdm
except Exception:  # pragma: no cover
    def tqdm(x, **kwargs):
        return x


# =============================================================================
# 1. 规则词表：企业信用风险/违约预测常见新闻线索
# =============================================================================

NEGATIVE_TERMS = [
    "违约", "逾期", "欠款", "债务", "偿债", "流动性", "展期", "兑付", "违约风险",
    "被执行", "失信", "限制高消费", "冻结", "查封", "拍卖", "司法", "诉讼", "仲裁",
    "破产", "重整", "清算", "停产", "停工", "亏损", "下滑", "下降", "资不抵债",
    "处罚", "罚款", "行政处罚", "监管函", "警示函", "问询函", "立案", "调查",
    "质押", "担保", "代偿", "担保责任", "保证责任", "风险提示", "经营困难",
    "裁员", "撤销", "吊销", "注销", "失联", "异常经营", "经营异常",
]

POSITIVE_TERMS = [
    "中标", "签约", "战略合作", "融资", "投资", "增资", "扩产", "投产", "入选",
    "获奖", "补贴", "专项资金", "高新技术", "专利", "上市", "盈利", "增长", "同比增长",
]

FINANCIAL_TERMS = [
    "营业收入", "营收", "净利润", "利润", "现金流", "资产负债率", "资产", "负债",
    "应收账款", "债券", "票据", "贷款", "利率", "授信", "融资租赁", "担保余额",
    "审计", "年报", "半年报", "财务", "偿债能力", "信用评级", "评级", "主体评级",
    "毛利率", "净资产", "所有者权益", "流动资产", "流动负债",
]

NOTICE_TERMS = [
    "公示", "公告", "通告", "名单", "附件", "序号", "单位名称", "企业名称", "项目名称",
    "拟支持", "拟认定", "拟推荐", "入库", "备案", "申报", "准予注册", "统一社会信用代码",
]

BAD_PAGE_RE = re.compile(
    r"云安全平台检测到|访问行为存在异常|触发WAF防护|事件ID|客户端IP|"
    r"HTTP ERROR|页面没有找到|无法处理此请求|404 Not Found|502 Bad Gateway|"
    r"Service Temporarily Unavailable|Access Denied|Forbidden|您当前的访问行为存在异常",
    re.I,
)

BOILERPLATE_PATTERNS = [
    r"扫一扫在手机打开当前页.*$",
    r"站点地图.*?违法和不良信息举报中心.*$",
    r"文章内容仅供参考。?",
    r"投资者据此操作，风险自担。?",
    r"据此操作，风险自担。?",
    r"若需转载本网稿件，请致电[:：]?.*?。",
    r"违反上述声明者，本网将追究其相关法律责任。?",
    r"特别提醒：如果我们使用了您的图片.*?撤下您的作品。?",
    r"免责声明[:：]?.{0,300}?(?:风险自担|仅供参考)。?",
    r"版权声明[:：]?.{0,300}?(?:法律责任|联系我们)。?",
    r"责任编辑[:：][^。；;\n]{0,40}",
    r"编辑[:：][^。；;\n]{0,40}",
    r"来源[:：][^。；;\n]{0,60}",
]
BOILERPLATE_RE = [re.compile(p, re.S) for p in BOILERPLATE_PATTERNS]


# =============================================================================
# 2. 数据结构
# =============================================================================

@dataclass
class NewsCell:
    row_index: int
    sample_id: str
    id_col_name: str
    label: Optional[Any]
    news_col: str
    text_hash: str
    occurrence_id: str
    raw_text: str
    char_len: int
    text_preview: str
    duty_id: Optional[str] = None


@dataclass
class CleanDoc:
    doc_id: str
    row_index: int
    sample_id: str
    id_col_name: str
    label: Optional[Any]
    news_col: str
    text_hash: str
    occurrence_id: str
    source_ref: str
    duty_id: Optional[str]
    raw_len: int
    clean_len: int
    chinese_ratio: float
    is_bad_page: bool
    is_too_short: bool
    is_low_chinese_ratio: bool
    is_notice_like: bool
    is_multi_company_like: bool
    drop_reason: Optional[str]
    clean_text: str


@dataclass
class ParentChunk:
    parent_id: str
    doc_id: str
    row_index: int
    sample_id: str
    id_col_name: str
    label: Optional[Any]
    news_col: str
    text_hash: str
    occurrence_id: str
    source_ref: str
    duty_id: Optional[str]
    parent_index: int
    start_sentence: int
    end_sentence: int
    text_len: int
    parent_text: str


@dataclass
class ChildChunk:
    child_id: str
    parent_id: str
    doc_id: str
    row_index: int
    sample_id: str
    id_col_name: str
    label: Optional[Any]
    news_col: str
    text_hash: str
    occurrence_id: str
    source_ref: str
    duty_id: Optional[str]
    sentence_index: int
    parent_index: int
    center_sentence: str
    child_text: str
    child_len: int
    rule_score: float
    rule_tags: List[str] = field(default_factory=list)
    keyword_hits: Dict[str, List[str]] = field(default_factory=dict)
    amount_count: int = 0
    date_count: int = 0
    is_notice_like: bool = False
    is_multi_company_like: bool = False
    is_candidate: bool = False


# =============================================================================
# 3. IO 与 Excel 新闻单元格分离
# =============================================================================

def normalize_sheet_name(sheet_name: str):
    if isinstance(sheet_name, str) and sheet_name.isdigit():
        return int(sheet_name)
    return sheet_name


def read_table(path: str | Path, sheet_name=0, max_rows: Optional[int] = None) -> pd.DataFrame:
    path = Path(path)
    ext = path.suffix.lower()
    if ext in [".xlsx", ".xls"]:
        df = pd.read_excel(path, sheet_name=normalize_sheet_name(sheet_name), nrows=max_rows)
        if not isinstance(df, pd.DataFrame):
            raise ValueError("sheet_name 请指定单个 sheet，不要传 None。")
        return df
    if ext == ".csv":
        try:
            return pd.read_csv(path, encoding="utf-8-sig", nrows=max_rows)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="gbk", nrows=max_rows)
    raise ValueError(f"只支持 .xlsx/.xls/.csv，当前文件：{path}")


def clean_cell_text(x) -> str:
    if pd.isna(x):
        return ""
    text = str(x)
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sha1_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def short_hash(text: str, n: int = 16) -> str:
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()[:n]


def safe_filename_part(x: Any, max_len: int = 80) -> str:
    s = str(x)
    s = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", s)
    return s[:max_len] if len(s) > max_len else s


def detect_news_cols(df: pd.DataFrame, news_col_regex: str) -> List[str]:
    pattern = re.compile(news_col_regex)
    news_cols = [str(c) for c in df.columns if pattern.match(str(c))]
    if not news_cols:
        raise ValueError(
            f"未找到新闻列。当前正则：{news_col_regex}\n"
            f"前 30 个列名：{list(df.columns[:30])}"
        )

    def news_col_key(c):
        m = re.search(r"news(\d+)_text", str(c))
        return int(m.group(1)) if m else 10**9

    return sorted(news_cols, key=news_col_key)


def auto_id_col(df: pd.DataFrame, user_id_col: Optional[str]) -> Optional[str]:
    if user_id_col:
        if user_id_col not in df.columns:
            raise ValueError(f"指定的 id_col 不存在：{user_id_col}")
        return user_id_col
    for cand in ["duty_id", "id", "ID", "company_id", "enterprise_id", "credit_code", "企业ID"]:
        if cand in df.columns:
            return cand
    return None


def auto_label_col(df: pd.DataFrame, user_label_col: Optional[str]) -> Optional[str]:
    if user_label_col:
        if user_label_col not in df.columns:
            raise ValueError(f"指定的 label_col 不存在：{user_label_col}")
        return user_label_col
    for cand in ["is_replace", "label", "y", "target", "违约", "是否违约"]:
        if cand in df.columns:
            return cand
    return None


def iter_news_cells(
    df: pd.DataFrame,
    news_cols: List[str],
    id_col: Optional[str],
    label_col: Optional[str],
    preview_chars: int = 120,
    max_news_cells: Optional[int] = None,
) -> Tuple[List[NewsCell], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    cells: List[NewsCell] = []
    unique_texts: Dict[str, str] = {}

    pbar = tqdm(df.iterrows(), total=len(df), desc="Scan news cells")
    for row_idx, row in pbar:
        raw_sample_id = row[id_col] if id_col else row_idx
        sample_id = str(raw_sample_id)
        label = row[label_col] if label_col else None
        duty_id = str(row["duty_id"]) if "duty_id" in df.columns and not pd.isna(row["duty_id"]) else None
        if id_col == "duty_id":
            duty_id = sample_id

        for col in news_cols:
            text = clean_cell_text(row[col])
            if not text:
                continue
            h = sha1_text(text)
            unique_texts.setdefault(h, text)
            occurrence_id = f"r{int(row_idx)}_{safe_filename_part(col)}_{h[:10]}"
            cells.append(NewsCell(
                row_index=int(row_idx),
                sample_id=sample_id,
                id_col_name=id_col or "row_index",
                label=None if pd.isna(label) else label,
                news_col=str(col),
                text_hash=h,
                occurrence_id=occurrence_id,
                raw_text=text,
                char_len=len(text),
                text_preview=text[:preview_chars],
                duty_id=duty_id,
            ))
            if max_news_cells is not None and len(cells) >= max_news_cells:
                break
        if max_news_cells is not None and len(cells) >= max_news_cells:
            break

    mapping_rows = []
    for c in cells:
        mapping_rows.append({
            "row_index": c.row_index,
            "sample_id": c.sample_id,
            "id_col_name": c.id_col_name,
            "label": c.label,
            "news_col": c.news_col,
            "text_hash": c.text_hash,
            "occurrence_id": c.occurrence_id,
            "char_len": c.char_len,
            "text_preview": c.text_preview,
            "duty_id": c.duty_id,
            c.id_col_name: c.sample_id,
        })
    cell_df = pd.DataFrame(mapping_rows)

    unique_rows = []
    for h, text in unique_texts.items():
        unique_rows.append({
            "text_hash": h,
            "char_len": len(text),
            "text_preview": text[:preview_chars],
            "occurrence_count": int((cell_df["text_hash"] == h).sum()) if len(cell_df) else 0,
        })
    unique_df = pd.DataFrame(unique_rows).sort_values("occurrence_count", ascending=False) if unique_rows else pd.DataFrame()

    if len(cell_df) > 0:
        sample_summary = (
            cell_df.groupby(["row_index", "sample_id", "label"], dropna=False)
            .agg(
                non_empty_news_count=("news_col", "count"),
                unique_news_count=("text_hash", "nunique"),
                total_chars=("char_len", "sum"),
                mean_chars_per_news=("char_len", "mean"),
                max_chars_single_news=("char_len", "max"),
            )
            .reset_index()
        )
    else:
        sample_summary = pd.DataFrame()

    return cells, cell_df, unique_df, sample_summary


def write_jsonl(path: str | Path, rows: Iterable[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def append_jsonl(path: str | Path, row: Dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


# =============================================================================
# 4. 文档级过滤 + 正文清洗，目标企业名未知版
# =============================================================================

def normalize_raw_text(text: str) -> str:
    text = text.replace("\ufeff", "")
    text = html.unescape(text)
    text = text.replace("\u3000", " ")
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def remove_boilerplate(text: str) -> str:
    for pat in BOILERPLATE_RE:
        text = pat.sub(" ", text)
    return text


def clean_body_text(text: str) -> str:
    text = normalize_raw_text(text)
    text = remove_boilerplate(text)

    # URL / 邮箱 / IP / 长事件 ID
    text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.I)
    text = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", " ", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", " ", text)
    text = re.sub(r"(?<![A-Za-z0-9])[a-fA-F0-9]{20,}(?![A-Za-z0-9])", " ", text)

    # 联系方式密集文本通常是公告尾部/网站尾部，保守删除具体号码，而不是删除整段。
    text = re.sub(r"(?:联系电话|联系方式|咨询电话|电话|传真)[:：]?\s*[0-9\-—\s]{6,30}", " ", text)

    # 企业名未知：不删除名单行，也不按企业名过滤；只在后续用 is_multi_company_like 降权。
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        if line:
            lines.append(line)
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chinese_ratio(text: str) -> float:
    if not text:
        return 0.0
    zh = len(re.findall(r"[\u4e00-\u9fff]", text))
    return zh / max(len(text), 1)


def is_multi_company_like(text: str) -> bool:
    if not text:
        return False
    strong = any(t in text for t in ["企业名称", "单位名称", "统一社会信用代码", "序号", "名单", "公示表"])
    company_like_count = len(re.findall(r"(?:公司|企业|集团|厂|单位)", text))
    return bool(strong and company_like_count >= 5)


def build_clean_doc(
    cell: NewsCell,
    min_chars: int = 80,
    min_chinese_ratio: float = 0.35,
) -> CleanDoc:
    raw_norm = normalize_raw_text(cell.raw_text)
    doc_id = f"doc_{cell.occurrence_id}"

    is_bad = bool(BAD_PAGE_RE.search(raw_norm))
    clean = "" if is_bad else clean_body_text(raw_norm)
    ratio = chinese_ratio(clean)
    is_short = len(clean) < min_chars
    is_low_ratio = ratio < min_chinese_ratio
    notice_like = any(t in clean for t in NOTICE_TERMS)
    multi_company = is_multi_company_like(clean)

    drop_reason = None
    if is_bad:
        drop_reason = "bad_page"
    elif is_short:
        drop_reason = "too_short"
    elif is_low_ratio:
        drop_reason = "low_chinese_ratio"

    return CleanDoc(
        doc_id=doc_id,
        row_index=cell.row_index,
        sample_id=cell.sample_id,
        id_col_name=cell.id_col_name,
        label=cell.label,
        news_col=cell.news_col,
        text_hash=cell.text_hash,
        occurrence_id=cell.occurrence_id,
        source_ref=f"excel://row={cell.row_index}/col={cell.news_col}",
        duty_id=cell.duty_id,
        raw_len=len(raw_norm),
        clean_len=len(clean),
        chinese_ratio=round(ratio, 4),
        is_bad_page=is_bad,
        is_too_short=is_short,
        is_low_chinese_ratio=is_low_ratio,
        is_notice_like=notice_like,
        is_multi_company_like=multi_company,
        drop_reason=drop_reason,
        clean_text=clean,
    )


# =============================================================================
# 5. 中文分句 + parent-child chunk
# =============================================================================

def split_paragraphs(text: str) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
    return [p for p in paras if len(p) >= 4]


def split_zh_sentences(text: str) -> List[str]:
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    # 句号/问号/感叹号/分号；过长列表句再按逗号/顿号切分。
    parts = re.split(r"(?<=[。！？!?；;])\s*", text)
    sents = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > 350:
            sub = re.split(r"(?<=[，,、])\s*", p)
            sents.extend([x.strip() for x in sub if len(x.strip()) >= 4])
        else:
            if len(p) >= 4:
                sents.append(p)
    return sents


def collect_sentences_by_paragraph(clean_text: str) -> List[Dict[str, Any]]:
    records = []
    sent_idx = 0
    for para_idx, para in enumerate(split_paragraphs(clean_text)):
        for sent in split_zh_sentences(para):
            records.append({
                "sentence_index": sent_idx,
                "paragraph_index": para_idx,
                "sentence": sent,
            })
            sent_idx += 1
    return records


def make_parent_chunks(
    doc: CleanDoc,
    sent_records: List[Dict[str, Any]],
    parent_max_chars: int = 1200,
    parent_min_chars: int = 300,
) -> Tuple[List[ParentChunk], Dict[int, str], Dict[int, int]]:
    parents: List[ParentChunk] = []
    sent_to_parent: Dict[int, str] = {}
    sent_to_parent_index: Dict[int, int] = {}
    cur_sents: List[Dict[str, Any]] = []
    cur_len = 0

    def flush():
        nonlocal cur_sents, cur_len
        if not cur_sents:
            return
        parent_index = len(parents)
        start_sentence = cur_sents[0]["sentence_index"]
        end_sentence = cur_sents[-1]["sentence_index"]
        parent_text = "".join(x["sentence"] for x in cur_sents)
        parent_id = f"{doc.doc_id}_p{parent_index:04d}"
        parents.append(ParentChunk(
            parent_id=parent_id,
            doc_id=doc.doc_id,
            row_index=doc.row_index,
            sample_id=doc.sample_id,
            id_col_name=doc.id_col_name,
            label=doc.label,
            news_col=doc.news_col,
            text_hash=doc.text_hash,
            occurrence_id=doc.occurrence_id,
            source_ref=doc.source_ref,
            duty_id=doc.duty_id,
            parent_index=parent_index,
            start_sentence=start_sentence,
            end_sentence=end_sentence,
            text_len=len(parent_text),
            parent_text=parent_text,
        ))
        for x in cur_sents:
            sent_to_parent[x["sentence_index"]] = parent_id
            sent_to_parent_index[x["sentence_index"]] = parent_index
        cur_sents = []
        cur_len = 0

    last_para = None
    for rec in sent_records:
        s = rec["sentence"]
        new_para = rec["paragraph_index"] != last_para
        would_len = cur_len + len(s)
        if cur_sents and (would_len > parent_max_chars or (new_para and cur_len >= parent_min_chars)):
            flush()
        cur_sents.append(rec)
        cur_len += len(s)
        last_para = rec["paragraph_index"]
    flush()
    return parents, sent_to_parent, sent_to_parent_index


def count_patterns(text: str) -> Dict[str, int]:
    amount_re = r"\d+(?:\.\d+)?\s*(?:亿|万|万元|亿元|元|人民币|美元)"
    date_re = r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    return {
        "amount_count": len(re.findall(amount_re, text)),
        "date_count": len(re.findall(date_re, text)),
    }


def hit_terms(text: str, terms: Sequence[str]) -> List[str]:
    return [t for t in terms if t in text]


def score_child(text: str, center_sentence: str, doc_notice_like: bool, doc_multi_company_like: bool) -> Tuple[float, List[str], Dict[str, List[str]], int, int, bool, bool]:
    """
    目标企业名未知版打分：
    - 不使用 company_name/company_mentioned。
    - 对公告/名单/多企业文本不直接删除，只标记并轻微降权。
    - 强风险词、财务词、金额仍保留为高价值候选。
    """
    neg = hit_terms(text, NEGATIVE_TERMS)
    pos = hit_terms(text, POSITIVE_TERMS)
    fin = hit_terms(text, FINANCIAL_TERMS)
    notice = hit_terms(text, NOTICE_TERMS)
    counts = count_patterns(text)
    chunk_multi = is_multi_company_like(text) or doc_multi_company_like
    chunk_notice = bool(notice) or doc_notice_like

    tags = []
    score = 0.0
    if neg:
        tags.append("negative")
        score += 3.0 + min(len(neg), 5) * 0.5
    if fin:
        tags.append("financial")
        score += 1.5 + min(len(fin), 5) * 0.3
    if pos:
        tags.append("positive")
        score += 0.8 + min(len(pos), 5) * 0.15
    if chunk_notice:
        tags.append("notice_like")
        score += 0.2
    if chunk_multi:
        tags.append("multi_company_like")
        score -= 0.3
    if counts["amount_count"] > 0:
        tags.append("amount")
        score += min(counts["amount_count"], 3) * 0.4
    if counts["date_count"] > 0:
        tags.append("date")
        score += min(counts["date_count"], 3) * 0.15

    # 中心句命中比窗口上下文更重要
    if hit_terms(center_sentence, NEGATIVE_TERMS):
        score += 1.0
    if hit_terms(center_sentence, FINANCIAL_TERMS):
        score += 0.5

    # 只有公告/名单而没有风险/财务/金额时，不鼓励进入候选。
    if chunk_notice and not neg and not fin and counts["amount_count"] == 0:
        score -= 0.5

    hits = {"negative": neg, "positive": pos, "financial": fin, "notice": notice}
    return round(max(score, 0.0), 3), sorted(set(tags)), hits, counts["amount_count"], counts["date_count"], chunk_notice, chunk_multi


def make_child_chunks(
    doc: CleanDoc,
    sent_records: List[Dict[str, Any]],
    sent_to_parent: Dict[int, str],
    sent_to_parent_index: Dict[int, int],
    child_window: int = 1,
    child_max_chars: int = 600,
    candidate_score_threshold: float = 2.0,
) -> List[ChildChunk]:
    sents = [x["sentence"] for x in sent_records]
    children: List[ChildChunk] = []
    for i, center in enumerate(sents):
        lo = max(0, i - child_window)
        hi = min(len(sents), i + child_window + 1)
        child_text = "".join(sents[lo:hi])
        if len(child_text) > child_max_chars:
            child_text = child_text[:child_max_chars]

        score, tags, hits, amount_count, date_count, notice_like, multi_company = score_child(
            child_text, center, doc.is_notice_like, doc.is_multi_company_like
        )
        child_id = f"{doc.doc_id}_c{i:04d}"
        parent_id = sent_to_parent.get(i, f"{doc.doc_id}_p_unknown")
        parent_index = sent_to_parent_index.get(i, -1)

        children.append(ChildChunk(
            child_id=child_id,
            parent_id=parent_id,
            doc_id=doc.doc_id,
            row_index=doc.row_index,
            sample_id=doc.sample_id,
            id_col_name=doc.id_col_name,
            label=doc.label,
            news_col=doc.news_col,
            text_hash=doc.text_hash,
            occurrence_id=doc.occurrence_id,
            source_ref=doc.source_ref,
            duty_id=doc.duty_id,
            sentence_index=i,
            parent_index=parent_index,
            center_sentence=center,
            child_text=child_text,
            child_len=len(child_text),
            rule_score=score,
            rule_tags=tags,
            keyword_hits=hits,
            amount_count=amount_count,
            date_count=date_count,
            is_notice_like=notice_like,
            is_multi_company_like=multi_company,
            is_candidate=score >= candidate_score_threshold,
        ))
    return children


# =============================================================================
# 6. LLM / Embedding 后端
# =============================================================================

class OpenAICompatibleClient:
    def __init__(self):
        load_dotenv()
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.chat_model = os.getenv("OPENAI_CHAT_MODEL", "")
        self.embed_model = os.getenv("OPENAI_EMBED_MODEL", "")
        self.timeout = float(os.getenv("OPENAI_TIMEOUT", "60"))
        self.use_json_mode = os.getenv("OPENAI_USE_JSON_MODE", "1") == "1"
        self.max_retries = int(os.getenv("OPENAI_MAX_RETRIES", "3"))
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY 未设置，请在 .env 中配置。")

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = requests.post(url, headers=self._headers(), json=payload, timeout=self.timeout)
                if resp.status_code == 429 or resp.status_code >= 500:
                    time.sleep(min(20, 2 ** attempt + random.random()))
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                last_err = e
                time.sleep(min(20, 2 ** attempt + random.random()))
        raise RuntimeError(f"API 请求失败: {url}; last_err={last_err}")

    def chat_json(self, system_prompt: str, user_prompt: str, temperature: float = 0.0) -> Dict[str, Any]:
        if not self.chat_model:
            raise RuntimeError("OPENAI_CHAT_MODEL 未设置。")
        payload: Dict[str, Any] = {
            "model": self.chat_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        if self.use_json_mode:
            payload["response_format"] = {"type": "json_object"}
        data = self._post("/chat/completions", payload)
        content = data["choices"][0]["message"]["content"]
        return parse_json_from_text(content)

    def embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        if not self.embed_model:
            raise RuntimeError("OPENAI_EMBED_MODEL 未设置。")
        vecs: List[List[float]] = []
        for i in tqdm(range(0, len(texts), batch_size), desc="OpenAI-compatible embedding"):
            batch = texts[i:i + batch_size]
            payload = {"model": self.embed_model, "input": batch}
            data = self._post("/embeddings", payload)
            items = sorted(data["data"], key=lambda x: x.get("index", 0))
            vecs.extend([item["embedding"] for item in items])
        return np.asarray(vecs, dtype=np.float32)


class LocalSentenceTransformerEmbedder:
    def __init__(self, model_name: str, device: str = "auto", cache_dir: str = ""):
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as e:
            raise ImportError(
                "本地 embedding 需要安装：pip install -U sentence-transformers torch"
            ) from e
        kwargs = {}
        if device and device != "auto":
            kwargs["device"] = device
        if cache_dir:
            kwargs["cache_folder"] = cache_dir
        self.model = SentenceTransformer(model_name, **kwargs)

    def embed_texts(self, texts: List[str], batch_size: int = 64) -> np.ndarray:
        emb = self.model.encode(
            texts,
            batch_size=batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )
        return emb.astype(np.float32)


def build_embedder(args):
    if args.embed_backend == "openai":
        return OpenAICompatibleClient()
    if args.embed_backend == "local":
        return LocalSentenceTransformerEmbedder(
            model_name=args.local_embed_model,
            device=args.local_embed_device,
            cache_dir=args.local_embed_cache_dir,
        )
    raise ValueError("embed_backend must be local or openai")


def parse_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, flags=re.S)
    if m:
        return json.loads(m.group(0))
    raise ValueError(f"无法解析 JSON: {text[:300]}")


EVENT_SYSTEM_PROMPT = """你是一个企业信用风险新闻分析助手。你只输出 JSON，不输出解释。"""

EVENT_USER_PROMPT_TEMPLATE = """
下面的新闻片段来自某个已知样本企业的 news*_text 字段，但目标企业名称未知。
因此：
- 不要根据企业名判断“是否提到目标企业”；
- 不要编造目标企业名称；
- 如果片段涉及多个企业、名单、公示表，需标记 multi_company=true 或 ambiguous_subject=true；
- 你只判断该片段本身是否包含可能影响企业信用风险、违约风险、偿债能力或经营状况的信息。

样本ID：{sample_id}
新闻列：{news_col}
规则标签：{rule_tags}
规则命中词：{keyword_hits}

新闻片段：
{child_text}

请严格输出 JSON，字段如下：
{{
  "is_credit_relevant": true/false,
  "ambiguous_subject": true/false,
  "multi_company": true/false,
  "polarity": "negative" | "positive" | "neutral" | "mixed",
  "event_types": [
    "default_or_overdue", "debt_risk", "lawsuit", "enforcement", "penalty",
    "bankruptcy_restructuring", "guarantee_or_pledge", "financial_deterioration",
    "financing", "contract_or_bid", "subsidy_or_award", "notice_or_list", "other"
  ],
  "severity": 0到5的整数,
  "event_date": "YYYY-MM-DD 或 null",
  "amounts": ["文本中出现的重要金额"],
  "evidence_sentences": ["保留最关键的1到3个证据句，必须来自原文"],
  "summary": "用一句话概括该片段对违约预测的可能意义"
}}

要求：
1. 如果只是网页版权、访问异常、无关政策，is_credit_relevant=false。
2. 如果是长名单/公示且无法判断主体，ambiguous_subject=true，event_types 包含 notice_or_list。
3. evidence_sentences 必须从原文复制，不要改写。
""".strip()


def extract_event_with_llm(client: OpenAICompatibleClient, child: ChildChunk) -> Dict[str, Any]:
    user_prompt = EVENT_USER_PROMPT_TEMPLATE.format(
        sample_id=child.sample_id,
        news_col=child.news_col,
        rule_tags=json.dumps(child.rule_tags, ensure_ascii=False),
        keyword_hits=json.dumps(child.keyword_hits, ensure_ascii=False),
        child_text=child.child_text,
    )
    result = client.chat_json(EVENT_SYSTEM_PROMPT, user_prompt, temperature=0.0)
    result.update({
        "child_id": child.child_id,
        "parent_id": child.parent_id,
        "doc_id": child.doc_id,
        "row_index": child.row_index,
        "sample_id": child.sample_id,
        "id_col_name": child.id_col_name,
        "news_col": child.news_col,
        "text_hash": child.text_hash,
        "occurrence_id": child.occurrence_id,
        "duty_id": child.duty_id,
        "rule_score": child.rule_score,
        "rule_tags": child.rule_tags,
    })
    if child.id_col_name:
        result[child.id_col_name] = child.sample_id
    return result


# =============================================================================
# 7. Pipeline 主流程
# =============================================================================

def add_dynamic_id_fields(rec: Dict[str, Any]) -> Dict[str, Any]:
    id_col_name = rec.get("id_col_name")
    sample_id = rec.get("sample_id")
    if id_col_name and sample_id is not None:
        rec[id_col_name] = sample_id
    # 兼容融合脚本 aliases：如果没有 duty_id，也至少有 sample_id。
    if rec.get("duty_id") is None and id_col_name == "duty_id":
        rec["duty_id"] = sample_id
    return rec


def run_pipeline(args: argparse.Namespace) -> None:
    out_dir = ensure_dir(args.out_dir)
    for name in [
        "news_cell_mapping.jsonl", "clean_texts.jsonl", "parents.jsonl", "children.jsonl",
        "llm_events.jsonl", "child_embedding_index.jsonl",
    ]:
        p = out_dir / name
        if p.exists():
            p.unlink()

    print("=" * 90)
    print("Read table and separate news cells")
    print("=" * 90)
    df = read_table(args.input, sheet_name=args.sheet_name, max_rows=args.max_rows)
    news_cols = detect_news_cols(df, args.news_col_regex)
    id_col = auto_id_col(df, args.id_col)
    label_col = auto_label_col(df, args.label_col)

    print(f"Rows: {len(df)}")
    print(f"News columns: {len(news_cols)} ({news_cols[0]} ... {news_cols[-1]})")
    print(f"ID column: {id_col if id_col else 'None -> row_index'}")
    print(f"Label column: {label_col if label_col else 'None'}")

    cells, cell_df, unique_df, sample_summary = iter_news_cells(
        df=df,
        news_cols=news_cols,
        id_col=id_col,
        label_col=label_col,
        preview_chars=args.preview_chars,
        max_news_cells=args.max_news_cells,
    )
    print(f"Non-empty news cells: {len(cells)}")
    print(f"Unique raw news texts: {len(unique_df)}")

    cell_df.to_csv(out_dir / "news_cell_mapping.csv", index=False, encoding="utf-8-sig")
    unique_df.to_csv(out_dir / "unique_news_texts.csv", index=False, encoding="utf-8-sig")
    sample_summary.to_csv(out_dir / "sample_news_summary.csv", index=False, encoding="utf-8-sig")
    write_jsonl(out_dir / "news_cell_mapping.jsonl", cell_df.to_dict("records"))

    if args.save_unique_texts:
        text_dir = ensure_dir(out_dir / "unique_texts")
        seen = {}
        for c in cells:
            if c.text_hash not in seen:
                seen[c.text_hash] = c.raw_text
                with open(text_dir / f"{c.text_hash}.txt", "w", encoding="utf-8") as f:
                    f.write(c.raw_text)

    clean_path = out_dir / "clean_texts.jsonl"
    parent_path = out_dir / "parents.jsonl"
    child_path = out_dir / "children.jsonl"
    event_path = out_dir / "llm_events.jsonl"
    summary_path = out_dir / "summary.json"

    all_children: List[ChildChunk] = []
    counters = {
        "input": args.input,
        "id_col": id_col,
        "label_col": label_col,
        "num_rows": int(len(df)),
        "num_news_text_columns": int(len(news_cols)),
        "num_non_empty_news_cells": int(len(cells)),
        "num_unique_raw_news_texts": int(len(unique_df)),
        "docs_total": 0,
        "docs_kept": 0,
        "docs_dropped": 0,
        "drop_reasons": {},
        "parents": 0,
        "children": 0,
        "candidate_children": 0,
        "llm_events": 0,
    }

    print("=" * 90)
    print("Clean + parent-child chunk")
    print("=" * 90)
    for cell in tqdm(cells, desc="Clean + chunk"):
        counters["docs_total"] += 1
        doc = build_clean_doc(cell, min_chars=args.min_chars, min_chinese_ratio=args.min_chinese_ratio)
        append_jsonl(clean_path, add_dynamic_id_fields(asdict(doc)))

        if doc.drop_reason:
            counters["docs_dropped"] += 1
            counters["drop_reasons"][doc.drop_reason] = counters["drop_reasons"].get(doc.drop_reason, 0) + 1
            continue

        counters["docs_kept"] += 1
        sent_records = collect_sentences_by_paragraph(doc.clean_text)
        if not sent_records:
            continue
        parents, sent_to_parent, sent_to_parent_index = make_parent_chunks(
            doc,
            sent_records,
            parent_max_chars=args.parent_max_chars,
            parent_min_chars=args.parent_min_chars,
        )
        children = make_child_chunks(
            doc,
            sent_records,
            sent_to_parent,
            sent_to_parent_index,
            child_window=args.child_window,
            child_max_chars=args.child_max_chars,
            candidate_score_threshold=args.candidate_score_threshold,
        )
        for p in parents:
            append_jsonl(parent_path, add_dynamic_id_fields(asdict(p)))
        for c in children:
            append_jsonl(child_path, add_dynamic_id_fields(asdict(c)))
        all_children.extend(children)
        counters["parents"] += len(parents)
        counters["children"] += len(children)
        counters["candidate_children"] += sum(1 for c in children if c.is_candidate)

    if args.llm:
        print("=" * 90)
        print("LLM event extraction, target company unknown mode")
        print("=" * 90)
        client = OpenAICompatibleClient()
        by_doc: Dict[str, List[ChildChunk]] = defaultdict(list)
        for c in all_children:
            if c.is_candidate:
                by_doc[c.doc_id].append(c)
        selected: List[ChildChunk] = []
        for chunks in by_doc.values():
            selected.extend(sorted(chunks, key=lambda x: x.rule_score, reverse=True)[:args.max_llm_chunks_per_doc])
        for child in tqdm(selected, desc="LLM event extraction"):
            try:
                append_jsonl(event_path, extract_event_with_llm(client, child))
                counters["llm_events"] += 1
            except Exception as e:
                append_jsonl(event_path, add_dynamic_id_fields({
                    "child_id": child.child_id,
                    "doc_id": child.doc_id,
                    "row_index": child.row_index,
                    "sample_id": child.sample_id,
                    "id_col_name": child.id_col_name,
                    "news_col": child.news_col,
                    "text_hash": child.text_hash,
                    "occurrence_id": child.occurrence_id,
                    "duty_id": child.duty_id,
                    "error": str(e),
                    "rule_score": child.rule_score,
                    "child_text": child.child_text,
                }))

    if args.embed:
        print("=" * 90)
        print(f"Child embedding backend={args.embed_backend}")
        print("=" * 90)
        if args.embed_scope == "all":
            embed_children = all_children
        else:
            embed_children = [c for c in all_children if c.is_candidate]

        texts = [c.child_text for c in embed_children]
        if texts:
            embedder = build_embedder(args)
            emb = embedder.embed_texts(texts, batch_size=args.embed_batch_size)
            np.save(out_dir / "child_embeddings.npy", emb)
            with open(out_dir / "child_embedding_index.jsonl", "w", encoding="utf-8") as f:
                for row_idx, c in enumerate(embed_children):
                    rec = add_dynamic_id_fields(asdict(c))
                    rec["embedding_row"] = row_idx
                    rec["embedding_backend"] = args.embed_backend
                    rec["embedding_model"] = args.local_embed_model if args.embed_backend == "local" else os.getenv("OPENAI_EMBED_MODEL", "")
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counters["embedded_children"] = int(len(embed_children))
            counters["embedding_dim"] = int(emb.shape[1]) if emb.ndim == 2 else None
            counters["embed_scope"] = args.embed_scope
            counters["embed_backend"] = args.embed_backend
        else:
            counters["embedded_children"] = 0
            counters["embedding_dim"] = None

    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(counters, f, ensure_ascii=False, indent=2)

    print("\nDone.")
    print(json.dumps(counters, ensure_ascii=False, indent=2))
    print(f"Output dir: {out_dir}")
    if args.embed:
        print("\nFor dl_distill_rank_news_fusion_demo.py:")
        preferred_key = id_col or "row_index"
        print(f"  --news_embeddings {out_dir / 'child_embeddings.npy'}")
        print(f"  --news_index {out_dir / 'child_embedding_index.jsonl'}")
        print(f"  --data_key_col {preferred_key}")
        print(f"  --news_key_col {preferred_key}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Excel aligned Chinese news cleaning + parent-child chunk + LLM/event embedding demo")
    p.add_argument("--input", required=True, help="输入训练集 Excel/CSV，例如 训练集.xlsx")
    p.add_argument("--out_dir", required=True, help="输出目录")
    p.add_argument("--sheet_name", default=0, help="Excel sheet 名称或序号，默认第 1 个 sheet")
    p.add_argument("--news_col_regex", default=r"^news\d+_text$", help="新闻文本列名正则")
    p.add_argument("--id_col", default=None, help="样本 ID 列，默认自动尝试 duty_id/id/company_id 等")
    p.add_argument("--label_col", default=None, help="标签列，默认自动尝试 is_replace/label/y 等")
    p.add_argument("--preview_chars", type=int, default=120)
    p.add_argument("--max_rows", type=int, default=None)
    p.add_argument("--max_news_cells", type=int, default=None, help="调试用：最多处理多少个非空 news 单元格")
    p.add_argument("--save_unique_texts", action="store_true", help="额外保存 unique_texts/*.txt 便于抽查")

    p.add_argument("--min_chars", type=int, default=80)
    p.add_argument("--min_chinese_ratio", type=float, default=0.35)
    p.add_argument("--child_window", type=int, default=1)
    p.add_argument("--child_max_chars", type=int, default=600)
    p.add_argument("--parent_min_chars", type=int, default=300)
    p.add_argument("--parent_max_chars", type=int, default=1200)
    p.add_argument("--candidate_score_threshold", type=float, default=2.0)

    p.add_argument("--llm", action="store_true", help="启用 OpenAI-compatible LLM 事件抽取")
    p.add_argument("--max_llm_chunks_per_doc", type=int, default=3)

    p.add_argument("--embed", action="store_true", help="启用 child embedding")
    p.add_argument("--embed_scope", choices=["candidates", "all"], default="candidates")
    p.add_argument("--embed_backend", choices=["local", "openai"], default="local")
    p.add_argument("--embed_batch_size", type=int, default=64)
    p.add_argument("--local_embed_model", default=os.getenv("LOCAL_EMBED_MODEL", "BAAI/bge-base-zh-v1.5"))
    p.add_argument("--local_embed_device", default=os.getenv("LOCAL_EMBED_DEVICE", "auto"))
    p.add_argument("--local_embed_cache_dir", default=os.getenv("LOCAL_EMBED_CACHE_DIR", ""))
    return p


if __name__ == "__main__":
    run_pipeline(build_arg_parser().parse_args())
