"""LLM-backed report writer with deterministic citation fallback."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from research_agent.adapters.llm_chat import LLMUnavailableError, OpenAICompatibleChatClient
from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import Corpus, EvidenceBundle, LLMReport, Paper
from research_agent.core.utils import utc_now_iso


class ReportWriterService:
    """Write a final research report from cached corpus, graph and evidence data."""

    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts

    def write_research_report(
        self,
        question: str,
        corpus: Corpus | None = None,
        field_structure: Dict[str, Any] | None = None,
        key_papers: Sequence[Dict[str, Any]] | None = None,
        evidence_bundle: EvidenceBundle | None = None,
    ) -> LLMReport:
        structure = field_structure or {}
        selected_key_papers = list(key_papers or structure.get("key_papers", []) or [])
        source_pack = self._build_source_pack(question, corpus, structure, selected_key_papers, evidence_bundle)
        citations = list(source_pack["citations"])
        warnings: List[str] = []

        try:
            markdown = self._write_with_llm(question, source_pack)
            if not markdown.strip():
                raise LLMUnavailableError("LLM returned an empty report")
        except Exception as exc:
            warnings.append(f"LLM report writer failed; used deterministic fallback: {exc}")
            markdown = self._fallback_markdown(question, source_pack)

        report = LLMReport(
            markdown=markdown,
            citations=citations,
            source_pack=source_pack,
            warnings=warnings,
        )
        self._write_artifacts(report)
        return report

    def _build_source_pack(
        self,
        question: str,
        corpus: Corpus | None,
        field_structure: Dict[str, Any],
        key_papers: Sequence[Dict[str, Any]],
        evidence_bundle: EvidenceBundle | None,
    ) -> Dict[str, Any]:
        paper_lookup = self._paper_lookup(corpus)
        evidence_by_work: Dict[str, List[Any]] = {}
        if evidence_bundle:
            for record in evidence_bundle.records:
                evidence_by_work.setdefault(record.work_id, []).append(record)

        citations: List[Dict[str, Any]] = []
        for index, paper in enumerate(key_papers[:12], 1):
            work_id = str(paper.get("work_id", ""))
            evidence_records = evidence_by_work.get(work_id, [])
            best_record = evidence_records[0] if evidence_records else None
            corpus_paper = paper_lookup.get(work_id)
            title = str(paper.get("title") or getattr(corpus_paper, "title", "") or work_id or "Untitled")
            abstract = getattr(corpus_paper, "abstract", "") if corpus_paper else ""
            snippet = ""
            score = paper.get("score", "")
            source_type = "graph_key_paper"
            if best_record:
                snippet = best_record.child_text[:500]
                score = best_record.retrieval_score
                source_type = "evidence_bundle"
            elif abstract:
                snippet = abstract[:500]
                source_type = "corpus_abstract"
            citations.append({
                "citation_id": f"E{index}",
                "source_type": source_type,
                "work_id": work_id,
                "title": title,
                "snippet": snippet,
                "score": score,
                "role": paper.get("role", ""),
                "publication_year": paper.get("publication_year"),
                "artifact_path": self._evidence_artifact_path(evidence_bundle),
            })

        topics = field_structure.get("topic_statistics", [])[:8]
        return {
            "question": question,
            "created_at": utc_now_iso(),
            "corpus": {
                "corpus_id": getattr(corpus, "corpus_id", ""),
                "query": getattr(corpus, "query", ""),
                "paper_count": len(getattr(corpus, "papers", []) or []),
            },
            "graph": {
                "snapshot_id": field_structure.get("snapshot_id", ""),
                "node_count": field_structure.get("node_count", 0),
                "edge_count": field_structure.get("edge_count", 0),
                "communities_count": field_structure.get("communities_count", 0),
                "topics": topics,
            },
            "evidence": {
                "evidence_bundle_id": getattr(evidence_bundle, "evidence_bundle_id", ""),
                "record_count": len(getattr(evidence_bundle, "records", []) or []),
            },
            "citations": citations,
        }

    def _write_with_llm(self, question: str, source_pack: Dict[str, Any]) -> str:
        client = OpenAICompatibleChatClient()
        if not client.available:
            raise LLMUnavailableError("RA_LLM_API_KEY, DEEPSEEK_API_KEY, or OPENAI_API_KEY is required")
        if client.config.demo_mode:
            raise LLMUnavailableError("RA_LLM_DEMO_MODE=1; report writer used deterministic fallback")
        compact_pack = json.dumps(source_pack, ensure_ascii=False, default=str)[:12000]
        return client.chat([
            {
                "role": "system",
                "content": (
                    "You are a scholarly research report writer. Write concise Markdown. "
                    "Use only the provided source pack. Every recommended paper must cite evidence markers like [E1]. "
                    "Do not invent papers, facts, datasets, URLs, or citation ids."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Research question: {question}\n\n"
                    f"Source pack JSON:\n{compact_pack}\n\n"
                    "Required sections: Overview, Recommended Literature, Why These Papers, Evidence Notes, Reading Route."
                ),
            },
        ], stream=False)

    def _fallback_markdown(self, question: str, source_pack: Dict[str, Any]) -> str:
        graph = source_pack.get("graph", {})
        corpus = source_pack.get("corpus", {})
        citations = source_pack.get("citations", [])
        lines = [
            f"# Research Field Guide: {question}",
            "",
            "## Overview",
            f"- Corpus: {corpus.get('paper_count', 0)} papers from query `{corpus.get('query', '')}`.",
            f"- Graph: {graph.get('node_count', 0)} nodes, {graph.get('edge_count', 0)} edges, {graph.get('communities_count', 0)} communities.",
            "",
            "## Recommended Literature",
        ]
        if citations:
            for citation in citations[:10]:
                marker = f"[{citation['citation_id']}]"
                role = citation.get("role") or "representative paper"
                score = citation.get("score", "")
                year = citation.get("publication_year") or "n.d."
                lines.append(f"- {marker} **{citation.get('title', 'Untitled')}** ({year}) ? {role}; score={score}.")
        else:
            lines.append("- No key paper citations were available.")
        lines.extend([
            "",
            "## Why These Papers",
            "The selected papers are drawn from graph key-paper ranking and available evidence snippets, so they represent central, bridging, or evidence-backed parts of the field.",
            "",
            "## Evidence Notes",
        ])
        for citation in citations[:8]:
            snippet = str(citation.get("snippet") or "No snippet available.").replace("\n", " ")[:260]
            lines.append(f"- [{citation['citation_id']}] {snippet}")
        lines.extend([
            "",
            "## Reading Route",
            "1. Start with the highest-scoring representative papers.",
            "2. Compare bridge papers across graph communities.",
            "3. Use evidence snippets to decide which full texts deserve deeper reading.",
        ])
        return "\n".join(lines)

    def _write_artifacts(self, report: LLMReport) -> None:
        self.artifacts.write_text("reports", "field_guide.md", report.markdown, "field_guide")
        self.artifacts.write_json("reports", "report_citations.json", report.citations, "report_citations", {"citations": len(report.citations)})
        self.artifacts.write_json("reports", "report_source_pack.json", report.source_pack, "report_source_pack")

    @staticmethod
    def _paper_lookup(corpus: Corpus | None) -> Dict[str, Paper]:
        papers = getattr(corpus, "papers", []) or []
        return {paper.work_id: paper for paper in papers if getattr(paper, "work_id", "")}

    @staticmethod
    def _evidence_artifact_path(evidence_bundle: EvidenceBundle | None) -> str:
        if not evidence_bundle:
            return ""
        return f"evidence/{evidence_bundle.evidence_bundle_id}.json"
