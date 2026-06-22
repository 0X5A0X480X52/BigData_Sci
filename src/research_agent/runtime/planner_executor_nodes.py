"""Planner–Executor mode nodes for the research agent StateGraph.

Nodes: plan → select_task → execute_task → evaluate_task → (replan | synthesize)
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from research_agent.adapters.llm_chat import OpenAICompatibleChatClient
from research_agent.core.models import TaskStatus
from research_agent.skills import SKILLS

from .planner import Task, build_default_plan


def plan_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate the task plan (LLM-driven or default template)."""
    config = state.get("config")
    question: str = state.get("question", "")
    seed_work_id: str | None = state.get("seed_work_id")
    use_llm = getattr(config.features, "llm_driven_plan", False) if config else False
    query_plan = state.get("openalex_query_plan", {"primary_query": question})
    search_query = str(query_plan.get("primary_query") or question)

    if use_llm:
        plan = _llm_generate_plan(state)
    else:
        plan = build_default_plan(question, seed_work_id, search_query=search_query)

    # Persist plan to trace and artifact store
    trace = state.get("trace")
    if trace and hasattr(trace, 'plan_created'):
        trace.plan_created(tasks=[asdict(task) for task in plan])

    artifact_store = state.get("artifact_store")
    if artifact_store:
        artifact_store.write_json("reports", "plan.json", [asdict(task) for task in plan], "agent_plan")

    # Save tasks to repository
    repo = state.get("repository")
    run_id = state.get("run_id", "")
    if repo:
        for task in plan:
            try:
                repo.save_task(run_id, task)
            except Exception:
                pass

    return {
        "plan": plan,
        "current_task_index": 0,
        "task_results": [],
        "last_event": {"type": "plan_created", "task_count": len(plan)},
    }


def select_task_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Select the next READY task from the plan (respecting dependency order).

    When no task is explicitly ready but some are still pending, this node
    auto-selects the first uncompleted task to ensure forward progress.
    """
    plan: List[Task] = list(state.get("plan", []))
    task_results: List[Any] = list(state.get("task_results", []))

    completed_task_ids = {tr.task_id for tr in task_results if hasattr(tr, 'status') and
                          tr.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)}

    # First pass: find a task with all deps met
    for i, task in enumerate(plan):
        if hasattr(task, 'status') and task.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.FAILED):
            continue
        deps_met = all(
            dep in completed_task_ids
            for dep in (task.depends_on if hasattr(task, 'depends_on') else [])
        )
        if deps_met:
            if hasattr(task, 'status'):
                task.status = TaskStatus.READY
            return {
                "current_task_index": i,
                "plan": plan,
                "last_event": {"type": "task_selected", "task_id": task.task_id, "skill": task.skill},
            }

    # Second pass: any non-completed task (force forward progress)
    for i, task in enumerate(plan):
        if hasattr(task, 'status') and task.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED, TaskStatus.FAILED):
            continue
        if hasattr(task, 'status'):
            task.status = TaskStatus.READY
        return {
            "current_task_index": i,
            "plan": plan,
            "last_event": {"type": "task_selected", "task_id": task.task_id, "skill": task.skill},
        }

    # All tasks are terminal
    return {
        "done": True,
        "plan": plan,
        "last_event": {"type": "all_tasks_terminal"},
    }


def execute_task_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the currently selected task using its registered skill function."""
    plan: List[Task] = state.get("plan", [])
    idx: int = state.get("current_task_index", 0)
    mcp = state.get("mcp")

    if idx >= len(plan):
        return {"last_event": {"type": "execute_skipped", "reason": "index out of range"}}

    task = plan[idx]
    skill_fn = SKILLS.get(task.skill)
    if skill_fn is None:
        if hasattr(task, 'status'):
            task.status = TaskStatus.FAILED
        return {
            "plan": plan,
            "last_event": {"type": "execute_failed", "task_id": task.task_id,
                           "error": f"Unknown skill: {task.skill}"},
        }

    trace = state.get("trace")
    if trace and hasattr(trace, 'task_started'):
        trace.task_started(task_id=task.task_id, skill=task.skill, title=task.title)

    if hasattr(task, 'status'):
        task.status = TaskStatus.RUNNING

    shared_state: Dict[str, Any] = state.get("shared_skill_state", {})
    try:
        result = skill_fn(shared_state, mcp, task)
        if hasattr(task, 'status'):
            task.status = TaskStatus.COMPLETED
        if trace and hasattr(trace, 'task_completed'):
            trace.task_completed(task_id=task.task_id, skill=task.skill, preview=str(result)[:200])
        return {
            "plan": plan,
            "shared_skill_state": shared_state,
            "last_event": {"type": "execute_complete", "task_id": task.task_id, "skill": task.skill},
        }
    except Exception as exc:
        if hasattr(task, 'status'):
            task.status = TaskStatus.FAILED
            task.retries = getattr(task, 'retries', 0) + 1
        if trace and hasattr(trace, 'task_failed'):
            trace.task_failed(task_id=task.task_id, skill=task.skill, error=str(exc))
        return {
            "plan": plan,
            "last_event": {"type": "execute_failed", "task_id": task.task_id, "error": str(exc)},
        }


def evaluate_task_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate the just-completed task; determine if replan is needed."""
    plan: List[Task] = state.get("plan", [])
    idx: int = state.get("current_task_index", 0)
    last_event: Dict[str, Any] = state.get("last_event", {})

    if idx < len(plan):
        task = plan[idx]
        if hasattr(task, 'status') and task.status == TaskStatus.FAILED:
            max_retries = state.get("config").max_retries if state.get("config") else 2
            if getattr(task, 'retries', 0) < max_retries:
                return {"needs_replan": False,
                        "last_event": {"type": "task_retry", "task_id": task.task_id}}

    return {
        "needs_replan": False,
        "last_event": {"type": "task_evaluated", "task_index": idx},
    }


def replan_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Modify the remaining plan in response to a task failure."""
    plan: List[Task] = state.get("plan", [])
    idx: int = state.get("current_task_index", 0)
    trace = state.get("trace")

    reason = "Task failure triggered replan"
    if idx < len(plan):
        task = plan[idx]
        reason = f"Task {task.task_id} ({task.skill}) failed — marking remaining dependent tasks as skipped"
        # Skip tasks that depend on the failed one
        for t in plan[idx + 1:]:
            if task.task_id in (t.depends_on if hasattr(t, 'depends_on') else []):
                if hasattr(t, 'status'):
                    t.status = TaskStatus.SKIPPED

    if trace and hasattr(trace, 'replan_triggered'):
        trace.replan_triggered(reason=reason)

    replan_count: int = state.get("replan_count", 0) + 1
    return {
        "plan": plan,
        "replan_count": replan_count,
        "last_event": {"type": "replan_complete", "reason": reason},
    }


def synthesize_pe_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize final output from Planner–Executor results."""
    shared_state: Dict[str, Any] = state.get("shared_skill_state", {})
    question: str = state.get("question", "")
    plan: List[Task] = state.get("plan", [])

    field_guide = shared_state.get("field_guide", "")
    if not field_guide:
        # Build a minimal summary from task results
        completed = [t for t in plan if hasattr(t, 'status') and t.status == TaskStatus.COMPLETED]
        field_guide = (
            f"# Research Summary: {question}\n\n"
            f"Completed {len(completed)}/{len(plan)} tasks.\n\n"
        )
        for t in completed:
            field_guide += f"- ✅ {t.title}\n"

    artifact_store = state.get("artifact_store")
    if artifact_store and field_guide:
        artifact_store.write_text("reports", "field_guide.md", field_guide, "field_guide")

    return {
        "field_guide": field_guide,
        "done": True,
        "success": True,
        "last_event": {"type": "synthesize_complete"},
    }


# ── Routing helpers ─────────────────────────────────────────

def route_after_select(state: Dict[str, Any]) -> str:
    if state.get("done"):
        return "synthesize"
    return "execute"


def route_after_evaluate_pe(state: Dict[str, Any]) -> str:
    if state.get("needs_replan"):
        return "replan"
    # After evaluating a task, always go back to select for the next one
    return "select"


def route_after_replan(state: Dict[str, Any]) -> str:
    return "select"


# ── LLM plan generation (placeholder) ───────────────────────

def _llm_generate_plan(state: Dict[str, Any]) -> List[Task]:
    """Generate a plan with an OpenAI-compatible LLM, falling back safely."""
    question = state.get("question", "")
    seed_work_id = state.get("seed_work_id")
    query_plan = state.get("openalex_query_plan", {"primary_query": question})
    default_plan = build_default_plan(question, seed_work_id, search_query=str(query_plan.get("primary_query") or question))
    try:
        client = OpenAICompatibleChatClient()
        response = client.complete_json(
            system=(
                "You are a scholarly research workflow planner. Produce a compact "
                "task plan using only the registered skills. Keep dependencies acyclic."
            ),
            user=str({
                "question": question,
                "seed_work_id": seed_work_id,
                "allowed_skills": sorted(SKILLS.keys()),
                "default_plan": [asdict(task) for task in default_plan],
            }),
            schema_hint=(
                '{"tasks":[{"task_id":"T1","skill":"scope_new_field",'
                '"title":"short title","depends_on":[],"parameters":{}}]}'
            ),
        )
        raw_tasks = response.get("tasks", [])
        parsed: List[Task] = []
        seen_ids = set()
        for item in raw_tasks:
            if not isinstance(item, dict):
                continue
            task_id = str(item.get("task_id") or f"T{len(parsed) + 1}")
            skill = str(item.get("skill") or "")
            if task_id in seen_ids or skill not in SKILLS:
                continue
            depends_on = [str(dep) for dep in item.get("depends_on", []) if str(dep) != task_id]
            parameters = item.get("parameters", {}) if isinstance(item.get("parameters", {}), dict) else {}
            if skill == "build_research_corpus" and not parameters.get("query"):
                parameters["query"] = str(query_plan.get("primary_query") or question)
                parameters["alternate_queries"] = query_plan.get("alternate_queries") or []
            parsed.append(Task(
                task_id=task_id,
                skill=skill,
                title=str(item.get("title") or skill.replace("_", " ").title()),
                depends_on=depends_on,
                parameters=parameters,
            ))
            seen_ids.add(task_id)
        if parsed and any(task.skill == "generate_field_guide" for task in parsed):
            return parsed
    except Exception:
        pass
    return default_plan



