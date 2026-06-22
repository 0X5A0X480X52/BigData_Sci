"""ResearchGraphAgent: dual-mode research agent with deterministic fallback."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import (
    AgentMode,
    MCPResult,
    Observation,
    ResearchRun,
    RunConfig,
    TaskResult,
    TaskStatus,
    ToolCall,
    to_dict,
)
from research_agent.core.utils import stable_hash, utc_now_iso
from research_agent.persistence.repository import ResearchRepository

from .budget import BudgetTracker
from .mcp_manager import MCPManager
from .planner_executor_nodes import (
    evaluate_task_node,
    execute_task_node,
    plan_node,
    replan_node,
    route_after_evaluate_pe,
    route_after_replan,
    route_after_select,
    select_task_node,
    synthesize_pe_node,
)
from .react_nodes import (
    act_node,
    evaluate_node,
    observe_node,
    route_after_evaluate,
    route_after_think,
    synthesize_node,
    think_node,
)
from .trace import TraceRecorder

try:
    from langgraph.graph import END, StateGraph

    _LANGGRAPH_AVAILABLE = True
except ImportError:
    END = "__end__"
    StateGraph = None
    _LANGGRAPH_AVAILABLE = False


class AgentState(TypedDict, total=False):
    run_id: str
    question: str
    seed_work_id: Optional[str]
    config: RunConfig
    mode: str
    thought: str
    pending_action: Optional[ToolCall]
    last_mcp_result: Optional[MCPResult]
    observations: List[Observation]
    working_memory: Dict[str, Any]
    plan: List[Any]
    current_task_index: int
    current_task_id: str
    task_results: List[TaskResult]
    shared_skill_state: Dict[str, Any]
    replan_count: int
    needs_replan: bool
    mcp: Any
    artifact_store: Any
    repository: Any
    budget: Any
    trace: Any
    iteration: int
    done: bool
    success: bool
    reason: str
    warnings: List[str]
    field_guide: str
    last_event: Dict[str, Any]
    last_raw_result: Any


class ResearchGraphAgent:
    """Runs the research workflow in ReAct or Planner-Executor mode."""

    def __init__(
        self,
        config: RunConfig,
        mcp: MCPManager,
        repository: Optional[ResearchRepository] = None,
        artifact_root: str = "artifacts",
        trace: Optional[TraceRecorder] = None,
        budget: Optional[BudgetTracker] = None,
        services: Optional[tuple] = None,
    ) -> None:
        self.config = config
        self.mcp = mcp
        self.repository = repository
        self.artifact_root = artifact_root
        self.trace = trace or TraceRecorder()
        self.budget = budget or BudgetTracker(config)
        self._services = services

    def run(
        self,
        question: str,
        seed_work_id: Optional[str] = None,
        openalex_query_plan: Optional[Dict[str, Any]] = None,
    ) -> ResearchRun:
        run_id = f"AR_{stable_hash({'question': question, 'seed': seed_work_id, 'time': utc_now_iso()}, 12)}"
        mode: AgentMode = self.config.agent_mode
        query_plan = openalex_query_plan or {"original_question": question, "primary_query": question, "alternate_queries": [], "keywords": [], "method": "raw"}
        openalex_query = str(query_plan.get("primary_query") or question)
        artifact_store = ArtifactStore(self.artifact_root, run_id)

        if self._services:
            for svc in self._services:
                svc.artifacts = artifact_store
                try:
                    svc.analysis_run_id = run_id
                except Exception:
                    pass

        run = ResearchRun(
            run_id=run_id,
            question=question,
            config=self.config,
            status="running",
            agent_mode=mode,
        )

        if self.repository:
            try:
                self.repository.create_run(run)
            except Exception:
                pass

        self.trace.mode_selected(mode=mode, reason=f"config.agent_mode={mode}")

        state: AgentState = {
            "run_id": run_id,
            "question": question,
            "seed_work_id": seed_work_id,
            "openalex_query": openalex_query,
            "openalex_query_plan": query_plan,
            "config": self.config,
            "mode": mode,
            "thought": "",
            "pending_action": None,
            "last_mcp_result": None,
            "observations": [],
            "working_memory": {},
            "plan": [],
            "current_task_index": 0,
            "current_task_id": "",
            "task_results": [],
            "shared_skill_state": self._base_skill_state(run_id, question, seed_work_id, artifact_store, query_plan),
            "replan_count": 0,
            "needs_replan": False,
            "mcp": self.mcp,
            "artifact_store": artifact_store,
            "repository": self.repository,
            "budget": self.budget,
            "trace": self.trace,
            "iteration": 0,
            "done": False,
            "success": False,
            "reason": "",
            "warnings": [],
            "field_guide": "",
            "last_event": {},
        }

        try:
            if _LANGGRAPH_AVAILABLE and self.config.features.use_langgraph_runtime:
                final_state = self._run_graph(state, mode)
            else:
                final_state = self._run_dag_fallback(state)
        except Exception as exc:
            run.status = "failed"
            self.trace.run_failed(error=str(exc))
            self._finalize_run(run, state)
            raise

        run.status = "completed" if final_state.get("success") else "failed"
        self._finalize_run(run, final_state)
        return run

    def _base_skill_state(
        self,
        run_id: str,
        question: str,
        seed_work_id: Optional[str],
        artifact_store: Optional[ArtifactStore],
        openalex_query_plan: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        query_plan = openalex_query_plan or {"primary_query": question}
        return {
            "run_id": run_id,
            "question": question,
            "seed_work_id": seed_work_id,
            "config": self.config,
            "artifact_store": artifact_store,
            "repository": self.repository,
            "openalex_query_plan": query_plan,
            "openalex_query": str(query_plan.get("primary_query") or question),
        }
    def _run_graph(self, state: AgentState, mode: str) -> AgentState:
        if mode == "planner_executor":
            graph = self._build_pe_graph()
        else:
            graph = self._build_react_graph()
        result = graph.invoke(state)
        return {**state, **result}  # type: ignore[typeddict-item]

    def _run_dag_fallback(self, state: AgentState) -> AgentState:
        from research_agent.skills import SKILLS

        from .planner import build_default_plan

        question = state.get("question", "")
        seed = state.get("seed_work_id")
        query_plan = state.get("openalex_query_plan", {"primary_query": question})
        plan = build_default_plan(question, seed, search_query=str(query_plan.get("primary_query") or question))
        shared = self._base_skill_state(
            run_id=state.get("run_id", ""),
            question=question,
            seed_work_id=seed,
            artifact_store=state.get("artifact_store"),
            openalex_query_plan=query_plan,
        )
        task_results: List[TaskResult] = []

        for task in plan:
            self.trace.task_started(task_id=task.task_id, skill=task.skill, title=task.title)
            before_results = len(self.mcp.results)
            try:
                task.result = SKILLS[task.skill](shared, self.mcp, task)
                task.status = TaskStatus.COMPLETED
                task_results.append(
                    TaskResult(
                        task_id=task.task_id,
                        skill=task.skill,
                        status=TaskStatus.COMPLETED,
                        mcp_results=self.mcp.results[before_results:],
                        completed_at=utc_now_iso(),
                    )
                )
                self.trace.task_completed(task_id=task.task_id, skill=task.skill)
            except Exception as exc:
                task.status = TaskStatus.FAILED
                task_results.append(
                    TaskResult(
                        task_id=task.task_id,
                        skill=task.skill,
                        status=TaskStatus.FAILED,
                        error=str(exc),
                        completed_at=utc_now_iso(),
                    )
                )
                self.trace.task_failed(task_id=task.task_id, skill=task.skill, error=str(exc))

        success = bool(shared.get("field_guide")) and all(
            result.status != TaskStatus.FAILED for result in task_results
        )
        return {
            **state,
            "plan": plan,
            "shared_skill_state": shared,
            "task_results": task_results,
            "field_guide": shared.get("field_guide", ""),
            "done": True,
            "success": success,
        }

    def _build_react_graph(self):
        if StateGraph is None:
            raise ImportError("langgraph is required for ReAct mode")
        graph = StateGraph(AgentState)
        graph.add_node("think", think_node)
        graph.add_node("act", act_node)
        graph.add_node("observe", observe_node)
        graph.add_node("evaluate", evaluate_node)
        graph.add_node("synthesize", synthesize_node)
        graph.set_entry_point("think")
        graph.add_conditional_edges("think", route_after_think, {"act": "act", "synthesize": "synthesize"})
        graph.add_edge("act", "observe")
        graph.add_edge("observe", "evaluate")
        graph.add_conditional_edges("evaluate", route_after_evaluate, {"think": "think", "synthesize": "synthesize"})
        graph.add_edge("synthesize", END)
        return graph.compile()

    def _build_pe_graph(self):
        if StateGraph is None:
            raise ImportError("langgraph is required for Planner-Executor mode")
        graph = StateGraph(AgentState)
        graph.add_node("plan", plan_node)
        graph.add_node("select", select_task_node)
        graph.add_node("execute", execute_task_node)
        graph.add_node("evaluate_task", evaluate_task_node)
        graph.add_node("replan", replan_node)
        graph.add_node("synthesize", synthesize_pe_node)
        graph.set_entry_point("plan")
        graph.add_edge("plan", "select")
        graph.add_conditional_edges("select", route_after_select, {"execute": "execute", "synthesize": "synthesize"})
        graph.add_edge("execute", "evaluate_task")
        graph.add_conditional_edges(
            "evaluate_task",
            route_after_evaluate_pe,
            {"select": "select", "replan": "replan", "synthesize": "synthesize"},
        )
        graph.add_conditional_edges("replan", route_after_replan, {"select": "select"})
        graph.add_edge("synthesize", END)
        return graph.compile()

    def _finalize_run(self, run: ResearchRun, state: AgentState) -> None:
        artifact_store: ArtifactStore | None = state.get("artifact_store")
        run.trace = self.trace.events
        run.results = list(self.mcp.results)
        run.task_results = list(state.get("task_results", []))
        run.completed_at = utc_now_iso()

        if artifact_store:
            artifact_store.write_json("reports", "trace.json", self.trace.events, "agent_trace", {"events": len(self.trace.events)})
            field_guide = state.get("field_guide", "")
            if field_guide:
                artifact_store.write_text("reports", "field_guide.md", field_guide, "field_guide")
            run.artifacts = list(artifact_store.refs)
            artifact_store.write_json("reports", "run.json", to_dict(run), "agent_run", {"status": run.status})
            run.artifacts = list(artifact_store.refs)

        if self.repository:
            try:
                for task_result in run.task_results:
                    self.repository.save_task_result(run.run_id, task_result)
                for mcp_result in run.results:
                    self.repository.save_mcp_result(mcp_result)
                if hasattr(self.repository, "save_run_outputs"):
                    self.repository.save_run_outputs(run)
                else:
                    self.repository.update_run_status(run.run_id, run.status, run.completed_at)
            except Exception:
                pass






