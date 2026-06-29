"""ReAct mode nodes for the research agent StateGraph.

Nodes: think → act → observe → evaluate → (loop | synthesize)

The think node can use either:
- LLM-driven reasoning (FeatureFlags.llm_driven_react=True)
- Deterministic rule-based fallback (default, always available offline)

MCPManager.call() returns the raw result for backward compat.
The act_node uses call_with_result() to capture both raw value and MCPResult.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal

from research_agent.adapters.llm_chat import LLMUnavailableError, OpenAICompatibleChatClient
from research_agent.core.models import MCPResult, Observation, ToolCall
from research_agent.core.utils import stable_hash, utc_now_iso


def think_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Decide next action based on current observations and working memory."""
    config = state.get("config")
    use_llm = getattr(config.features, "llm_driven_react", False) if config else False
    if use_llm:
        return _llm_think(state)
    return _rule_based_think(state)


def act_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Execute the pending tool call via MCPManager.call_with_result().

    This captures both the raw domain object (Corpus, dict, etc.) and the
    structured MCPResult in one call.  On failure the MCPResult is stored
    with status="failed" and the loop continues (evaluate_node decides retry).
    """
    pending: ToolCall | None = state.get("pending_action")
    mcp = state.get("mcp")
    run_id: str = state.get("run_id", "")
    task_id: str = state.get("current_task_id", "react")

    if pending is None:
        return {"last_event": {"type": "act_skipped", "reason": "no pending action"}}

    raw_result, mcp_result = mcp.call_with_result(
        provider=pending.provider,
        tool=pending.tool,
        run_id=run_id,
        task_id=task_id,
        **pending.args,
    )
    pending.tool_call_id = mcp_result.tool_call_id
    return {
        "pending_action": pending,
        "last_raw_result": raw_result,          # the domain object (Corpus, dict, …)
        "last_mcp_result": mcp_result,
        "last_event": {"type": "act_completed", "tool_call_id": mcp_result.tool_call_id,
                       "status": mcp_result.status},
    }


def observe_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Process the raw result + MCPResult into an Observation and update memory."""
    mcp_result: MCPResult | None = state.get("last_mcp_result")
    raw_result: Any = state.get("last_raw_result")
    pending: ToolCall | None = state.get("pending_action")
    trace = state.get("trace")

    if mcp_result is None:
        return {}

    # Build observation
    observation = Observation.from_mcp_result(
        mcp_result,
        summary=f"[{mcp_result.provider}.{mcp_result.method.get('name', 'unknown')}] "
                f"status={mcp_result.status}",
    )

    observations: List[Observation] = list(state.get("observations", []))
    observations.append(observation)

    # Update working memory (use raw_result for actual data)
    memory: Dict[str, Any] = dict(state.get("working_memory", {}))
    _update_memory_from_result(memory, pending, raw_result, mcp_result)

    if trace and hasattr(trace, 'observation_recorded'):
        trace.observation_recorded(
            observation_id=observation.observation_id,
            summary=observation.summary,
        )

    return {
        "observations": observations,
        "working_memory": memory,
        "last_event": {"type": "observation_recorded", "observation_id": observation.observation_id},
    }


def evaluate_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate the last step and decide: continue, retry, or finish."""
    mcp_result: MCPResult | None = state.get("last_mcp_result")
    budget = state.get("budget")
    observations: List[Observation] = list(state.get("observations", []))

    # Check iteration budget
    if budget:
        try:
            budget.consume_iteration()
        except Exception:
            return {"done": True, "success": len(observations) > 0,
                    "reason": "iteration budget exhausted",
                    "last_event": {"type": "evaluate_done", "reason": "budget_exhausted"}}

    # Handle tool failure — store warning but keep going (don't consume retry here;
    # retries are for explicit re-attempts of the same action)
    if mcp_result and mcp_result.status == "failed":
        warnings: List[str] = list(state.get("warnings", []))
        warnings.append(f"Tool {mcp_result.provider}.{mcp_result.method.get('name', '?')} failed: {mcp_result.error}")
        return {
            "warnings": warnings,
            "last_event": {"type": "evaluate_warning", "error": mcp_result.error},
        }

    # Check if all stages are complete
    memory: Dict[str, Any] = state.get("working_memory", {})
    if _all_stages_complete(memory):
        return {"done": True, "success": True, "reason": "all research stages complete",
                "last_event": {"type": "evaluate_done", "reason": "stages_complete"}}

    return {
        "last_event": {"type": "evaluate_continue", "observations_count": len(observations)},
    }


def synthesize_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Final synthesis: produce the field guide from accumulated working memory."""
    config = state.get("config")
    if getattr(getattr(config, "features", None), "llm_report_writer", False):
        llm_result = _try_llm_report_synthesis(state)
        if llm_result is not None:
            return llm_result

    memory: Dict[str, Any] = state.get("working_memory", {})
    question: str = state.get("question", "")
    observations: List[Observation] = list(state.get("observations", []))

    parts: List[str] = [f"# Research Field Guide: {question}\n"]

    # Scope
    scope = memory.get("scope", {})
    if scope:
        field_def = scope.get("field_definition", "") or str(scope)
        parts.append(f"\n## Scope\n- {field_def}\n")

    # Key papers from field structure
    field_structure = memory.get("field_structure", {})
    key_papers = field_structure.get("key_papers", memory.get("key_papers", []))
    if key_papers:
        parts.append(f"\n## Key Papers ({len(key_papers)} identified)\n")
        for i, kp in enumerate(key_papers[:10], 1):
            title = kp.get("title", "Untitled") if isinstance(kp, dict) else str(kp)[:80]
            score = kp.get("score", "N/A") if isinstance(kp, dict) else "N/A"
            role = kp.get("role", "N/A") if isinstance(kp, dict) else "N/A"
            parts.append(f"{i}. **{title}** (score: {score}, role: {role})")

    # Evidence
    evidence = memory.get("evidence_bundle")
    if evidence:
        record_count = len(getattr(evidence, 'records', [])) if hasattr(evidence, 'records') else 0
        parts.append(f"\n## Evidence Summary\n- {record_count} evidence records collected")

    parts.append(f"\n## Run Statistics\n- Observations: {len(observations)}")
    parts.append(f"- Data collected at: {utc_now_iso()}")

    field_guide = "\n".join(parts)

    return {
        "field_guide": field_guide,
        "done": True,
        "success": True,
        "last_event": {"type": "synthesize_complete", "guide_length": len(field_guide)},
    }



def _try_llm_report_synthesis(state: Dict[str, Any]) -> Dict[str, Any] | None:
    memory: Dict[str, Any] = state.get("working_memory", {})
    mcp = state.get("mcp")
    if mcp is None:
        return None
    try:
        field_structure = memory.get("field_structure", {})
        report = mcp.call(
            "report-writer",
            "write_research_report",
            run_id=state.get("run_id", ""),
            task_id=state.get("current_task_id", "react_synthesize"),
            question=state.get("question", ""),
            corpus=memory.get("field_corpus"),
            field_structure=field_structure,
            key_papers=memory.get("key_papers", field_structure.get("key_papers", [])),
            evidence_bundle=memory.get("evidence_bundle"),
        )
        warnings = list(state.get("warnings", []))
        warnings.extend(getattr(report, "warnings", []) or [])
        return {
            "field_guide": report.markdown,
            "llm_report": report,
            "report_citations": report.citations,
            "warnings": warnings,
            "done": True,
            "success": True,
            "last_event": {"type": "llm_report_synthesize_complete", "citations": len(report.citations)},
        }
    except Exception as exc:
        warnings = list(state.get("warnings", []))
        warnings.append(f"LLM report writer failed; used ReAct template synthesis: {exc}")
        state["warnings"] = warnings
        return None


# Routing helpers

def route_after_think(state: Dict[str, Any]) -> Literal["act", "synthesize"]:
    if state.get("done"):
        return "synthesize"
    if state.get("pending_action") is None:
        return "synthesize"
    return "act"


def route_after_evaluate(state: Dict[str, Any]) -> Literal["think", "synthesize"]:
    if state.get("done"):
        return "synthesize"
    return "think"


# ── Rule-based think (deterministic fallback) ───────────────

def _rule_based_think(state: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic progression through the 4-stage research pipeline.

    Stage 0: Scope the field (scholarly-data.get_corpus_summary — lightweight)
    Stage 1: Build corpus (scholarly-data.create_field_corpus)
    Stage 2: Map field structure (graph-analytics.map_field_structure)
    Stage 3: Build evidence bundle (evidence-rag.build_evidence_bundle)

    Does NOT require LLM.  Mirrors the original 7-skill pipeline but
    skips skills that do only local computation (perspectives,
    identify_key_papers, generate_field_guide).
    """
    memory: Dict[str, Any] = state.get("working_memory", {})
    config = state.get("config")
    question: str = state.get("question", "")
    openalex_query = _openalex_query(state)
    alternate_queries = _alternate_openalex_queries(state)
    max_pdfs = config.max_pdfs if config else 5

    # Stage 0: Scope the field — build a small initial corpus to define boundaries
    if "scope_corpus" not in memory:
        return {
            "thought": "Scope the unfamiliar research field — build a small initial paper set.",
            "pending_action": ToolCall(
                provider="scholarly-data", tool="create_field_corpus",
                args={"query": openalex_query,
                      "max_results": min(config.max_field_corpus if config else 100, 20),
                      "alternate_queries": alternate_queries},
            ),
        }

    # Stage 1: Build the full OpenAlex field corpus
    if "field_corpus" not in memory:
        return {
            "thought": "Build the OpenAlex field corpus to collect relevant papers.",
            "pending_action": ToolCall(
                provider="scholarly-data", tool="create_field_corpus",
                args={"query": openalex_query,
                      "max_results": config.max_field_corpus if config else 100,
                      "alternate_queries": alternate_queries},
            ),
        }

    # Stage 2: Map field structure (graph analytics)
    if "field_structure" not in memory:
        corpus = memory.get("field_corpus")
        if corpus is None:
            return {"thought": "No corpus available — cannot map field structure.", "done": True}
        return {
            "thought": "Map topics, yearly trends, graph communities, and rank key papers.",
            "pending_action": ToolCall(
                provider="graph-analytics", tool="map_field_structure",
                args={"corpus": corpus},
            ),
        }

    # Stage 3: Build evidence bundle from key papers
    if "evidence_bundle" not in memory:
        field_structure = memory.get("field_structure", {})
        key_papers = field_structure.get("key_papers", [])[:max_pdfs]
        if not key_papers:
            key_papers = memory.get("key_papers", [])[:max_pdfs]
        if key_papers:
            work_ids = [p.get("work_id", "") for p in key_papers if isinstance(p, dict) and p.get("work_id")]
            if work_ids:
                return {
                    "thought": f"Retrieve evidence for {len(work_ids)} key papers.",
                    "pending_action": ToolCall(
                        provider="evidence-rag", tool="build_evidence_bundle",
                        args={"question": question, "work_ids": work_ids},
                    ),
                }

    # All stages complete
    return {"thought": "All research stages complete — ready to synthesize.", "done": True}


def _llm_think(state: Dict[str, Any]) -> Dict[str, Any]:
    """Use an OpenAI-compatible LLM to choose the next research action.

    The model chooses among safe, state-aware candidates. Raw domain objects
    such as Corpus are injected by this node rather than serialized through the
    model. If the LLM is unavailable or returns an invalid action, the offline
    deterministic policy is used.
    """
    candidates = _llm_action_candidates(state)
    if not candidates:
        return {"thought": "All research stages complete — ready to synthesize.", "done": True}

    try:
        client = OpenAICompatibleChatClient()
        memory = state.get("working_memory", {})
        user = {
            "question": state.get("question", ""),
            "completed_stages": sorted(memory.keys()),
            "candidate_actions": [
                {"id": c["id"], "provider": c["provider"], "tool": c["tool"], "description": c["description"]}
                for c in candidates
            ],
        }
        response = client.complete_json(
            system=(
                "You are a cautious research agent controller. Select exactly one "
                "candidate action that advances the scholarly research workflow."
            ),
            user=str(user),
            schema_hint='{"action_id":"one candidate id","thought":"short rationale"}',
        )
        action_id = str(response.get("action_id", ""))
        selected = next((candidate for candidate in candidates if candidate["id"] == action_id), None)
        if selected is None:
            raise LLMUnavailableError(f"unknown action_id: {action_id}")
        return {
            "thought": str(response.get("thought") or selected["description"]),
            "pending_action": ToolCall(
                provider=selected["provider"],
                tool=selected["tool"],
                args=selected["args"],
            ),
        }
    except Exception as exc:
        warnings: List[str] = list(state.get("warnings", []))
        warnings.append(f"LLM ReAct selection fell back to deterministic policy: {exc}")
        fallback = _rule_based_think(state)
        fallback["warnings"] = warnings
        return fallback


def _llm_action_candidates(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    memory: Dict[str, Any] = state.get("working_memory", {})
    config = state.get("config")
    question: str = state.get("question", "")
    openalex_query = _openalex_query(state)
    alternate_queries = _alternate_openalex_queries(state)
    max_pdfs = config.max_pdfs if config else 5

    if "scope_corpus" not in memory:
        return [{
            "id": "scope_corpus",
            "provider": "scholarly-data",
            "tool": "create_field_corpus",
            "description": "Build a small initial paper set to scope the field.",
            "args": {"query": openalex_query, "max_results": min(config.max_field_corpus if config else 100, 20),
                      "alternate_queries": alternate_queries},
        }]
    if "field_corpus" not in memory:
        return [{
            "id": "field_corpus",
            "provider": "scholarly-data",
            "tool": "create_field_corpus",
            "description": "Build the full OpenAlex-style field corpus.",
            "args": {"query": openalex_query, "max_results": config.max_field_corpus if config else 100,
                      "alternate_queries": alternate_queries},
        }]
    if "field_structure" not in memory:
        return [{
            "id": "map_structure",
            "provider": "graph-analytics",
            "tool": "map_field_structure",
            "description": "Run graph analytics and identify key papers.",
            "args": {"corpus": memory["field_corpus"]},
        }]
    if "evidence_bundle" not in memory:
        field_structure = memory.get("field_structure", {})
        key_papers = field_structure.get("key_papers", memory.get("key_papers", []))[:max_pdfs]
        work_ids = [p.get("work_id", "") for p in key_papers if isinstance(p, dict) and p.get("work_id")]
        if work_ids:
            return [{
                "id": "build_evidence",
                "provider": "evidence-rag",
                "tool": "build_evidence_bundle",
                "description": "Retrieve and bundle evidence for selected key papers.",
                "args": {"question": question, "work_ids": work_ids},
            }]
    return []


def _alternate_openalex_queries(state: Dict[str, Any]) -> List[str]:
    query_plan = state.get("openalex_query_plan", {}) or {}
    alternates = query_plan.get("alternate_queries") or []
    return [str(item) for item in alternates if str(item).strip()]

def _openalex_query(state: Dict[str, Any]) -> str:
    query_plan = state.get("openalex_query_plan", {}) or {}
    return str(query_plan.get("primary_query") or state.get("openalex_query") or state.get("question", ""))
# ── Working memory updater ──────────────────────────────────

def _update_memory_from_result(memory: Dict[str, Any], pending: ToolCall | None,
                               raw_result: Any, mcp_result: MCPResult) -> None:
    """Store the raw domain object in working memory, keyed by tool type."""
    if pending is None or mcp_result.status != "completed" or raw_result is None:
        return

    provider = pending.provider
    tool = pending.tool

    if provider == "scholarly-data":
        if tool == "create_field_corpus":
            # Stage 0 (scope) → scope_corpus; Stage 1 (full) → field_corpus
            if "scope_corpus" not in memory:
                memory["scope_corpus"] = raw_result   # initial small corpus
            else:
                memory["field_corpus"] = raw_result    # full corpus
        elif tool == "create_seed_lineage_corpus":
            memory["field_corpus"] = raw_result
    elif provider == "graph-analytics":
        if tool == "map_field_structure":
            memory["field_structure"] = raw_result  # dict with key_papers, communities, etc.
    elif provider == "evidence-rag":
        if tool == "build_evidence_bundle":
            memory["evidence_bundle"] = raw_result   # EvidenceBundle object
        elif tool == "search_paper_evidence":
            memory.setdefault("evidence_records", []).extend(
                raw_result if isinstance(raw_result, list) else []
            )


def _all_stages_complete(memory: Dict[str, Any]) -> bool:
    """Check if all core research stages have produced data."""
    return bool(
        memory.get("scope_corpus") and
        memory.get("field_corpus") and
        memory.get("field_structure") and
        memory.get("evidence_bundle")
    )





