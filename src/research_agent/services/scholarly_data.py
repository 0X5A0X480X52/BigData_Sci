"""Scholarly corpus construction with OpenAlex-compatible clients."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Protocol

from research_agent.core.artifact_store import ArtifactStore
from research_agent.data.cleaners import BatchCleaner
from research_agent.persistence.mysql_inserter import MySQLInserter
from research_agent.core.models import Corpus, MCPResult, Paper, RunConfig
from research_agent.core.utils import abstract_from_inverted_index, normalize_openalex_id, stable_hash, utc_now_iso


class ScholarlyClient(Protocol):
    def search_works(self, query: str, max_results: int) -> Iterable[Dict[str, Any]]:
        ...

    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        ...

    def get_citing_works(self, work_id: str, max_results: int) -> Iterable[Dict[str, Any]]:
        ...


class FixtureOpenAlexClient:
    """Deterministic offline client so the MVP works without network access."""

    def __init__(self) -> None:
        self._works = self._build_fixture()

    def _build_fixture(self) -> Dict[str, Dict[str, Any]]:
        topics = ["graph learning", "retrieval augmented generation", "scientific discovery"]
        works: Dict[str, Dict[str, Any]] = {}
        for idx in range(1, 31):
            work_id = f"W{1000 + idx}"
            topic = topics[idx % len(topics)]
            refs = [f"W{1000 + j}" for j in range(max(1, idx - 3), idx)]
            works[work_id] = {
                "id": f"https://openalex.org/{work_id}",
                "title": f"{topic.title()} Study {idx}",
                "abstract": (
                    f"This paper investigates {topic} for unfamiliar research fields. "
                    "It discusses corpus construction, evidence retrieval, benchmarks, and limitations."
                ),
                "publication_year": 2015 + idx % 10,
                "cited_by_count": 10 * idx,
                "authorships": [{"author": {"display_name": f"Author {idx % 7}"}}],
                "topics": [{"display_name": topic, "score": 0.9}],
                "concepts": [{"display_name": topic, "score": 0.8}],
                "referenced_works": refs,
                "doi": f"https://doi.org/10.0000/fixture.{idx}",
                "open_access": {"oa_url": None},
            }
        return works

    def search_works(self, query: str, max_results: int) -> Iterable[Dict[str, Any]]:
        query_terms = {t for t in query.lower().split() if t}
        scored = []
        for work in self._works.values():
            haystack = f"{work['title']} {work['abstract']}".lower()
            score = sum(1 for term in query_terms if term in haystack)
            scored.append((score, work["cited_by_count"], work))
        for _, _, work in sorted(scored, key=lambda item: (-item[0], -item[1]))[:max_results]:
            yield dict(work)

    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        return self._works.get(normalize_openalex_id(work_id))

    def get_citing_works(self, work_id: str, max_results: int) -> Iterable[Dict[str, Any]]:
        normalized = normalize_openalex_id(work_id)
        found = [work for work in self._works.values() if normalized in [normalize_openalex_id(x) for x in work.get("referenced_works", [])]]
        yield from found[:max_results]


class HttpOpenAlexClient:
    """Tiny OpenAlex client using stdlib urllib; optional and network-dependent."""

    base_url = "https://api.openalex.org"

    def __init__(self, email: str = "", cache_dir: str | Path = ".cache/openalex") -> None:
        self.email = email
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _request(self, path: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self.email:
            params["mailto"] = self.email
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(params)}"
        cache_file = self.cache_dir / f"{stable_hash(url, 24)}.json"
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding="utf-8"))
        with urllib.request.urlopen(url, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def search_works(self, query: str, max_results: int) -> Iterable[Dict[str, Any]]:
        remaining = max_results
        cursor = "*"
        while remaining > 0:
            page_size = min(100, remaining)
            data = self._request("/works", {"search": query, "per-page": page_size, "cursor": cursor})
            results = data.get("results", [])
            if not results:
                return
            for item in results:
                yield item
                remaining -= 1
                if remaining <= 0:
                    return
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                return

    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        work_id = normalize_openalex_id(work_id)
        try:
            return self._request(f"/works/{work_id}", {})
        except Exception:
            return None

    def get_citing_works(self, work_id: str, max_results: int) -> Iterable[Dict[str, Any]]:
        work_id = normalize_openalex_id(work_id)
        remaining = max_results
        cursor = "*"
        while remaining > 0:
            data = self._request("/works", {"filter": f"cites:{work_id}", "per-page": min(100, remaining), "cursor": cursor})
            for item in data.get("results", []):
                yield item
                remaining -= 1
                if remaining <= 0:
                    return
            cursor = data.get("meta", {}).get("next_cursor")
            if not cursor:
                return


def paper_from_openalex(raw: Dict[str, Any], source: str = "openalex") -> Paper:
    work_id = normalize_openalex_id(str(raw.get("id", "")))
    abstract = raw.get("abstract") or abstract_from_inverted_index(raw.get("abstract_inverted_index"))
    topics = [x.get("display_name", "") for x in raw.get("topics", []) if x.get("display_name")]
    if not topics:
        topics = [x.get("display_name", "") for x in raw.get("concepts", [])[:5] if x.get("display_name")]
    authors = [
        item.get("author", {}).get("display_name", "")
        for item in raw.get("authorships", [])
        if item.get("author", {}).get("display_name")
    ]
    oa = raw.get("open_access") or {}

    return Paper(
        work_id=work_id,
        title=raw.get("title") or "(untitled)",
        abstract=abstract or "",
        publication_year=raw.get("publication_year"),
        cited_by_count=int(raw.get("cited_by_count") or 0),
        authors=authors,
        topics=topics,
        referenced_works=[normalize_openalex_id(x) for x in raw.get("referenced_works", [])],
        doi=raw.get("doi"),
        open_access_pdf_url=oa.get("oa_url") or raw.get("pdf_url"),
        source=source,
        raw=raw,
    )


class ScholarlyDataService:
    def __init__(self, artifact_store: ArtifactStore, config: RunConfig,
                 client: Optional[ScholarlyClient] = None,
                 repository: Any = None,
                 openalex_source: Any = None) -> None:
        self.artifacts = artifact_store
        self.config = config
        self.client = client or FixtureOpenAlexClient()
        self._repo = repository          # MySQLResearchRepository (optional)
        self._openalex = openalex_source  # OpenAlexSource (optional, Phase 2)
        self.corpora: Dict[str, Corpus] = {}
        self.analysis_run_id: str = ""

    # ── Provider selection ────────────────────────────────────

    @property
    def provider_name(self) -> str:
        """Human-readable name of the active data provider."""
        if self._openalex is not None:
            return "openalex"
        return self.client.__class__.__name__

    def _fetch_works(self, query: str, max_results: int) -> Iterator[Dict[str, Any]]:
        """Fetch works from the active provider (OpenAlexSource or ScholarlyClient)."""
        if self._openalex is not None:
            yield from self._openalex.search_works(query, max_results)
        else:
            yield from self.client.search_works(query, max_results)

    def _fetch_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        if self._openalex is not None:
            return self._openalex.get_work(work_id)
        return self.client.get_work(work_id)

    def _fetch_citing_works(self, work_id: str, max_results: int) -> Iterator[Dict[str, Any]]:
        if self._openalex is not None:
            yield from self._openalex.get_citing_works(work_id, max_results)
        else:
            yield from self.client.get_citing_works(work_id, max_results)

    # ── Corpus building ──────────────────────────────────────

    def create_field_corpus(self, query: str, max_results: Optional[int] = None,
                            alternate_queries: Optional[List[str]] = None) -> Corpus:
        limit = min(max_results or self.config.max_field_corpus, self.config.max_field_corpus)
        query_hash = stable_hash(f"{query}|{limit}", 32)

        # ── Idempotency check via MySQL ──
        if self._repo:
            existing_id = self._repo.find_corpus_by_hash(query_hash)
            if existing_id:
                cached_papers = self._load_corpus_from_repo(existing_id, query)
                if cached_papers and len(cached_papers) >= limit * 0.8:
                    corpus = Corpus(
                        corpus_id=existing_id, query=query, papers=cached_papers,
                        member_sources={p.work_id: ["field_query"] for p in cached_papers},
                    )
                    self.corpora[existing_id] = corpus
                    return corpus
                else:
                    print(f"Using cached papers for query: {query}")

        provider_used = self.provider_name
        fetch_warnings: List[str] = []
        attempted_queries = [query] + [q for q in (alternate_queries or []) if q and q != query]
        papers: List[Paper] = []
        raw_works: List[Dict[str, Any]] = []
        used_query = query
        for candidate_query in attempted_queries:
            try:
                raw_works = list(self._fetch_works(candidate_query, limit))
                papers = self._dedupe(
                    paper_from_openalex(raw, source=provider_used)
                    for raw in raw_works
                )
                used_query = candidate_query
                if papers:
                    if candidate_query != query:
                        fetch_warnings.append(f"Primary query returned no papers; used alternate query: {candidate_query}")
                    break
                if candidate_query != attempted_queries[-1]:
                    fetch_warnings.append(f"Query returned no papers, trying alternate: {candidate_query}")
            except Exception as exc:
                fetch_warnings.append(f"{provider_used} failed for query '{candidate_query}': {exc}")
                papers = []
        if not papers and self._openalex is not None:
            fixture = FixtureOpenAlexClient()
            provider_used = "FixtureOpenAlexClient"
            used_query = query
            fetch_warnings.insert(0, "openalex failed; fell back to fixture data")
            raw_works = list(fixture.search_works(query, limit))
            papers = self._dedupe(
                paper_from_openalex(raw, source=provider_used)
                for raw in raw_works
            )
        corpus_id = f"field_{query_hash[:12]}"
        warnings = list(fetch_warnings)
        if not papers:
            warnings.append("No papers returned; provider may be offline.")
        corpus = Corpus(
            corpus_id=corpus_id,
            query=used_query,
            papers=papers,
            member_sources={paper.work_id: ["field_query"] for paper in papers},
            warnings=warnings,
        )
        self.corpora[corpus_id] = corpus
        self.artifacts.write_json("corpora", f"{corpus_id}.json", corpus, "field_corpus", {"papers": len(papers)})

        self._persist_corpus_to_repo(corpus, raw_works, "field_query")

        return corpus

    def create_seed_lineage_corpus(self, seed_work_id: str, max_depth: Optional[int] = None,
                                    max_results: Optional[int] = None) -> Corpus:
        depth_limit = min(max_depth or self.config.max_bfs_depth, self.config.max_bfs_depth)
        paper_limit = min(max_results or self.config.max_seed_lineage, self.config.max_seed_lineage)
        seed_work_id = normalize_openalex_id(seed_work_id)
        corpus_id = f"lineage_{stable_hash({'seed': seed_work_id, 'depth': depth_limit}, 12)}"

        # ── Resume from crawl_frontier if available ──
        seen: Dict[str, Paper] = {}
        raw_seen: Dict[str, Dict[str, Any]] = {}
        member_sources: Dict[str, List[str]] = {}
        frontier: List[tuple] = []

        if self._repo:
            # Load any existing frontier entries
            pending = self._repo.get_pending_frontier(corpus_id, limit=paper_limit)
            for entry in pending:
                frontier.append((entry["work_id"], entry["depth"], entry["source"]))
            if not frontier:
                # First run: seed the frontier
                self._repo.upsert_frontier(seed_work_id, corpus_id, 0, "seed")
                frontier = [(seed_work_id, 0, "seed")]

        if not frontier:
            frontier = [(seed_work_id, 0, "seed")]

        while frontier and len(seen) < paper_limit:
            work_id, depth, source = frontier.pop(0)
            if work_id in seen or depth > depth_limit:
                if self._repo:
                    self._repo.update_frontier_status(work_id, corpus_id, "completed")
                continue

            raw = self._fetch_work(work_id)
            if not raw:
                if self._repo:
                    self._repo.update_frontier_status(work_id, corpus_id, "failed", "Not found in OpenAlex")
                continue

            paper = paper_from_openalex(raw, source=self.provider_name)
            seen[paper.work_id] = paper
            raw_seen[paper.work_id] = raw
            member_sources.setdefault(paper.work_id, []).append(source)

            if self._repo:
                self._repo.update_frontier_status(work_id, corpus_id, "completed")

            if depth < depth_limit:
                for ref in paper.referenced_works[:50]:
                    if self._repo:
                        self._repo.upsert_frontier(ref, corpus_id, depth + 1, f"ref_d{depth+1}")
                    frontier.append((ref, depth + 1, f"reference_depth_{depth + 1}"))
                for citing in self._fetch_citing_works(paper.work_id, 25):
                    citing_id = normalize_openalex_id(str(citing.get("id", "")))
                    if self._repo:
                        self._repo.upsert_frontier(citing_id, corpus_id, depth + 1, f"cite_d{depth+1}")
                    frontier.append((citing_id, depth + 1, f"citing_depth_{depth + 1}"))

        corpus = Corpus(corpus_id=corpus_id, query=seed_work_id, papers=list(seen.values()),
                        member_sources=member_sources)
        self.corpora[corpus_id] = corpus
        self.artifacts.write_json("corpora", f"{corpus_id}.json", corpus, "seed_lineage_corpus",
                                  {"papers": len(corpus.papers)})

        self._persist_corpus_to_repo(corpus, list(raw_seen.values()), "seed_lineage")

        return corpus

    def _persist_corpus_to_repo(self, corpus: Corpus, raw_works: List[Dict[str, Any]], membership_source: str) -> None:
        if not self._repo:
            return
        try:
            self._repo.create_corpus(corpus, run_id=self.analysis_run_id)
            if raw_works:
                batch = BatchCleaner().process_batch(raw_works)
                MySQLInserter(self._repo).insert_batch(batch, corpus_id=corpus.corpus_id, run_id=self.analysis_run_id, membership_source=membership_source)
            else:
                for paper in corpus.papers:
                    self._repo.upsert_corpus_membership(corpus.corpus_id, paper.work_id, membership_source)
        except Exception:
            # Persistence is best-effort; artifacts remain the source of truth when DB writes fail.
            pass
    def _load_corpus_from_repo(self, corpus_id: str, query: str) -> List[Paper]:
        """Reconstruct corpus papers from MySQL corpus_membership."""
        if not self._repo:
            return []
        work_ids = self._repo.get_corpus_members(corpus_id)
        papers = []
        for wid in work_ids[:self.config.max_field_corpus]:
            raw = self._fetch_work(wid)
            if raw:
                papers.append(paper_from_openalex(raw, source=self.provider_name))
        return papers

    def expand_references(self, work_id: str, max_results: int = 50) -> List[Paper]:
        raw = self.client.get_work(work_id)
        if not raw:
            return []
        refs = raw.get("referenced_works", [])[:max_results]
        return [paper_from_openalex(item, self.client.__class__.__name__) for item in filter(None, (self.client.get_work(ref) for ref in refs))]

    def expand_citing_works(self, work_id: str, max_results: int = 50) -> List[Paper]:
        return [paper_from_openalex(raw, self.client.__class__.__name__) for raw in self.client.get_citing_works(work_id, max_results)]

    def get_corpus_summary(self, corpus_id: str) -> Dict[str, Any]:
        corpus = self.corpora[corpus_id]
        years: Dict[int, int] = {}
        for paper in corpus.papers:
            if paper.publication_year:
                years[paper.publication_year] = years.get(paper.publication_year, 0) + 1
        return {"corpus_id": corpus_id, "papers": len(corpus.papers), "years": dict(sorted(years.items()))}

    def get_work(self, work_id: str) -> Optional[Paper]:
        raw = self.client.get_work(work_id)
        return paper_from_openalex(raw, self.client.__class__.__name__) if raw else None

    def list_candidate_papers(self, corpus_id: str, limit: int = 20) -> List[Paper]:
        return sorted(self.corpora[corpus_id].papers, key=lambda p: (p.cited_by_count, p.publication_year or 0), reverse=True)[:limit]

    def result(self, run_id: str, task_id: str, tool_call_id: str, result_type: str, corpus: Corpus) -> MCPResult:
        return MCPResult(
            tool_call_id=tool_call_id,
            analysis_run_id=run_id,
            task_id=task_id,
            provider="scholarly-data",
            status="completed",
            result_type=result_type,
            scope={"corpus_id": corpus.corpus_id, "data_cutoff": corpus.data_cutoff},
            method={"name": result_type, "version": "1.0", "parameters": {"query": corpus.query}},
            summary={"papers": len(corpus.papers)},
            preview=[asdict(paper) for paper in corpus.papers[:5]],
            provenance={"created_at": utc_now_iso(), "software_version": "research-agent-mvp-0.1"},
            warnings=corpus.warnings,
        )

    @staticmethod
    def _dedupe(papers: Iterable[Paper]) -> List[Paper]:
        seen: Dict[str, Paper] = {}
        for paper in papers:
            if paper.work_id and paper.work_id not in seen:
                seen[paper.work_id] = paper
        return list(seen.values())





