"""Report Writer MCP to ReportWriterService bridge."""

from __future__ import annotations

from typing import Any, Callable, Dict

from research_agent.services.report_writer import ReportWriterService


class ReportWriterServiceBridge:
    def __init__(self, service: ReportWriterService) -> None:
        self.service = service
        self._handlers: Dict[str, Callable[[Dict[str, Any]], Any]] = {
            "write_research_report": self._handle_write_research_report,
        }

    def dispatch(self, tool_name: str, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown report-writer tool: {tool_name}")
        try:
            return handler(arguments)
        except Exception as exc:
            raise RuntimeError(f"[report-writer.{tool_name}] {exc}") from exc

    def _handle_write_research_report(self, args: Dict[str, Any]) -> Any:
        return self.service.write_research_report(
            question=args["question"],
            corpus=args.get("corpus"),
            field_structure=args.get("field_structure"),
            key_papers=args.get("key_papers"),
            evidence_bundle=args.get("evidence_bundle"),
        )
