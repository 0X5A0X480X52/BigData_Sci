#!/usr/bin/env python
"""Export research-agent MySQL tables to CSV files.

This script is read-only: it does not run OpenAlex ELT, does not clean data,
and does not write to MySQL. It connects to an existing MySQL database and
exports selected project tables to one CSV file per table.

Example:
    python scripts\\export_mysql_tables_to_csv.py ^
      --mysql-host localhost ^
      --mysql-user root ^
      --mysql-password <password> ^
      --mysql-database research_agent ^
      --output-dir outputs\\mysql_csv_export\\research_agent
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


DEFAULT_TABLES: List[str] = [
    "analysis_runs",
    "analysis_tasks",
    "mcp_tool_calls",
    "works",
    "authors",
    "institutions",
    "venues",
    "concepts",
    "countries",
    "work_types",
    "work_authors",
    "author_institutions",
    "work_institutions",
    "work_author_affiliations",
    "work_concepts",
    "work_venues",
    "citations",
    "external_work_refs",
    "analysis_corpora",
    "corpus_membership",
    "crawl_frontier",
    "crawl_jobs",
    "graph_snapshots",
    "graph_nodes",
    "graph_edges",
    "graph_algorithm_runs",
    "materialization_jobs",
    "paper_files",
    "chunk_runs",
    "embedding_runs",
]

TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

TABLE_DESCRIPTIONS: Dict[str, str] = {
    "analysis_runs": "一次 Agent 或 OpenAlex ELT 调研运行的主记录。",
    "analysis_tasks": "Agent Planner-Executor 模式下的任务节点与执行状态。",
    "mcp_tool_calls": "MCP 工具调用记录，包括 provider、tool、summary、warning 和错误信息。",
    "works": "OpenAlex work/论文实体表，保存标题、摘要、年份、引用数、DOI 等核心信息。",
    "authors": "OpenAlex author/作者实体表。",
    "institutions": "OpenAlex institution/机构实体表。",
    "venues": "OpenAlex venue/source/期刊或会议来源实体表。",
    "concepts": "OpenAlex concept/topic 概念实体表。",
    "countries": "国家或地区字典表，主要用于机构归属。",
    "work_types": "OpenAlex work 类型字典表，例如 article、book-chapter 等。",
    "work_authors": "论文和作者的二元关系表，保留作者顺序和通讯作者标记。",
    "author_institutions": "作者和机构的全局二元关系表。",
    "work_institutions": "论文和机构的二元关系表，表示该论文出现过的机构。",
    "work_author_affiliations": "作者在某篇论文中的机构上下文表，用于回答某论文某作者挂靠哪些机构。",
    "work_concepts": "论文和概念/主题的关系表，包含 OpenAlex score。",
    "work_venues": "论文和发表来源的关系表。",
    "citations": "论文引用关系表，保存 citing/cited OpenAlex ID 和可选内部 FK。",
    "external_work_refs": "引用中出现但尚未完整入库的外部 work 引用。",
    "analysis_corpora": "一次调研构建的文献集合/corpus 元数据。",
    "corpus_membership": "corpus 与 work 的成员关系。",
    "crawl_frontier": "引用扩展/BFS 抓取 frontier 状态。",
    "crawl_jobs": "抓取任务级状态记录。",
    "graph_snapshots": "基于 corpus 构建的图快照元数据。",
    "graph_nodes": "图快照中的节点明细。",
    "graph_edges": "图快照中的边明细。",
    "graph_algorithm_runs": "图算法运行记录，例如 PageRank、community、bridge score。",
    "materialization_jobs": "PDF 或全文 materialization 任务状态。",
    "paper_files": "论文 PDF 文件存储与 SHA-256 去重记录。",
    "chunk_runs": "Parent/Child chunk 切分和 embedding 执行记录。",
    "embedding_runs": "Embedding 批处理运行记录。",
}

COLUMN_DESCRIPTIONS: Dict[str, str] = {
    "run_id": "运行 ID。",
    "question": "用户问题、检索主题或调研目标。",
    "config_json": "运行配置 JSON。",
    "agent_mode": "Agent 运行模式，例如 react 或 planner_executor。",
    "status": "状态，例如 created、running、completed、failed。",
    "trace_json": "运行 trace JSON。",
    "artifacts_json": "运行产生的 Artifact 引用 JSON。",
    "task_results_json": "任务结果 JSON 汇总。",
    "created_at": "创建时间。",
    "updated_at": "更新时间。",
    "completed_at": "完成时间。",
    "task_id": "任务 ID。",
    "skill": "Skill 名称。",
    "title": "标题或名称。",
    "depends_on_json": "依赖任务列表 JSON。",
    "parameters_json": "任务参数 JSON。",
    "retries": "重试次数。",
    "result_json": "结果 JSON。",
    "error": "错误信息。",
    "started_at": "开始时间。",
    "tool_call_id": "MCP 工具调用 ID。",
    "provider": "MCP provider 名称。",
    "tool": "工具名称。",
    "args_json": "工具参数 JSON。",
    "result_type": "结果类型。",
    "summary_json": "结果摘要 JSON。",
    "preview_json": "结果预览 JSON。",
    "artifact_id": "关联 Artifact ID。",
    "warnings_json": "warning 列表 JSON。",
    "called_at": "调用时间。",
    "duration_ms": "耗时，毫秒。",
    "work_id": "内部 work 自增主键或外部 work ID，视表定义而定。",
    "work_id_fk": "works.work_id 内部外键。",
    "openalex_id": "OpenAlex 源 ID。",
    "doi": "DOI。",
    "abstract": "摘要文本。",
    "publication_year": "发表年份。",
    "cited_by_count": "OpenAlex 引用次数。",
    "type_id": "work_types.type_id 外键。",
    "primary_venue_id": "venues.venue_id 主发表来源外键。",
    "open_access_pdf_url": "Open access PDF URL。",
    "source": "数据来源或关系来源。",
    "raw_json": "原始数据 JSON。",
    "author_id": "内部 author 自增主键或外部 author ID，视表定义而定。",
    "author_id_fk": "authors.author_id 内部外键。",
    "author_openalex_id": "作者 OpenAlex ID。",
    "orcid": "作者 ORCID。",
    "display_name": "显示名称。",
    "institution_id": "内部 institution 自增主键或外部 institution ID，视表定义而定。",
    "institution_id_fk": "institutions.institution_id 内部外键。",
    "institution_openalex_id": "机构 OpenAlex ID。",
    "ror": "机构 ROR ID。",
    "type": "类型。",
    "country_code": "国家或地区代码。",
    "venue_id": "内部 venue 自增主键或外部 venue ID，视表定义而定。",
    "venue_id_fk": "venues.venue_id 内部外键。",
    "venue_openalex_id": "来源 OpenAlex ID。",
    "issn_l": "ISSN-L。",
    "issn": "ISSN。",
    "issn_json": "ISSN 列表 JSON。",
    "publisher": "出版方。",
    "is_open_access": "是否开放获取。",
    "concept_id": "内部 concept 自增主键或外部 concept ID，视表定义而定。",
    "concept_id_fk": "concepts.concept_id 内部外键。",
    "concept_openalex_id": "概念 OpenAlex ID。",
    "level": "OpenAlex 概念层级。",
    "type_name": "work 类型名称。",
    "work_author_id": "work_authors 关系内部主键。",
    "work_openalex_id": "论文 OpenAlex ID。",
    "author_order": "作者顺序。",
    "is_corresponding": "是否通讯作者。",
    "raw_author_position_json": "OpenAlex authorship position 原始 JSON。",
    "relationship_source": "关系来源。",
    "first_seen_at": "首次观察到该关系的时间。",
    "last_seen_at": "最近观察到该关系的时间。",
    "raw_affiliation_string": "原始 affiliation 字符串。",
    "score": "关系或算法分数。",
    "is_primary": "是否主来源。",
    "citation_id": "引用关系内部主键。",
    "citing_work_id_fk": "引用方 works.work_id 外键。",
    "cited_work_id_fk": "被引方 works.work_id 外键。",
    "citing_work_openalex_id": "引用方 OpenAlex work ID。",
    "cited_work_openalex_id": "被引方 OpenAlex work ID。",
    "first_seen_from": "首次发现该外部引用的来源 work。",
    "corpus_id": "Corpus ID。",
    "query": "检索词或 corpus 查询。",
    "query_hash": "查询 hash，用于幂等。",
    "paper_count": "论文数量。",
    "data_cutoff": "数据截止时间。",
    "added_at": "加入时间。",
    "depth": "BFS 深度。",
    "attempted_at": "尝试时间。",
    "graph_snapshot_id": "图快照 ID。",
    "algorithm_version": "算法版本。",
    "node_count": "节点数量。",
    "edge_count": "边数量。",
    "node_types_json": "节点类型统计 JSON。",
    "edge_types_json": "边类型统计 JSON。",
    "algo_run_id": "图算法运行 ID。",
    "algorithm": "算法名称。",
    "results_json": "算法结果 JSON。",
    "node_id": "图节点 ID。",
    "node_type": "图节点类型。",
    "label": "节点标签。",
    "properties_json": "属性 JSON。",
    "edge_id": "图边 ID。",
    "source_node_id": "源节点 ID。",
    "target_node_id": "目标节点 ID。",
    "edge_type": "边类型。",
    "weight": "边权重。",
    "job_id": "任务 ID。",
    "pdf_url": "PDF URL。",
    "pdf_sha256": "PDF SHA-256。",
    "parser_name": "解析器名称。",
    "page_count": "页数。",
    "storage_key": "文件存储路径或对象键。",
    "file_size_bytes": "文件大小，字节。",
    "downloaded_at": "下载时间。",
    "chunk_run_id": "chunk 运行 ID。",
    "embedder_backend": "Embedding 后端。",
    "embedder_model": "Embedding 模型。",
    "parent_count": "Parent chunk 数量。",
    "child_count": "Child chunk 数量。",
    "vector_dim": "向量维度。",
    "embedding_run_id": "Embedding 运行 ID。",
    "total_chunks": "总 chunk 数。",
    "storage_path": "向量或中间结果存储路径。",
    "job_type": "任务类型。",
    "total_works": "总 work 数。",
    "completed_works": "完成 work 数。",
    "failed_works": "失败 work 数。",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export research-agent MySQL tables to CSV files.",
    )
    parser.add_argument("--mysql-host", default="localhost")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-user", default="research_agent")
    parser.add_argument("--mysql-password", default="")
    parser.add_argument("--mysql-database", default="research_agent")
    parser.add_argument(
        "--output-dir",
        default="",
        help="CSV output directory. Defaults to outputs/mysql_csv_export/<timestamp>.",
    )
    parser.add_argument(
        "--tables",
        default="",
        help="Comma-separated table names. Defaults to the project table allowlist.",
    )
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument(
        "--encoding",
        default="utf-8-sig",
        help="CSV encoding. utf-8-sig is convenient for Excel.",
    )
    parser.add_argument(
        "--fail-on-missing-table",
        action="store_true",
        help="Fail if a requested table does not exist.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    tables = parse_tables(args.tables)
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import pymysql
    except ImportError as exc:
        raise SystemExit("pymysql is required. Install with: pip install pymysql") from exc

    manifest: Dict[str, Any] = {
        "database": args.mysql_database,
        "host": args.mysql_host,
        "port": args.mysql_port,
        "exported_at": utc_now_iso(),
        "output_dir": str(output_dir),
        "exported_tables": [],
        "skipped_tables": [],
        "total_rows": 0,
        "warnings": [],
    }

    conn = pymysql.connect(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
        database=args.mysql_database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.SSCursor,
    )
    try:
        with conn.cursor() as cursor:
            for table in tables:
                if not is_safe_table_name(table):
                    message = f"Unsafe table name skipped: {table}"
                    handle_skip(manifest, table, message, args.fail_on_missing_table)
                    continue

                if not table_exists(conn, table):
                    message = f"Table does not exist: {table}"
                    handle_skip(manifest, table, message, args.fail_on_missing_table)
                    continue

                row_count, columns = export_table(
                    cursor=cursor,
                    table=table,
                    output_path=output_dir / f"{table}.csv",
                    batch_size=max(1, args.batch_size),
                    encoding=args.encoding,
                )
                manifest["exported_tables"].append(
                    {
                        "table": table,
                        "csv": f"{table}.csv",
                        "rows": row_count,
                        "columns": columns,
                    }
                )
                manifest["total_rows"] += row_count
                print(f"[csv-export] {table}: {row_count} rows")
    finally:
        conn.close()

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    markdown_path = output_dir / "schema_description.md"
    markdown_path.write_text(build_schema_markdown(manifest), encoding="utf-8")
    print(f"[csv-export] manifest: {manifest_path}")
    print(f"[csv-export] schema description: {markdown_path}")


def parse_tables(raw: str) -> List[str]:
    if not raw.strip():
        return list(DEFAULT_TABLES)
    tables = [item.strip() for item in raw.split(",") if item.strip()]
    return unique_preserve_order(tables)


def resolve_output_dir(raw: str) -> Path:
    if raw.strip():
        return Path(raw)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    return Path("outputs") / "mysql_csv_export" / stamp


def is_safe_table_name(table: str) -> bool:
    return bool(TABLE_NAME_RE.match(table))


def table_exists(conn: Any, table: str) -> bool:
    with conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE %s", (table,))
        return cursor.fetchone() is not None


def export_table(
    cursor: Any,
    table: str,
    output_path: Path,
    batch_size: int,
    encoding: str,
) -> Tuple[int, List[str]]:
    cursor.execute(f"SELECT * FROM `{table}`")
    columns = [item[0] for item in cursor.description or []]
    rows_written = 0

    with output_path.open("w", newline="", encoding=encoding) as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                writer.writerow([to_csv_value(value) for value in row])
            rows_written += len(rows)

    return rows_written, columns


def build_schema_markdown(manifest: Dict[str, Any]) -> str:
    lines: List[str] = [
        "# MySQL CSV 导出数据字典",
        "",
        "本文档随 CSV 导出自动生成，用于说明每个数据表的用途和实际导出的字段含义。",
        "",
        "## 导出概览",
        "",
        f"- 数据库：`{manifest.get('database', '')}`",
        f"- 主机：`{manifest.get('host', '')}`",
        f"- 导出时间：`{manifest.get('exported_at', '')}`",
        f"- 输出目录：`{manifest.get('output_dir', '')}`",
        f"- 导出表数：{len(manifest.get('exported_tables', []))}",
        f"- 总行数：{manifest.get('total_rows', 0)}",
        "",
    ]

    skipped = manifest.get("skipped_tables", [])
    if skipped:
        lines.extend(["## 跳过的表", ""])
        for item in skipped:
            lines.append(f"- `{item.get('table', '')}`：{item.get('reason', '')}")
        lines.append("")

    lines.extend(["## 表与字段说明", ""])
    for item in manifest.get("exported_tables", []):
        table = item.get("table", "")
        columns = item.get("columns", [])
        lines.extend(
            [
                f"### `{table}`",
                "",
                TABLE_DESCRIPTIONS.get(table, "项目相关 MySQL 数据表。"),
                "",
                f"- CSV 文件：`{item.get('csv', '')}`",
                f"- 行数：{item.get('rows', 0)}",
                "",
                "| 字段 | 含义 |",
                "|---|---|",
            ]
        )
        if columns:
            for column in columns:
                description = COLUMN_DESCRIPTIONS.get(column, "未在脚本中预置说明，请参考 MySQL DDL 或源数据。")
                lines.append(f"| `{column}` | {description} |")
        else:
            lines.append("| `(无字段)` | 表存在但未返回字段。 |")
        lines.append("")

    return "\n".join(lines)


def to_csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def handle_skip(
    manifest: Dict[str, Any],
    table: str,
    message: str,
    fail_on_missing_table: bool,
) -> None:
    if fail_on_missing_table:
        raise SystemExit(message)
    manifest["skipped_tables"].append({"table": table, "reason": message})
    manifest["warnings"].append(message)
    print(f"[csv-export] skipped {table}: {message}", file=sys.stderr)


def unique_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


if __name__ == "__main__":
    main()
