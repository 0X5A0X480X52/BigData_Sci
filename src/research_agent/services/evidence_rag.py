"""Parent-child evidence retrieval with local vector fallback."""

from __future__ import annotations

import math
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import ChildChunk, EvidenceBundle, EvidenceRecord, MCPResult, Paper, ParentChunk, RunConfig
from research_agent.core.utils import content_hash, simple_tokenize, stable_hash, utc_now_iso


class EvidenceRAGService:
    def __init__(self, artifact_store: ArtifactStore, config: RunConfig,
                 pdf_manager: Any = None, parser: Any = None,
                 embedder: Any = None, vector_store: Any = None,
                 repository: Any = None) -> None:
        self.artifacts = artifact_store
        self.config = config
        self.pdf_manager = pdf_manager
        self.parser = parser
        self.embedder = embedder
        self.vector_store = vector_store
        self._repo = repository
        self.parents: Dict[str, ParentChunk] = {}
        self.children: Dict[str, ChildChunk] = {}
        self.paper_children: Dict[str, List[str]] = {}
        self.paper_metadata: Dict[str, Dict[str, object]] = {}

    def ensure_fulltext_materialized(self, paper: Paper, text: Optional[str] = None,
                                      pdf_path: Optional[str | Path] = None) -> Dict[str, int]:
        # ── Try PDF download + parse (Phase 4) ──
        parsed_doc = None
        if self.pdf_manager and not text and not pdf_path:
            asset = self.pdf_manager.download(paper)
            if asset:
                local_path = self.pdf_manager.get_pdf_path(paper.work_id)
                if local_path and self.parser:
                    parsed_doc = self.parser.parse(local_path)

        # Build text from parsed doc, explicit text, local PDF, or fallback
        if parsed_doc and parsed_doc.pages:
            raw_text = "\n\n".join(p.text for p in parsed_doc.pages)
            source = f"pdf:{parsed_doc.parser_name}"
        else:
            raw_text = text or self._load_text(pdf_path) or self._fallback_text(paper)
            source = "abstract_fallback"

        sections = self._split_sections(raw_text)
        child_counter = 0
        for section_name, section_text in sections:
            sentences = self._split_sentences(section_text)
            if not sentences:
                continue
            for parent_index, start in enumerate(range(0, len(sentences), 6)):
                window = sentences[start : start + 8]
                parent_text = " ".join(window)
                parent_id = f"P_{stable_hash({'work': paper.work_id, 'section': section_name, 'idx': parent_index}, 14)}"
                first_child = child_counter
                for local_idx, sent_idx in enumerate(range(start, min(start + 8, len(sentences)))):
                    center = sentences[sent_idx]
                    child_text = " ".join(sentences[max(0, sent_idx - 1) : min(len(sentences), sent_idx + 2)])
                    char_start = section_text.find(center)
                    char_end = char_start + len(center) if char_start >= 0 else -1
                    tokens_before = simple_tokenize(section_text[: max(0, char_start)])
                    tokens_child = simple_tokenize(child_text)
                    child = ChildChunk(
                        child_id=f"C_{stable_hash({'work': paper.work_id, 'parent': parent_id, 'idx': child_counter}, 14)}",
                        parent_id=parent_id,
                        work_id=paper.work_id,
                        page=None,
                        section=section_name,
                        child_index=child_counter,
                        char_start=max(char_start, 0),
                        char_end=max(char_end, 0),
                        token_start=len(tokens_before),
                        token_end=len(tokens_before) + len(tokens_child),
                        text=child_text,
                        content_hash=content_hash(child_text),
                        embedding=self._embed(child_text),
                    )
                    self.children[child.child_id] = child
                    self.paper_children.setdefault(paper.work_id, []).append(child.child_id)
                    child_counter += 1
                    if len(self.children) >= self.config.max_chunks:
                        break
                parent = ParentChunk(
                    parent_id=parent_id,
                    work_id=paper.work_id,
                    page=None,
                    section=section_name,
                    start_child_index=first_child,
                    end_child_index=child_counter - 1,
                    text=parent_text,
                    content_hash=content_hash(parent_text),
                )
                self.parents[parent_id] = parent
                if len(self.children) >= self.config.max_chunks:
                    break
        self.paper_metadata[paper.work_id] = asdict(paper)
        parent_rows = [asdict(p) for p in self.parents.values() if p.work_id == paper.work_id]
        child_rows = [asdict(self.children[cid]) for cid in self.paper_children.get(paper.work_id, [])]
        self.artifacts.write_jsonl("evidence", f"{paper.work_id}_parents.jsonl", parent_rows, "parent_chunks")
        self.artifacts.write_jsonl("evidence", f"{paper.work_id}_children.jsonl", child_rows, "child_chunks")
        return {"parents": len(parent_rows), "children": len(child_rows)}

    def get_materialization_status(self, work_id: str) -> Dict[str, object]:
        return {
            "work_id": work_id,
            "materialized": work_id in self.paper_children,
            "children": len(self.paper_children.get(work_id, [])),
        }

    def search_paper_evidence(self, query: str, work_ids: Optional[Sequence[str]] = None, top_k: int = 8) -> List[EvidenceRecord]:
        query_vec = self._embed(query)
        candidates = [
            child
            for child in self.children.values()
            if work_ids is None or child.work_id in set(work_ids)
        ]
        scored = sorted(((self._cosine(query_vec, child.embedding), child) for child in candidates), key=lambda item: item[0], reverse=True)
        records: List[EvidenceRecord] = []
        for score, child in scored[:top_k]:
            parent = self.parents[child.parent_id]
            records.append(
                EvidenceRecord(
                    evidence_id=f"E_{stable_hash({'query': query, 'child': child.child_id}, 14)}",
                    work_id=child.work_id,
                    child_id=child.child_id,
                    parent_id=child.parent_id,
                    query=query,
                    child_text=child.text,
                    parent_text=parent.text,
                    page=child.page,
                    section=child.section,
                    retrieval_score=round(score, 6),
                    support_status="uncertain",
                )
            )
        return records

    def get_parent_context(self, parent_id: str) -> Optional[ParentChunk]:
        return self.parents.get(parent_id)

    def build_evidence_bundle(self, question: str, work_ids: Optional[Sequence[str]] = None, top_k: int = 8) -> EvidenceBundle:
        records = self.search_paper_evidence(question, work_ids=work_ids, top_k=top_k)
        bundle = EvidenceBundle(
            evidence_bundle_id=f"EB_{stable_hash({'question': question, 'records': [r.evidence_id for r in records]}, 14)}",
            question=question,
            records=records,
            paper_metadata={work_id: self.paper_metadata.get(work_id, {}) for work_id in {r.work_id for r in records}},
            warnings=[] if records else ["No matching child chunks were available; summary should fall back to abstract-level evidence."],
        )
        self.artifacts.write_json("evidence", f"{bundle.evidence_bundle_id}.json", bundle, "evidence_bundle", {"records": len(records)})
        return bundle

    def verify_claim_support(self, claim: str, evidence_bundle: EvidenceBundle, min_score: float = 0.12) -> EvidenceBundle:
        claim_terms = set(simple_tokenize(claim))
        for record in evidence_bundle.records:
            evidence_terms = set(simple_tokenize(record.child_text))
            overlap = len(claim_terms & evidence_terms) / max(1, len(claim_terms))
            if record.retrieval_score >= min_score and overlap > 0.05:
                record.support_status = "supports"
            else:
                record.support_status = "uncertain"
        self.artifacts.write_json(
            "evidence",
            f"{evidence_bundle.evidence_bundle_id}.json",
            evidence_bundle,
            "evidence_bundle",
            {"records": len(evidence_bundle.records), "verified": True},
        )
        return evidence_bundle

    def result(self, run_id: str, task_id: str, tool_call_id: str, result_type: str, raw_result: object) -> MCPResult:
        bundle = raw_result if isinstance(raw_result, EvidenceBundle) else None
        scope = {"evidence_bundle_id": bundle.evidence_bundle_id} if bundle else {}
        records = bundle.records if bundle else []
        paper_metadata = bundle.paper_metadata if bundle else {}
        warnings = bundle.warnings if bundle else []
        return MCPResult(
            tool_call_id=tool_call_id,
            analysis_run_id=run_id,
            task_id=task_id,
            provider="evidence-rag",
            status="completed",
            result_type=result_type,
            scope=scope,
            method={"name": result_type, "version": "1.0"},
            summary={"records": len(records), "papers": len(paper_metadata)},
            preview=[asdict(record) for record in records[:5]],
            provenance={"created_at": utc_now_iso(), "software_version": "research-agent-mvp-0.1"},
            warnings=warnings,
        )

    def _load_text(self, path: Optional[str | Path]) -> str:
        if not path:
            return ""
        path = Path(path)
        if not path.exists():
            return ""
        if path.suffix.lower() in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")
        try:
            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            return ""

    @staticmethod
    def _fallback_text(paper: Paper) -> str:
        return (
            f"# Abstract\n{paper.title}\n{paper.abstract}\n\n"
            "# Evidence Notes\nFull text was not available. This materialization uses title and abstract as fallback evidence."
        )

    @staticmethod
    def _split_sections(text: str) -> List[Tuple[str, str]]:
        chunks: List[Tuple[str, str]] = []
        current_name = "body"
        current_lines: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if re.match(r"^#{1,3}\s+\S+", stripped):
                if current_lines:
                    chunks.append((current_name, "\n".join(current_lines)))
                current_name = stripped.lstrip("#").strip()[:80]
                current_lines = []
            else:
                current_lines.append(stripped)
        if current_lines:
            chunks.append((current_name, "\n".join(current_lines)))
        return [(name, body) for name, body in chunks if body.strip()]

    @staticmethod
    def _split_sentences(text: str) -> List[str]:
        parts = re.split(r"(?<=[。！？.!?])\s+|[\n\r]+", text)
        return [part.strip() for part in parts if len(part.strip()) >= 20]

    @staticmethod
    def _embed(text: str, dim: int = 64) -> List[float]:
        vec = [0.0] * dim
        for token in simple_tokenize(text):
            idx = int(content_hash(token, 8), 16) % dim
            vec[idx] += 1.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

