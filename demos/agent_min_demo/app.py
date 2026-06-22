"""Streamlit UI for Lab 3.

Run:
    streamlit run lab3/app.py
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List

import streamlit as st

from lab3.poetry_skill import PoemRequest, PoetryLangGraphAgent


def short_json(data: Any, limit: int = 180) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def stream_text(text: str) -> Iterable[str]:
    for char in text:
        yield char
        time.sleep(0.015)


def render_metric(metric: Dict[str, Any]) -> None:
    if not metric:
        st.info("尚未完成格律检验。")
        return

    checks = [
        ("句数", metric.get("line_count_ok"), f"{len(metric.get('lines', []))}/{metric.get('expected_lines', '-')}"),
        ("字数", metric.get("char_count_ok"), f"{metric.get('lengths', [])}"),
        ("押韵", metric.get("rhyme_ok"), "、".join(metric.get("rhyme_keys", [])) or "-"),
    ]
    cols = st.columns(3)
    for col, (label, ok, detail) in zip(cols, checks):
        col.metric(label, "通过" if ok else "未通过", detail)

    st.progress(float(metric.get("score", 0.0)), text=f"综合分：{float(metric.get('score', 0.0)):.0%}")

    lines = metric.get("lines", [])
    lengths = metric.get("lengths", [])
    table_rows = []
    for idx, line in enumerate(lines):
        table_rows.append({
            "句序": idx + 1,
            "诗句": line,
            "字数": lengths[idx] if idx < len(lengths) else "-",
            "偶句韵母": metric.get("rhyme_finals", [])[idx // 2] if idx % 2 == 1 and idx // 2 < len(metric.get("rhyme_finals", [])) else "",
        })
    if table_rows:
        st.dataframe(table_rows, hide_index=True, width='stretch')

    problems = metric.get("problems", [])
    if problems:
        st.warning("\n".join(str(problem) for problem in problems))
    else:
        st.success("句数、字数与偶句押韵均通过。")


def render_trace(trace: List[Dict[str, Any]]) -> None:
    if not trace:
        st.info("等待 Agent 开始行动。")
        return

    by_step: Dict[int, List[Dict[str, Any]]] = {}
    for item in trace:
        by_step.setdefault(int(item.get("step", 0)), []).append(item)

    for step, items in by_step.items():
        with st.expander(f"第 {step} 步", expanded=step == max(by_step)):
            for item in items:
                event_type = str(item.get("type", "事件"))
                tool = item.get("tool")
                status = str(item.get("status", ""))
                duration = item.get("duration_ms")
                title = f"{event_type}" + (f" · {tool}" if tool else "")
                if status:
                    title += f" · {status}"
                if duration is not None:
                    title += f" · {duration} ms"
                st.markdown(f"**{title}**")
                if "content" in item:
                    st.write(item["content"])
                if item.get("tool_calls"):
                    st.caption("计划调用")
                    st.dataframe([
                        {"工具": call.get("name"), "参数": short_json(call.get("args", {}), 260)}
                        for call in item.get("tool_calls", [])
                    ], hide_index=True, width='stretch')
                if "args" in item:
                    st.caption("输入")
                    st.code(json.dumps(item["args"], ensure_ascii=False, indent=2), language="json")
                if "result" in item:
                    result = item["result"]
                    if isinstance(result, str):
                        st.text(result)
                    elif isinstance(result, dict) and "score" in result:
                        st.caption(f"格律分：{float(result.get('score', 0.0)):.0%}")
                        st.write(result)
                    else:
                        st.write(result)
                if "finish_status" in item:
                    st.caption("终止判断")
                    st.json(item["finish_status"], expanded=False)
                if item.get("raw_content"):
                    st.caption("模型原始文本")
                    st.text(str(item["raw_content"]))


def render_review(review: Dict[str, Any]) -> None:
    if not review:
        st.info("尚未完成评审。")
        return

    passed = review.get("通过")
    cols = st.columns(3)
    cols[0].metric("评审", "通过" if passed is True or str(passed).lower() == "true" or passed == "通过" else "未通过")
    cols[1].metric("解析", str(review.get("parse_status", "-")))
    cols[2].metric("字段", "完整" if "意见" in review else "缺少意见")
    opinion = str(review.get("意见", ""))
    if passed is True or str(passed).lower() == "true" or passed == "通过":
        st.success(opinion or "评审通过。")
    else:
        st.warning(opinion or "评审未通过。")


def render_tool_history(tool_history: List[Dict[str, Any]]) -> None:
    if not tool_history:
        st.info("尚无工具调用记录。")
        return

    st.dataframe([
        {
            "步骤": item.get("step"),
            "工具": item.get("tool"),
            "状态": item.get("status", "success"),
            "耗时(ms)": item.get("duration_ms", "-"),
            "输入": short_json(item.get("args", {}), 180),
            "输出": short_json(item.get("result", {}), 220),
        }
        for item in tool_history
    ], hide_index=True, width='stretch')


def render_debug_panel(result: Dict[str, Any]) -> None:
    debug_events = result.get("debug_events", [])
    if not debug_events:
        return

    with st.expander("Debug 原始流程", expanded=False):
        st.caption("仅 DEBUG_MODE=1 时输出；不包含 API Key。")
        tabs = st.tabs(["Tool Calling", "Trace", "State"])
        with tabs[0]:
            for event in debug_events:
                label = f"Debug #{event.get('event_id')} · {event.get('kind')} · {event.get('model')}"
                with st.expander(label, expanded=False):
                    if event.get("error"):
                        st.error(str(event.get("error")))
                    st.caption("tool_choice")
                    st.code(json.dumps(event.get("tool_choice"), ensure_ascii=False, indent=2), language="json")
                    st.caption("messages")
                    st.code(json.dumps(event.get("messages", []), ensure_ascii=False, indent=2), language="json")
                    st.caption("tools schema")
                    st.code(json.dumps(event.get("tools_schema", []), ensure_ascii=False, indent=2), language="json")
                    st.caption("raw content")
                    st.text(str(event.get("raw_content", "")))
                    st.caption("raw tool_calls")
                    st.code(json.dumps(event.get("raw_tool_calls", []), ensure_ascii=False, indent=2), language="json")
                    st.caption("parsed tool_calls")
                    st.code(json.dumps(event.get("parsed_tool_calls", []), ensure_ascii=False, indent=2), language="json")
        with tabs[1]:
            st.code(json.dumps(result.get("trace", []), ensure_ascii=False, indent=2), language="json")
            st.caption("tool_history")
            st.code(json.dumps(result.get("tool_history", []), ensure_ascii=False, indent=2), language="json")
        with tabs[2]:
            st.caption("context_summary")
            st.text(str(result.get("context_summary", "")))
            st.caption("attempts")
            st.code(json.dumps(result.get("attempts", []), ensure_ascii=False, indent=2), language="json")
            if result.get("tool_calling_error"):
                st.caption("tool_calling_error")
                st.error(str(result.get("tool_calling_error")))


st.set_page_config(page_title="唐诗创作 Agent Demo", page_icon="📝", layout="wide")
st.title("唐诗创作 Agent Demo")

with st.sidebar:
    theme = st.text_input("主题", "春天")
    genre = st.selectbox("体裁", ["七言绝句", "五言绝句"], index=0)
    emotion = st.text_input("情感", "清新")
    max_steps = st.slider("最大迭代步数", min_value=1, max_value=8, value=5)
    submitted = st.button("生成", type="primary", width='stretch')

poem_panel, metric_panel = st.columns([1.05, 1])
trace_panel = st.container()

if submitted:
    agent = PoetryLangGraphAgent(max_steps=max_steps)
    request = PoemRequest(theme=theme, genre=genre, emotion=emotion)

    latest_trace: List[Dict[str, Any]] = []
    latest_metric: Dict[str, Any] = {}
    latest_review: Dict[str, Any] = {}
    final_result: Dict[str, Any] = {}

    with st.status("思考中，正在生成候选诗并检查格律...", expanded=True) as status:
        poem_slot = poem_panel.empty()
        metric_slot = metric_panel.empty()
        trace_slot = trace_panel.empty()

        for event in agent.stream(request):
            if event.get("event") == "status":
                status.write(str(event.get("content", "")))
                continue

            latest_trace = list(event.get("trace", latest_trace))  # type: ignore[arg-type]
            if event.get("event") == "metric":
                latest_metric = event.get("metric", {})  # type: ignore[assignment]
                status.write("格律检验已更新。")
            elif event.get("event") == "trace":
                entry = event.get("entry", {})
                status.write(f"{entry.get('type', '事件')} {entry.get('tool', '')}".strip())
            elif event.get("event") == "review":
                latest_review = event.get("review", {})  # type: ignore[assignment]
                status.write("评审 Agent 已返回意见。")
            elif event.get("event") == "final":
                final_result = event.get("result", {})  # type: ignore[assignment]
                latest_trace = list(final_result.get("trace", latest_trace))
                latest_metric = final_result.get("metric", latest_metric)  # type: ignore[assignment]
                latest_review = final_result.get("review", latest_review)  # type: ignore[assignment]

            with metric_slot.container():
                st.subheader("格律检验")
                render_metric(latest_metric)
            with trace_slot.container():
                st.subheader("Agent 轨迹")
                render_trace(latest_trace)
            time.sleep(0.05)

        if final_result.get("success"):
            status.update(label="生成完成", state="complete", expanded=False)
        else:
            status.update(label="已停止，未完全通过", state="error", expanded=True)

    with poem_slot.container():
        st.subheader("最终诗作")
        poem = str(final_result.get("final_poem", ""))
        if poem:
            st.write_stream(stream_text(poem))
        else:
            st.warning(final_result.get("reason", "未生成诗作。"))

    finish_status = final_result.get("finish_status", {})
    if finish_status:
        with st.expander("终止条件", expanded=False):
            cols = st.columns(3)
            cols[0].metric("格律", "通过" if finish_status.get("metric_passed") else "未通过")
            cols[1].metric("评审", "通过" if finish_status.get("review_passed") else "未通过")
            cols[2].metric("停止", "是" if finish_status.get("should_stop") else "否")
            if final_result.get("reason"):
                st.caption(str(final_result.get("reason")))

    with st.expander("评审结果", expanded=True):
        render_review(final_result.get("review", latest_review))

    with st.expander("工具历史", expanded=True):
        render_tool_history(final_result.get("tool_history", []))

    memory = final_result.get("memory", [])
    if memory:
        with st.expander("失败记忆", expanded=False):
            for idx, item in enumerate(memory, start=1):
                st.markdown(f"**尝试 {idx}**")
                count = item.get("count", 1)
                st.write(f"[{item.get('failure_type', 'failed')}] x{count}: {item.get('feedback', '')}")

    attempts = final_result.get("attempts", [])
    context_summary = final_result.get("context_summary", "")
    if attempts or context_summary:
        with st.expander("上下文记忆", expanded=False):
            if context_summary:
                st.text(context_summary)
            if attempts:
                st.dataframe([
                    {
                        "尝试": item.get("attempt_id"),
                        "状态": item.get("status"),
                        "诗作": item.get("poem"),
                        "反馈": item.get("feedback", ""),
                    }
                    for item in attempts
                ], hide_index=True, width='stretch')

    render_debug_panel(final_result)
else:
    with poem_panel:
        st.subheader("最终诗作")
        st.info("设置主题、体裁和情感后开始生成。")
    with metric_panel:
        st.subheader("格律检验")
        render_metric({})
    with trace_panel:
        st.subheader("Agent 轨迹")
        render_trace([])
