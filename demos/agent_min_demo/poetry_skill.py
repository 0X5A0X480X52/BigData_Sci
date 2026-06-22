"""LangGraph poetry skill for Lab 3."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterator, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field

from common.llm_client import OpenAICompatibleClient
from common.poem_utils import evaluate_poem, get_final

try:
    from langgraph.graph import END, StateGraph
except Exception as exc:  # pragma: no cover - import-time dependency guard.
    END = "__end__"  # type: ignore[assignment]
    StateGraph = None  # type: ignore[assignment]
    LANGGRAPH_IMPORT_ERROR: Optional[Exception] = exc
else:
    LANGGRAPH_IMPORT_ERROR = None


Genre = Literal["七言绝句", "五言绝句"]


@dataclass
class PoemRequest:
    theme: str
    genre: str = "七言绝句"
    emotion: str = "清新"


class GeneratePoemArgs(BaseModel):
    theme: str = Field(..., description="诗歌主题，例如：春天、送别、边塞。")
    genre: Genre = Field("七言绝句", description="诗歌体裁。")
    emotion: str = Field("清新", description="诗歌情感风格，例如：清新、豪迈、惆怅。")
    feedback: str = Field("", description="上一轮格律或评审反馈。首次生成时为空字符串。")
    failed_attempts: List[Dict[str, Any]] = Field(default_factory=list, description="本次会话中已失败的尝试。")
    context: Dict[str, Any] = Field(default_factory=dict, description="本次会话的完整上下文摘要、历史尝试和工具轨迹。")


class CheckMetricArgs(BaseModel):
    poem: str = Field(..., description="待检查的诗歌文本。")
    genre: Genre = Field("七言绝句", description="目标体裁。")


class LookupRhymeArgs(BaseModel):
    char: str = Field(..., min_length=1, max_length=1, description="要查询韵母的单个汉字。")


class ReviewPoemArgs(BaseModel):
    poem: str = Field(..., description="待评审的诗歌文本。")
    theme: str = Field(..., description="诗歌主题。")
    genre: Genre = Field("七言绝句", description="目标体裁。")
    emotion: str = Field("清新", description="目标情感风格。")


def _model_json_schema(model: type[BaseModel]) -> Dict[str, Any]:
    if hasattr(model, "model_json_schema"):
        return model.model_json_schema()  # type: ignore[attr-defined]
    return model.schema()


def _openai_tool_schema(name: str, description: str, args_schema: type[BaseModel]) -> Dict[str, Any]:
    schema = _model_json_schema(args_schema)
    schema.setdefault("type", "object")
    schema.setdefault("additionalProperties", False)
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": schema,
        },
    }


TOOL_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "generate_poem": {
        "description": "根据主题、体裁、情感、失败记忆和反馈生成候选诗。",
        "args_schema": GeneratePoemArgs,
    },
    "check_metric": {
        "description": "检查诗歌句数、字数、押韵情况，返回结构化格律报告。",
        "args_schema": CheckMetricArgs,
    },
    "lookup_rhyme": {
        "description": "查询汉字韵母，用于修正偶数句尾字。",
        "args_schema": LookupRhymeArgs,
    },
    "review_poem": {
        "description": "评审诗歌是否扣题、用词自然、意境完整。",
        "args_schema": ReviewPoemArgs,
    },
}

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    _openai_tool_schema(name, definition["description"], definition["args_schema"])
    for name, definition in TOOL_DEFINITIONS.items()
]


class PoetrySkill:
    """Tool implementation and function-calling dispatcher."""

    def __init__(self) -> None:
        self.client = OpenAICompatibleClient()
        self.demo_candidates = [
            "春风拂柳入云天\n细雨催花满故园\n一径清香随客远\n千山新绿照云中",
            "春风吹柳岸\n细雨润花间\n新燕归来早\n一溪明月还",
            "春色满园映碧天\n东风入户柳如烟\n花开一路香随客\n雨过千山草色鲜",
        ]
        self.demo_call_count = 0

    def generate_poem(
        self,
        theme: str,
        genre: str = "七言绝句",
        emotion: str = "清新",
        feedback: str = "",
        failed_attempts: Optional[List[Dict[str, Any]]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload = {
            "theme": theme,
            "genre": genre,
            "emotion": emotion,
            "feedback": feedback,
            "failed_attempts": failed_attempts or [],
            "context": context or {},
        }
        if not self.client.config.demo_mode:
            return self._generate_poem_with_llm(payload)

        idx = min(self.demo_call_count, len(self.demo_candidates) - 1)
        self.demo_call_count += 1
        return self.demo_candidates[idx]

    def _generate_poem_with_llm(self, payload: Dict[str, Any]) -> str:
        genre = str(payload["genre"])
        expected_chars = 5 if "五言" in genre else 7
        failed_attempts = payload.get("failed_attempts") or []
        context = payload.get("context") or {}
        context_summary = str(context.get("summary", "")).strip() or "暂无"
        attempts = context.get("attempts", [])
        tool_history = context.get("tool_history", [])
        failed_summary = json.dumps(failed_attempts, ensure_ascii=False, indent=2) if failed_attempts else "暂无"
        attempts_summary = json.dumps(attempts, ensure_ascii=False, indent=2) if attempts else "暂无"
        recent_tools = json.dumps(tool_history[-8:], ensure_ascii=False, indent=2) if tool_history else "暂无"
        prompt = (
            f"请创作一首{genre}。\n"
            f"主题：{payload['theme']}\n"
            f"情感：{payload['emotion']}\n"
            f"每句必须 {expected_chars} 个汉字，共 4 句。\n"
            "第 2 句和第 4 句必须押同一韵。\n"
            "只输出诗句本身，每句一行，不要标题、解释、标点、JSON 或 Markdown。\n"
            f"上一轮反馈：{payload.get('feedback') or '暂无'}\n"
            f"上下文摘要：\n{context_summary}\n"
            f"历史候选尝试：\n{attempts_summary}\n"
            f"失败记忆：\n{failed_summary}\n"
            f"最近工具调用：\n{recent_tools}\n"
        )
        raw = self.client.chat([
            {"role": "system", "content": "你是严格遵守格律约束的唐诗创作工具。"},
            {"role": "user", "content": prompt},
        ], temperature=0.7, stream=False)
        return self._clean_generated_poem(raw)

    def _clean_generated_poem(self, raw: str) -> str:
        parts: List[str] = []
        for line in raw.splitlines():
            line = line.strip().strip("`")
            if not line or line.lower() in {"json", "markdown"}:
                continue
            if line.startswith(("标题", "诗题", "解释", "说明")):
                continue
            parts.extend(re.split(r"[，。！？；、,.!?;]+", line))

        lines = []
        for part in parts:
            chinese = "".join(re.findall(r"[\u4e00-\u9fff]", part))
            if chinese:
                lines.append(chinese)
        return "\n".join(lines[:4]).strip() or raw.strip()

    def check_metric(self, poem: str, genre: str = "七言绝句") -> Dict[str, Any]:
        expected_chars = 5 if "五言" in genre else 7
        return evaluate_poem(poem, expected_lines=4, expected_chars=expected_chars)

    def lookup_rhyme(self, char: str) -> Dict[str, str]:
        return {"char": char, "final": get_final(char)}

    def review_poem(self, poem: str, theme: str, genre: str = "七言绝句", emotion: str = "清新") -> Dict[str, Any]:
        if self.client.config.demo_mode:
            return {"通过": True, "意见": "DEMO_MODE 下默认通过评审。", "parse_status": "demo"}

        raw = self.client.chat([
            {"role": "system", "content": "你是古诗评审 Agent。"},
            {
                "role": "user",
                "content": (
                    "请评价下面诗歌是否扣题、用词是否自然、意境是否完整。\n"
                    f"主题：{theme}\n体裁：{genre}\n情感：{emotion}\n诗歌：\n{poem}\n"
                    "请用 JSON 输出：{\"通过\": true/false, \"意见\": \"...\"}"
                ),
            },
        ], temperature=0.1, stream=False)
        return _parse_review_response(raw)

    def dispatch(self, name: str, arguments: Dict[str, Any]) -> Any:
        if name not in TOOL_DEFINITIONS:
            raise ValueError(f"未知工具：{name}")
        args_model = TOOL_DEFINITIONS[name]["args_schema"]
        args = args_model(**arguments)
        if name == "generate_poem":
            return self.generate_poem(**args.dict())
        if name == "check_metric":
            return self.check_metric(**args.dict())
        if name == "lookup_rhyme":
            return self.lookup_rhyme(**args.dict())
        if name == "review_poem":
            return self.review_poem(**args.dict())
        raise ValueError(f"未知工具：{name}")


class PoetryState(TypedDict, total=False):
    request: Dict[str, Any]
    messages: List[Dict[str, Any]]
    current_poem: str
    metric: Dict[str, Any]
    review: Dict[str, Any]
    trace: List[Dict[str, Any]]
    memory: List[Dict[str, Any]]
    attempts: List[Dict[str, Any]]
    tool_history: List[Dict[str, Any]]
    context_summary: str
    feedback: str
    pending_tool_calls: List[Dict[str, Any]]
    iteration: int
    max_steps: int
    success: bool
    done: bool
    reason: str
    finish_status: Dict[str, bool]
    last_event: Dict[str, Any]
    tool_calling_error: str
    debug_events: List[Dict[str, Any]]


def _append_trace(state: PoetryState, entry: Dict[str, Any]) -> PoetryState:
    trace = list(state.get("trace", []))
    trace.append(entry)
    return {"trace": trace, "last_event": entry}


def _short_json(data: Any, limit: int = 300) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "..."


def _parse_review_response(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    candidates = [text]

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S | re.I)
    if fenced:
        candidates.insert(0, fenced.group(1))

    obj = re.search(r"\{.*\}", text, flags=re.S)
    if obj:
        candidates.append(obj.group(0))

    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                data.setdefault("parse_status", "json")
                return data
        except Exception:
            continue

    return {"通过": False, "意见": raw, "parse_status": "failed"}


def _truthy_review_value(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "通过", "是", "合格"}
    return False


class PoetryLangGraphAgent:
    """LangGraph-based poetry agent with native tool-call dispatch."""

    def __init__(self, max_steps: int = 5) -> None:
        if StateGraph is None:
            raise ImportError(f"请先安装 langgraph：{LANGGRAPH_IMPORT_ERROR}")
        self.max_steps = max_steps
        self.skill = PoetrySkill()
        self.client = self.skill.client
        self.graph = self._build_graph()

    def run(self, request: PoemRequest) -> Dict[str, Any]:
        final_state = self.graph.invoke(self._initial_state(request))
        return self._result_from_state(final_state)

    def stream(self, request: PoemRequest) -> Iterator[Dict[str, Any]]:
        final_state: PoetryState = {}
        for update in self.graph.stream(self._initial_state(request)):
            for node_update in update.values():
                if not node_update:
                    continue
                final_state.update(node_update)
                event = node_update.get("last_event")
                if event:
                    yield {
                        "event": self._event_kind(event),
                        "entry": event,
                        "trace": final_state.get("trace", []),
                        "metric": final_state.get("metric", {}),
                        "review": final_state.get("review", {}),
                    }
                if node_update.get("done"):
                    yield {"event": "final", "result": self._result_from_state(final_state)}

    def _build_graph(self) -> Any:
        graph = StateGraph(PoetryState)
        graph.add_node("agent_decide", self._agent_decide)
        graph.add_node("tool_dispatch", self._tool_dispatch)
        graph.add_node("memory_update", self._memory_update)
        graph.add_node("finish_check", self._finish_check)
        graph.set_entry_point("agent_decide")
        graph.add_edge("agent_decide", "tool_dispatch")
        graph.add_edge("tool_dispatch", "memory_update")
        graph.add_edge("memory_update", "finish_check")
        graph.add_conditional_edges("finish_check", self._route_after_finish, {"continue": "agent_decide", "end": END})
        return graph.compile()

    def _initial_state(self, request: PoemRequest) -> PoetryState:
        return {
            "request": asdict(request),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是唐诗创作 Agent。必须使用工具生成诗、检查格律、必要时查韵并评审。"
                        "如果已有失败记忆，下一轮必须避免重复同类错误。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"主题：{request.theme}\n体裁：{request.genre}\n情感：{request.emotion}",
                },
            ],
            "current_poem": "",
            "metric": {},
            "review": {},
            "trace": [],
            "memory": [],
            "attempts": [],
            "tool_history": [],
            "context_summary": "暂无历史上下文。",
            "feedback": "",
            "pending_tool_calls": [],
            "iteration": 0,
            "max_steps": self.max_steps,
            "success": False,
            "done": False,
            "reason": "",
            "finish_status": {"metric_passed": False, "review_passed": False, "should_stop": False},
            "debug_events": [],
        }

    def _context_payload(self, state: PoetryState) -> Dict[str, Any]:
        return {
            "summary": state.get("context_summary", ""),
            "attempts": state.get("attempts", []),
            "tool_history": state.get("tool_history", []),
            "memory": state.get("memory", []),
            "current_poem": state.get("current_poem", ""),
            "metric": state.get("metric", {}),
            "review": state.get("review", {}),
            "feedback": state.get("feedback", ""),
        }

    def _build_context_summary(self, state: PoetryState) -> str:
        attempts = state.get("attempts", [])
        tool_history = state.get("tool_history", [])
        memory = state.get("memory", [])

        lines = [
            f"当前候选诗：{state.get('current_poem') or '暂无'}",
            f"当前反馈：{state.get('feedback') or '暂无'}",
            "历史候选尝试：",
        ]
        if attempts:
            for attempt in attempts:
                metric = attempt.get("metric") or {}
                review = attempt.get("review") or {}
                problems = "；".join(str(x) for x in metric.get("problems", [])) or "无"
                review_text = review.get("意见", "未评审") if isinstance(review, dict) else "未评审"
                lines.append(
                    f"- 尝试{attempt.get('attempt_id')}: status={attempt.get('status')} "
                    f"poem={attempt.get('poem')} metric_score={metric.get('score', '-')} "
                    f"problems={problems} review={review_text}"
                )
        else:
            lines.append("- 暂无")

        lines.append("失败模式：")
        if memory:
            for item in memory:
                lines.append(
                    f"- [{item.get('failure_type')}] count={item.get('count', 1)} "
                    f"last_step={item.get('last_step')} feedback={item.get('feedback')}"
                )
        else:
            lines.append("- 暂无")

        lines.append("最近工具调用：")
        if tool_history:
            for item in tool_history[-8:]:
                lines.append(
                    f"- step={item.get('step')} tool={item.get('tool')} "
                    f"args={_short_json(item.get('args', {}), 120)} "
                    f"result={_short_json(item.get('result', {}), 180)}"
                )
        else:
            lines.append("- 暂无")

        return "\n".join(lines)

    def _remember_failure(
        self,
        state: PoetryState,
        failure_type: str,
        signature: str,
        feedback: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        memory = list(state.get("memory", []))
        step = state.get("iteration", 0)
        for item in memory:
            if item.get("signature") == signature and item.get("failure_type") == failure_type:
                item["count"] = int(item.get("count", 1)) + 1
                item["last_step"] = step
                item["feedback"] = feedback
                if extra:
                    item.update(extra)
                return memory

        new_item = {
            "failure_type": failure_type,
            "signature": signature,
            "feedback": feedback,
            "count": 1,
            "first_step": step,
            "last_step": step,
        }
        if extra:
            new_item.update(extra)
        memory.append(new_item)
        return memory

    def _update_last_attempt(self, attempts: List[Dict[str, Any]], values: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not attempts:
            return attempts
        updated = [dict(item) for item in attempts]
        updated[-1].update(values)
        return updated

    def _is_metric_passed(self, metric: Dict[str, Any]) -> bool:
        return bool(metric) and bool(metric.get("line_count_ok")) and bool(metric.get("char_count_ok")) and bool(metric.get("rhyme_ok"))

    def _is_review_passed(self, review: Dict[str, Any]) -> bool:
        return bool(review) and review.get("parse_status") != "failed" and _truthy_review_value(review.get("通过"))

    def _finish_status(self, state: PoetryState) -> Dict[str, bool]:
        metric_passed = self._is_metric_passed(state.get("metric", {}))
        review_passed = self._is_review_passed(state.get("review", {}))
        return {
            "metric_passed": metric_passed,
            "review_passed": review_passed,
            "should_stop": metric_passed and review_passed,
        }

    def _is_ready_to_finish(self, state: PoetryState) -> bool:
        return self._finish_status(state)["should_stop"]

    def _agent_decide(self, state: PoetryState) -> PoetryState:
        current_iteration = int(state.get("iteration", 0))
        if self._is_ready_to_finish(state):
            finish_status = self._finish_status(state)
            entry = {"step": current_iteration, "type": "终止", "content": "accepted：格律与评审均通过。", "finish_status": finish_status}
            updates = _append_trace(state, entry)
            updates.update({
                "done": True,
                "success": True,
                "reason": "已通过格律与评审。",
                "finish_status": finish_status,
                "pending_tool_calls": [],
            })
            return updates

        request = state["request"]

        if self.client.config.demo_mode:
            tool_calls = self._demo_tool_calls(state)
            thought = "DEMO_MODE 使用 LangGraph 节点生成下一步工具调用。"
        else:
            tool_calls, thought, debug_event = self._native_tool_calls(state)
        iteration = current_iteration + 1 if any(call["name"] == "generate_poem" for call in tool_calls) else current_iteration
        if iteration == 0:
            iteration = 1

        entry = {
            "step": iteration,
            "type": "思考",
            "content": thought,
            "tool_calls": [{"name": call["name"], "args": call["args"]} for call in tool_calls],
        }
        if not self.client.config.demo_mode:
            entry["debug_ref"] = debug_event.get("event_id")
            if debug_event.get("raw_content"):
                entry["raw_content"] = debug_event["raw_content"]
        updates = _append_trace(state, entry)
        updates.update({"iteration": iteration, "pending_tool_calls": tool_calls})
        if not self.client.config.demo_mode and self.client.config.debug_mode:
            updates["debug_events"] = list(state.get("debug_events", [])) + [debug_event]
        if not tool_calls:
            updates.update({"done": True, "reason": thought})
        if not request.get("theme"):
            updates.update({"done": True, "reason": "主题不能为空。"})
        return updates

    def _tool_dispatch(self, state: PoetryState) -> PoetryState:
        if state.get("done"):
            return {}

        updates: PoetryState = {"pending_tool_calls": []}
        messages = list(state.get("messages", []))
        current_poem = state.get("current_poem", "")
        metric = dict(state.get("metric", {}))
        review = dict(state.get("review", {}))
        attempts = list(state.get("attempts", []))
        tool_history = list(state.get("tool_history", []))
        trace = list(state.get("trace", []))
        last_event: Dict[str, Any] = {}

        for call in state.get("pending_tool_calls", []):
            name = call["name"]
            args = call.get("args", {})
            if name == "generate_poem":
                args = dict(args)
                args.setdefault("failed_attempts", state.get("memory", []))
                args.setdefault("context", self._context_payload(state))
            step = state.get("iteration", 0)
            action = {"step": step, "type": "行动", "tool": name, "args": args, "status": "running"}
            trace.append(action)
            started_at = time.perf_counter()
            status = "success"
            try:
                result = self.skill.dispatch(name, args)
            except Exception as exc:
                result = {"error": str(exc)}
                status = "error"
                updates["tool_calling_error"] = str(exc)
            duration_ms = round((time.perf_counter() - started_at) * 1000, 2)
            action["status"] = status
            action["duration_ms"] = duration_ms
            observation = {
                "step": step,
                "type": "观察",
                "tool": name,
                "result": result,
                "status": status,
                "duration_ms": duration_ms,
            }
            trace.append(observation)
            last_event = observation
            tool_history.append({
                "step": step,
                "tool": name,
                "args": args,
                "result": result,
                "status": status,
                "duration_ms": duration_ms,
            })
            messages.append({"role": "tool", "name": name, "content": json.dumps(result, ensure_ascii=False)})

            if name == "generate_poem" and isinstance(result, str):
                current_poem = result
                metric = {}
                review = {}
                updates["feedback"] = ""
                attempts.append({
                    "attempt_id": len(attempts) + 1,
                    "step": step,
                    "poem": result,
                    "feedback": args.get("feedback", ""),
                    "context_summary": state.get("context_summary", ""),
                    "metric": {},
                    "review": {},
                    "status": "generated",
                })
            elif name == "check_metric" and isinstance(result, dict):
                metric = result
                status = "metric_passed" if (
                    result.get("line_count_ok") and result.get("char_count_ok") and result.get("rhyme_ok")
                ) else "metric_failed"
                attempts = self._update_last_attempt(attempts, {"metric": result, "status": status})
            elif name == "review_poem" and isinstance(result, dict):
                review = result
                status = "review_passed" if self._is_review_passed(result) else "review_failed"
                attempts = self._update_last_attempt(attempts, {"review": result, "status": status})

            candidate_state: PoetryState = dict(state)
            candidate_state.update({"metric": metric, "review": review})
            if self._is_ready_to_finish(candidate_state):
                break

        temp_state: PoetryState = dict(state)
        temp_state.update({
            "messages": messages,
            "current_poem": current_poem,
            "metric": metric,
            "review": review,
            "attempts": attempts,
            "tool_history": tool_history,
            "trace": trace,
            "feedback": updates.get("feedback", state.get("feedback", "")),
        })
        updates.update({
            "messages": messages,
            "current_poem": current_poem,
            "metric": metric,
            "review": review,
            "attempts": attempts,
            "tool_history": tool_history,
            "context_summary": self._build_context_summary(temp_state),
            "trace": trace,
            "last_event": last_event,
        })
        return updates

    def _memory_update(self, state: PoetryState) -> PoetryState:
        last_event = state.get("last_event", {})
        tool = last_event.get("tool")
        result = last_event.get("result")
        failure_type = ""
        signature = ""
        feedback = ""
        extra: Dict[str, Any] = {"poem": state.get("current_poem", "")}

        if isinstance(result, dict) and result.get("error"):
            failure_type = "tool_error"
            signature = f"{tool}:{result.get('error')}"
            feedback = f"工具 {tool} 调用失败：{result.get('error')}"
            extra["tool_error"] = result
        elif tool == "check_metric":
            metric = state.get("metric", {})
            if not metric or not metric.get("problems"):
                return {}
            failure_type = "metric_failed"
            feedback = "；".join(str(x) for x in metric.get("problems", []))
            signature = "|".join(str(x) for x in metric.get("problems", []))
            extra["metric"] = metric
        elif tool == "review_poem":
            review = state.get("review", {})
            if not review or self._is_review_passed(review):
                return {}
            failure_type = "review_failed"
            feedback = str(review.get("意见", "评审未通过。"))
            signature = f"review:{feedback}"
            extra["review"] = review
        else:
            return {}

        old_memory = list(state.get("memory", []))
        memory = self._remember_failure(state, failure_type, signature, feedback, extra)
        repeated = len(memory) == len(old_memory)
        display_feedback = feedback + ("；注意不要重复上一轮失败模式。" if repeated else "")
        entry = {
            "step": state.get("iteration", 0),
            "type": "记忆",
            "content": display_feedback,
            "failure_type": failure_type,
        }
        temp_state: PoetryState = dict(state)
        temp_state.update({"memory": memory, "feedback": display_feedback})
        updates = _append_trace(state, entry)
        updates.update({
            "memory": memory,
            "feedback": display_feedback,
            "context_summary": self._build_context_summary(temp_state),
        })
        return updates

    def _finish_check(self, state: PoetryState) -> PoetryState:
        if state.get("done"):
            return {}

        metric = state.get("metric", {})
        review = state.get("review", {})
        max_steps = int(state.get("max_steps", self.max_steps))
        iteration = int(state.get("iteration", 0))
        finish_status = self._finish_status(state)

        if finish_status["should_stop"]:
            entry = {
                "step": iteration,
                "type": "终止",
                "content": "accepted：格律与评审均通过，接受当前诗作。",
                "finish_status": finish_status,
                "reason": "已通过格律与评审。",
            }
            updates = _append_trace(state, entry)
            updates.update({"done": True, "success": True, "reason": "已通过格律与评审。", "finish_status": finish_status})
            return updates

        continue_reason = ""
        if self._is_review_passed(review) and not self._is_metric_passed(metric):
            continue_reason = "评审已通过，但格律尚未全部通过，继续修正。"

        if iteration >= max_steps:
            entry = {
                "step": iteration,
                "type": "终止",
                "content": "达到最大步数仍未通过。",
                "finish_status": finish_status,
                "reason": "达到最大步数仍未通过。",
            }
            updates = _append_trace(state, entry)
            updates.update({"done": True, "success": False, "reason": "达到最大步数仍未通过。", "finish_status": finish_status})
            return updates

        if continue_reason:
            entry = {
                "step": iteration,
                "type": "终止检查",
                "content": continue_reason,
                "finish_status": finish_status,
                "reason": continue_reason,
            }
            updates = _append_trace(state, entry)
            updates.update({"done": False, "reason": continue_reason, "finish_status": finish_status})
            return updates

        return {"done": False, "reason": continue_reason, "finish_status": finish_status}

    def _route_after_finish(self, state: PoetryState) -> str:
        return "end" if state.get("done") else "continue"

    def _demo_tool_calls(self, state: PoetryState) -> List[Dict[str, Any]]:
        request = state["request"]
        poem = state.get("current_poem", "")
        metric = state.get("metric", {})
        review = state.get("review", {})
        feedback = state.get("feedback", "")
        memory = state.get("memory", [])

        if not poem or feedback:
            return [{
                "name": "generate_poem",
                "args": {
                    "theme": request["theme"],
                    "genre": request["genre"],
                    "emotion": request["emotion"],
                    "feedback": feedback,
                    "failed_attempts": memory,
                    "context": self._context_payload(state),
                },
            }]
        if not metric:
            return [{"name": "check_metric", "args": {"poem": poem, "genre": request["genre"]}}]
        if not self._is_metric_passed(metric):
            return [{
                "name": "generate_poem",
                "args": {
                    "theme": request["theme"],
                    "genre": request["genre"],
                    "emotion": request["emotion"],
                    "feedback": state.get("feedback", ""),
                    "failed_attempts": memory,
                    "context": self._context_payload(state),
                },
            }]
        if not review:
            return [{
                "name": "review_poem",
                "args": {
                    "poem": poem,
                    "theme": request["theme"],
                    "genre": request["genre"],
                    "emotion": request["emotion"],
                },
            }]
        if not self._is_review_passed(review):
            return [{
                "name": "generate_poem",
                "args": {
                    "theme": request["theme"],
                    "genre": request["genre"],
                    "emotion": request["emotion"],
                    "feedback": str(review.get("意见", "评审未通过，请重写。")),
                    "failed_attempts": memory,
                    "context": self._context_payload(state),
                },
            }]
        return []

    def _native_tool_calls(self, state: PoetryState) -> tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
        event_id = len(state.get("debug_events", [])) + 1
        messages = self._messages_for_native_call(state)
        tool_choice: Any = "auto"
        debug_event: Dict[str, Any] = {
            "event_id": event_id,
            "kind": "native_tool_call",
            "model": self.client.config.model_name,
            "messages": messages,
            "tools_schema": TOOL_SCHEMAS,
            "tool_choice": tool_choice,
            "raw_content": "",
            "raw_tool_calls": [],
            "parsed_tool_calls": [],
            "error": "",
        }
        if not self.client.is_initialized():
            debug_event["error"] = "OpenAI client 尚未初始化。"
            return [], "OpenAI client 尚未初始化；请检查 API_BASE_URL、MODEL_NAME 或设置 DEMO_MODE=1。", debug_event

        try:
            response = self.client.chat_completion(
                messages=messages,
                temperature=0.1,
                tools=TOOL_SCHEMAS,
                tool_choice=tool_choice,
                stream=False,
            )
        except Exception as exc:
            debug_event["error"] = str(exc)
            return [], f"原生工具调用请求失败：{exc}", debug_event
        message = response.choices[0].message
        raw_tool_calls = message.tool_calls or []
        debug_event["raw_content"] = message.content or ""
        debug_event["raw_tool_calls"] = [
            {
                "id": call.id,
                "type": call.type,
                "name": call.function.name,
                "arguments": call.function.arguments,
            }
            for call in raw_tool_calls
        ]
        if not raw_tool_calls:
            debug_event["error"] = "模型未返回 tool_calls。"
            return [], (
                "模型未返回 tool_calls。当前接口或模型可能不支持原生工具调用；"
                "请更换支持 function calling/tool use 的模型。"
            ), debug_event

        calls: List[Dict[str, Any]] = []
        for call in raw_tool_calls:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except Exception as exc:
                debug_event["error"] = f"tool arguments JSON 解析失败：{exc}"
                return [], f"模型返回了 tool_calls，但参数不是合法 JSON：{exc}", debug_event
            calls.append({
                "id": call.id,
                "name": call.function.name,
                "args": arguments,
            })
        debug_event["parsed_tool_calls"] = calls
        return calls, message.content or "模型返回原生 tool_calls。", debug_event

    def _messages_for_native_call(self, state: PoetryState) -> List[Dict[str, Any]]:
        request = state["request"]
        return [
            {
                "role": "system",
                "content": (
                    "你是唐诗创作 Agent，只能通过工具推进任务。"
                    "请根据当前状态选择 generate_poem、check_metric、lookup_rhyme 或 review_poem。"
                    "若格律或评审失败，必须参考失败记忆避免重复。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps({
                    "request": request,
                    "context_summary": state.get("context_summary", ""),
                    "attempts": state.get("attempts", []),
                    "tool_history": state.get("tool_history", []),
                    "memory": state.get("memory", []),
                    "current_poem": state.get("current_poem", ""),
                    "metric": state.get("metric", {}),
                    "review": state.get("review", {}),
                    "failed_attempts": state.get("memory", []),
                    "feedback": state.get("feedback", ""),
                    "iteration": state.get("iteration", 0),
                    "max_steps": state.get("max_steps", self.max_steps),
                }, ensure_ascii=False),
            },
        ]

    def _event_kind(self, event: Dict[str, Any]) -> str:
        if event.get("type") == "观察" and event.get("tool") == "check_metric":
            return "metric"
        if event.get("type") == "观察" and event.get("tool") == "review_poem":
            return "review"
        return "trace"

    def _result_from_state(self, state: PoetryState) -> Dict[str, Any]:
        result = {
            "request": state.get("request", {}),
            "final_poem": state.get("current_poem", ""),
            "metric": state.get("metric", {}),
            "review": state.get("review", {}),
            "trace": state.get("trace", []),
            "memory": state.get("memory", []),
            "attempts": state.get("attempts", []),
            "tool_history": state.get("tool_history", []),
            "context_summary": state.get("context_summary", ""),
            "success": bool(state.get("success", False)),
            "reason": state.get("reason", ""),
            "finish_status": state.get("finish_status", self._finish_status(state)),
            "tool_calling_error": state.get("tool_calling_error", ""),
        }
        if self.client.config.debug_mode:
            result["debug_events"] = state.get("debug_events", [])
        return result
