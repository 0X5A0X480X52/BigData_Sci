"""Streamlit workspace for the research agent MVP."""

from __future__ import annotations

import importlib
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    import streamlit as st
except Exception as exc:  # pragma: no cover
    raise SystemExit("Install UI dependencies with: pip install -e .[ui]") from exc

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import research_agent.runtime.runner as runner_module

runner_module = importlib.reload(runner_module)
ResearchRunOptions = runner_module.ResearchRunOptions
ResearchWorkflowResult = runner_module.ResearchWorkflowResult
run_research_workflow = runner_module.run_research_workflow


def main() -> None:
    _load_env_file(ROOT / ".env")
    st.set_page_config(page_title="Research Agent Workbench", layout="wide")
    st.title("Research Agent Workbench")

    options, question, seed_work_id, run_button = _sidebar_options()
    if run_button:
        result = _run_workflow_stream(options, question, seed_work_id)
        st.session_state["workflow_result"] = result

    result: ResearchWorkflowResult | None = st.session_state.get("workflow_result")
    if result is None:
        st.info("Configure the workflow in the sidebar and start a run.")
        return

    run = result.run
    run_root = Path(run.config.artifact_root) / run.run_id
    _status_strip(result, run_root)

    tabs = st.tabs(["Report", "Plan", "Corpus", "Graph", "Evidence", "Artifacts", "Run Trace"])
    with tabs[0]:
        _render_report(run_root)
    with tabs[1]:
        _render_plan(run_root, run)
    with tabs[2]:
        _render_corpus(run_root)
    with tabs[3]:
        _render_graph(run_root, result)
    with tabs[4]:
        _render_evidence(run_root)
    with tabs[5]:
        _render_artifacts(run_root)
    with tabs[6]:
        _render_trace(result)



def _run_workflow_stream(options: ResearchRunOptions, question: str, seed_work_id: str) -> ResearchWorkflowResult:
    events: "queue.Queue[tuple[str, Any]]" = queue.Queue()

    def emit(event: Dict[str, Any]) -> None:
        events.put(("event", event))

    def worker() -> None:
        try:
            result = run_research_workflow(
                options,
                question,
                seed_work_id=seed_work_id or None,
                event_callback=emit,
            )
            events.put(("result", result))
        except Exception as exc:
            events.put(("error", exc))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    status = st.status("Running research workflow", expanded=True)
    query_placeholder = st.empty()
    metrics_placeholder = st.empty()
    table_placeholder = st.empty()
    event_rows: List[Dict[str, Any]] = []
    result: ResearchWorkflowResult | None = None

    while True:
        try:
            kind, payload = events.get(timeout=0.25)
        except queue.Empty:
            if not thread.is_alive():
                break
            time.sleep(0.05)
            continue

        if kind == "event":
            event = payload
            event_rows.append(_stream_event_row(event))
            if event.get("type") == "openalex_query_planned":
                query_placeholder.info(
                    f"OpenAlex query: {event.get('primary_query', '')} | method: {event.get('method', '')}"
                )
            metrics_placeholder.metric("Stream events", len(event_rows))
            table_placeholder.dataframe(_safe_table_rows(event_rows[-80:]), width="stretch")
            status.write(_format_stream_event(event))
        elif kind == "result":
            result = payload
            status.update(label=f"Workflow finished: {result.run.status}", state="complete", expanded=False)
            break
        elif kind == "error":
            status.update(label="Workflow failed", state="error", expanded=True)
            raise payload

    thread.join(timeout=1)
    if result is None:
        raise RuntimeError("Workflow ended without returning a result")
    return result


def _stream_event_row(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "time": event.get("time", ""),
        "type": event.get("type", ""),
        "provider": event.get("provider", event.get("service", "")),
        "tool": event.get("tool", ""),
        "status": event.get("status", ""),
        "detail": _format_stream_event(event),
    }


def _format_stream_event(event: Dict[str, Any]) -> str:
    event_type = event.get("type", "event")
    if event_type == "openalex_query_planned":
        return f"Planned OpenAlex query: {event.get('primary_query', '')} ({event.get('method', '')})"
    if event_type == "service_checked":
        status = event.get("status", {})
        return f"Checked {event.get('service')}: {status.get('status', status)}"
    if event_type == "tool_call":
        args = event.get("args", {}) or {}
        query = args.get("query") if isinstance(args, dict) else ""
        suffix = f" query={query}" if query else ""
        return f"Calling {event.get('provider')}.{event.get('tool')}{suffix}"
    if event_type == "tool_result":
        return f"{event.get('provider')}.{event.get('tool')} -> {event.get('status')}"
    if event_type in {"task_started", "task_completed", "task_failed"}:
        return f"{event_type}: {event.get('task_id', '')} {event.get('skill', '')}"
    if event_type == "workflow_completed":
        return f"Workflow completed: {event.get('status')}"
    return event_type


def _sidebar_options() -> tuple[ResearchRunOptions, str, str, bool]:
    with st.sidebar:
        st.header("Research")
        question = st.text_area("Research question", "retrieval augmented generation for scientific discovery", height=110)
        seed_work_id = st.text_input("Seed OpenAlex Work ID", "")
        agent_mode = st.selectbox("Agent mode", ["react", "planner_executor"], index=0)
        artifact_root = st.text_input("Artifact root", "artifacts")
        max_field_corpus = st.slider("Corpus cap", 10, 500, 30)
        max_key_papers = st.slider("Key papers", 5, 30, 10)
        max_pdfs = st.slider("PDF/materialization cap", 1, 20, 5)

        st.header("Data Provider")
        provider = st.selectbox("Provider", ["fixture", "openalex"], index=0)
        openalex_email = st.text_input("OpenAlex email", os.getenv("RA_OPENALEX_EMAIL", ""))
        openalex_cache_dir = st.text_input("OpenAlex cache", os.getenv("RA_OPENALEX_CACHE_DIR", ".cache/openalex"))

        st.header("Agent")
        llm_backend = st.selectbox("LLM", ["off", "deepseek", "openai-compatible"], index=0)
        llm_query = st.checkbox("LLM OpenAlex query rewrite", value=llm_backend != "off", disabled=llm_backend == "off")
        llm_plan = st.checkbox("LLM-generated plan", value=False, disabled=llm_backend == "off")
        llm_react = st.checkbox("LLM ReAct action selection", value=False, disabled=llm_backend == "off")
        llm_report_writer = st.checkbox("LLM report writer", value=False, disabled=llm_backend == "off")
        default_base = "https://api.deepseek.com" if llm_backend == "deepseek" else os.getenv("RA_LLM_BASE_URL", "")
        default_model = "deepseek-chat" if llm_backend == "deepseek" else os.getenv("RA_LLM_MODEL", "")
        llm_base_url = st.text_input("LLM base URL", default_base, disabled=llm_backend == "off")
        llm_model = st.text_input("LLM model", default_model, disabled=llm_backend == "off")
        llm_api_key = st.text_input("LLM API key", "", type="password", disabled=llm_backend == "off")

        st.header("Storage")
        persistence_backend = st.selectbox("Persistence", ["local", "mysql"], index=0)
        mysql_host = st.text_input("MySQL host", os.getenv("RA_MYSQL_HOST", "localhost"), disabled=persistence_backend == "local")
        mysql_port = st.number_input("MySQL port", min_value=1, max_value=65535, value=int(os.getenv("RA_MYSQL_PORT", "3306")), disabled=persistence_backend == "local")
        mysql_user = st.text_input("MySQL user", os.getenv("RA_MYSQL_USER", "research_agent"), disabled=persistence_backend == "local")
        mysql_password = st.text_input("MySQL password", "", type="password", disabled=persistence_backend == "local")
        mysql_database = st.text_input("MySQL database", os.getenv("RA_MYSQL_DATABASE", "research_agent"), disabled=persistence_backend == "local")
        mysql_init_schema = st.checkbox("Initialize MySQL schema", value=False, disabled=persistence_backend == "local")

        st.header("Evidence")
        enable_pdf = st.checkbox("Try PDF download + parsing", value=False)
        parser_preference = st.selectbox("Parser preference", ["pymupdf", "pypdf", "raw"], index=0, disabled=not enable_pdf)
        vector_backend = st.selectbox("Vector store", ["local_numpy", "qdrant"], index=0)
        qdrant_url = st.text_input("Qdrant URL", os.getenv("RA_QDRANT_URL", "http://localhost:6333"), disabled=vector_backend != "qdrant")
        qdrant_collection = st.text_input("Qdrant collection", "research_evidence", disabled=vector_backend != "qdrant")
        vector_storage_dir = st.text_input("Local vector storage", "artifacts/vectors")

        st.header("Graph Sync")
        neo4j_sync = st.checkbox("Sync graph snapshot to Neo4j", value=False)
        neo4j_uri = st.text_input("Neo4j URI", os.getenv("RA_NEO4J_URI", "bolt://localhost:7687"), disabled=not neo4j_sync)
        neo4j_user = st.text_input("Neo4j user", os.getenv("RA_NEO4J_USER", "neo4j"), disabled=not neo4j_sync)
        neo4j_password = st.text_input("Neo4j password", "", type="password", disabled=not neo4j_sync)
        neo4j_database = st.text_input("Neo4j database", os.getenv("RA_NEO4J_DATABASE", "neo4j"), disabled=not neo4j_sync)
        es_sync = st.checkbox("Best-effort Elasticsearch sync", value=False)

        run_button = st.button("Run workflow", type="primary")

    options = ResearchRunOptions(
        provider=provider,
        openalex_email=openalex_email,
        openalex_cache_dir=openalex_cache_dir,
        agent_mode=agent_mode,
        artifact_root=artifact_root,
        max_field_corpus=max_field_corpus,
        max_key_papers=max_key_papers,
        max_pdfs=max_pdfs,
        seed_work_id=seed_work_id,
        llm_react=llm_backend != "off" and llm_react,
        llm_plan=llm_backend != "off" and llm_plan,
        llm_query=llm_backend != "off" and llm_query,
        llm_report_writer=llm_backend != "off" and llm_report_writer,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
        persistence_backend=persistence_backend,
        mysql_host=mysql_host,
        mysql_port=int(mysql_port),
        mysql_user=mysql_user,
        mysql_password=mysql_password or os.getenv("RA_MYSQL_PASSWORD", ""),
        mysql_database=mysql_database,
        mysql_init_schema=mysql_init_schema,
        enable_pdf=enable_pdf,
        parser_preference=parser_preference,
        vector_backend=vector_backend,
        qdrant_url=qdrant_url,
        qdrant_collection=qdrant_collection,
        vector_storage_dir=vector_storage_dir,
        neo4j_sync=neo4j_sync,
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password or os.getenv("RA_NEO4J_PASSWORD", ""),
        neo4j_database=neo4j_database,
        es_sync=es_sync,
    )
    return options, question, seed_work_id, run_button


def _status_strip(result: ResearchWorkflowResult, run_root: Path) -> None:
    run = result.run
    cols = st.columns(5)
    cols[0].metric("Status", run.status)
    cols[1].metric("Mode", run.agent_mode)
    cols[2].metric("Trace events", len(run.trace))
    cols[3].metric("Tool calls", len([e for e in run.trace if e.get("type") == "tool_call"]))
    cols[4].metric("Artifacts", len(run.artifacts))
    st.caption(str(run_root))
    if result.warnings:
        for warning in result.warnings:
            st.warning(warning)
    with st.expander("External service status", expanded=bool(result.sync_results)):
        st.json({"services": result.service_status, "sync": result.sync_results})


def _render_report(run_root: Path) -> None:
    guide_path = run_root / "reports" / "field_guide.md"
    if guide_path.exists():
        st.markdown(guide_path.read_text(encoding="utf-8"))
        st.download_button("Download field guide", guide_path.read_bytes(), file_name="field_guide.md")
        _render_report_citations(run_root)
    else:
        st.warning("No field guide generated.")


def _render_report_citations(run_root: Path) -> None:
    citations_path = run_root / "reports" / "report_citations.json"
    if not citations_path.exists():
        return
    citations = _read_json(citations_path)
    if not isinstance(citations, list) or not citations:
        return
    st.divider()
    st.subheader("Evidence Used")
    for citation in citations:
        citation_id = citation.get("citation_id", "E?")
        title = citation.get("title") or citation.get("work_id") or "Untitled"
        source_type = citation.get("source_type", "source")
        score = citation.get("score", "")
        with st.expander(f"[{citation_id}] {title}"):
            cols = st.columns(3)
            cols[0].caption(f"Source: {source_type}")
            cols[1].caption(f"Work ID: {citation.get('work_id', '')}")
            cols[2].caption(f"Score: {score}")
            snippet = citation.get("snippet", "")
            if snippet:
                st.write(snippet)
            artifact_path = citation.get("artifact_path", "")
            if artifact_path:
                st.caption(f"Artifact: {artifact_path}")


def _render_plan(run_root: Path, run: Any) -> None:
    plan_path = run_root / "reports" / "plan.json"
    if plan_path.exists():
        st.json(_read_json(plan_path))
    task_rows = []
    for item in run.task_results:
        task_rows.append({
            "task_id": item.task_id,
            "skill": item.skill,
            "status": getattr(item.status, "value", str(item.status)),
            "error": item.error or "",
            "mcp_results": len(item.mcp_results),
        })
    if task_rows:
        st.dataframe(_safe_table_rows(task_rows), width="stretch")


def _render_corpus(run_root: Path) -> None:
    corpus_files = sorted((run_root / "corpora").glob("*.json")) if (run_root / "corpora").exists() else []
    if not corpus_files:
        st.info("No corpus artifact.")
        return
    for path in corpus_files:
        data = _read_json(path)
        papers = data.get("papers", [])
        st.subheader(path.name)
        st.metric("Papers", len(papers))
        rows = [{"work_id": p.get("work_id"), "year": p.get("publication_year"), "cites": p.get("cited_by_count"), "title": p.get("title")} for p in papers[:50]]
        st.dataframe(_safe_table_rows(rows), width="stretch")
        years: Dict[str, int] = {}
        for paper in papers:
            year = str(paper.get("publication_year") or "unknown")
            years[year] = years.get(year, 0) + 1
        st.bar_chart(years)


def _render_graph(run_root: Path, result: ResearchWorkflowResult) -> None:
    graph_dir = run_root / "graph"
    graph_files = sorted(graph_dir.glob("*.json")) if graph_dir.exists() else []
    if result.sync_results.get("neo4j"):
        st.json({"neo4j_sync": result.sync_results["neo4j"]})
    for path in graph_files:
        data = _read_json(path)
        with st.expander(path.name, expanded="metrics" in path.name):
            if isinstance(data, dict) and "key_papers" in data:
                st.dataframe(_safe_table_rows(data.get("key_papers", [])), width="stretch")
                st.json({k: v for k, v in data.items() if k != "key_papers"})
            else:
                st.json(data)


def _render_evidence(run_root: Path) -> None:
    evidence_dir = run_root / "evidence"
    bundle_files = sorted(evidence_dir.glob("EB_*.json")) if evidence_dir.exists() else []
    if not bundle_files:
        st.info("No evidence bundle.")
        return
    for path in bundle_files:
        bundle = _read_json(path)
        st.subheader(path.name)
        if bundle.get("warnings"):
            for warning in bundle["warnings"]:
                st.warning(warning)
        for record in bundle.get("records", []):
            title = f"{record.get('work_id')} | {record.get('section') or 'body'} | score={record.get('retrieval_score')} | {record.get('support_status')}"
            with st.expander(title):
                st.write(record.get("child_text", ""))
                st.caption(record.get("parent_text", ""))


def _render_artifacts(run_root: Path) -> None:
    if not run_root.exists():
        st.info("No artifact directory.")
        return
    rows = []
    for path in sorted(p for p in run_root.rglob("*") if p.is_file()):
        rows.append({"path": str(path.relative_to(run_root)), "bytes": path.stat().st_size})
    st.dataframe(_safe_table_rows(rows), width="stretch")
    for row in rows:
        path = run_root / row["path"]
        with path.open("rb") as fh:
            st.download_button(f"Download {row['path']}", fh.read(), file_name=path.name, key=f"dl_{row['path']}")


def _render_trace(result: ResearchWorkflowResult) -> None:
    tool_rows = [event for event in result.run.trace if event.get("type") in {"tool_call", "tool_result", "task_failed"}]
    st.json({"warnings": result.warnings, "services": result.service_status, "sync": result.sync_results})
    st.dataframe(_safe_table_rows(tool_rows), width="stretch")
    with st.expander("Raw run trace"):
        st.json(result.run.trace)
    with st.expander("MCP results"):
        st.json([_to_jsonable(item) for item in result.run.results])



def _safe_table_rows(rows: Any) -> List[Dict[str, Any]]:
    """Convert nested objects to Arrow-friendly scalar table cells."""
    if not rows:
        return []
    if isinstance(rows, dict):
        rows = [rows]
    safe_rows: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            safe_rows.append({"value": _safe_cell(row)})
            continue
        safe_rows.append({str(key): _safe_cell(value) for key, value in row.items()})
    return safe_rows


def _safe_cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int, float, bool)):
        return getattr(value, "value")
    try:
        return json.dumps(_to_jsonable(value), ensure_ascii=False, default=str)
    except Exception:
        return str(value)

def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "path": str(path)}


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(value)
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


if __name__ == "__main__":
    main()


