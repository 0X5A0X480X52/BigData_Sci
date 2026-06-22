"""Trace recording for auditable runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from research_agent.core.utils import utc_now_iso


@dataclass
class TraceRecorder:
    events: List[Dict[str, Any]] = field(default_factory=list)
    on_event: Optional[Callable[[Dict[str, Any]], None]] = None

    def append(self, event_type: str, node_name: str = "", **payload: Any) -> Dict[str, Any]:
        event = {"time": utc_now_iso(), "type": event_type}
        if node_name:
            event["node"] = node_name
        event.update(payload)
        self.events.append(event)
        if self.on_event:
            try:
                self.on_event(dict(event))
            except Exception:
                pass
        return event

    # ── Convenience helpers for common event types ──

    def plan_created(self, tasks: List[Dict[str, Any]], **extra: Any) -> Dict[str, Any]:
        return self.append("plan_created", tasks=tasks, **extra)

    def mode_selected(self, mode: str, reason: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("mode_selected", mode=mode, reason=reason, **extra)

    def task_started(self, task_id: str, skill: str, title: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("task_started", task_id=task_id, skill=skill, title=title, **extra)

    def task_completed(self, task_id: str, skill: str, preview: Any = None, **extra: Any) -> Dict[str, Any]:
        return self.append("task_completed", task_id=task_id, skill=skill, preview=preview, **extra)

    def task_failed(self, task_id: str, skill: str, error: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("task_failed", task_id=task_id, skill=skill, error=error, **extra)

    def tool_call(self, provider: str, tool: str, args: Any = None, **extra: Any) -> Dict[str, Any]:
        return self.append("tool_call", provider=provider, tool=tool, args=args, **extra)

    def tool_result(self, provider: str, tool: str, status: str = "completed",
                    preview: Any = None, error: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("tool_result", provider=provider, tool=tool,
                           status=status, preview=preview, error=error, **extra)

    def observation_recorded(self, observation_id: str, summary: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("observation_recorded", observation_id=observation_id, summary=summary, **extra)

    def working_memory_updated(self, key: str, **extra: Any) -> Dict[str, Any]:
        return self.append("working_memory_updated", key=key, **extra)

    def replan_triggered(self, reason: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("replan_triggered", reason=reason, **extra)

    def run_failed(self, error: str = "", **extra: Any) -> Dict[str, Any]:
        return self.append("run_failed", error=error, **extra)

