#!/usr/bin/env python
"""One-shot OpenAlex ELT CLI.

Pipeline:
query -> seed selection -> BFS expansion (references + citations) -> cleaning
-> MySQL normalization -> Neo4j sync

This script intentionally bypasses the research-agent planner/executor stack and
acts as a direct ETL entrypoint, similar in spirit to the demo under
``demos/openalex_python_elt_demo/module_ELT/main.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover - optional dependency
    tqdm = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.config import MySQLConfig, Neo4jConfig, OpenAlexConfig
from research_agent.core.models import Corpus, ResearchRun, RunConfig
from research_agent.core.utils import normalize_openalex_id, stable_hash, utc_now_iso
from research_agent.data.cleaners import BatchCleaner, BatchCleanedResult
from research_agent.data.openalex_api_bulk_source import DEFAULT_BULK_SELECT, OpenAlexApiBulkSource, iter_jsonl
from research_agent.data.openalex_cli_planner import OpenAlexCliPlanBuilder, load_plan_filter
from research_agent.data.openalex_cli_source import OpenAlexCliSource
from research_agent.data.openalex_source import DEFAULT_SCOUT_SELECT, OpenAlexSource
from research_agent.persistence.mysql_inserter import BatchInsertStats, MySQLInserter
from research_agent.persistence.mysql_repository import MySQLResearchRepository
from research_agent.persistence.neo4j_sync import Neo4jGraphSync
from research_agent.services.scholarly_data import FixtureOpenAlexClient


logger = logging.getLogger("openalex_elt_cli")
UNLIMITED_FANOUT = 10**9


@dataclass
class CrawlStats:
    seed_query: str
    seed_work_id: str = ""
    seed_title: str = ""
    seed_results_scanned: int = 0
    fetched_works: int = 0
    missing_works: int = 0
    processed_works: int = 0
    reference_edges: int = 0
    citing_edges: int = 0
    enqueued: int = 0
    max_depth_reached: int = 0
    request_count: int = 0
    cache_hits: int = 0
    failures: int = 0
    rate_limited: int = 0
    elapsed_seconds: float = 0.0
    works_per_second: float = 0.0
    warnings: List[str] = field(default_factory=list)


@dataclass
class ELTArtifacts:
    run_id: str
    corpus_id: str
    query: str
    seed_work_id: str
    seed_title: str
    raw_work_count: int
    cleaned_work_count: int
    cleaned_entity_counts: Dict[str, int]
    insertion_stats: Dict[str, Any]
    neo4j_stats: Dict[str, Any]
    crawl_stats: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenAlex one-shot ETL: query -> BFS -> clean -> MySQL -> Neo4j",
    )
    parser.add_argument("query", nargs="?", default="bert", help="OpenAlex search query, e.g. bert")
    parser.add_argument("--provider", choices=["openalex", "fixture", "openalex_cli", "openalex_api_bulk"], default="openalex",
                        help="Data provider. Use fixture for offline smoke tests, openalex for BFS scout, or a bulk metadata provider.")
    parser.add_argument("--seed-search-limit", type=int, default=1,
                        help="How many search results to inspect when auto-selecting the seed.")
    parser.add_argument("--max-depth", type=int, default=1, help="BFS scout depth limit. Default: 1")
    parser.add_argument("--max-reference-fanout", type=int, default=50,
                        help="Per-node reference fanout cap. Default: 50.")
    parser.add_argument("--max-citing-fanout", type=int, default=50,
                        help="Per-node citing-work fanout cap. Default: 50.")
    parser.add_argument("--target-works", type=int, default=5000,
                        help="Hard cap for BFS scout works. 0 means no cap.")
    parser.add_argument("--bfs-batch-size", type=int, default=50,
                        help="How many frontier works to expand per batch in BFS scout. Default: 50.")
    parser.add_argument("--max-frontier-per-depth", type=int, default=1000,
                        help="Maximum next-layer frontier size after ranking/pruning. 0 means no cap.")
    parser.add_argument("--checkpoint-dir", default="",
                        help="Directory for Batch BFS layer artifacts and checkpoint.")
    parser.add_argument("--resume", action="store_true",
                        help="Resume Batch BFS from --checkpoint-dir/checkpoint.json when available.")
    parser.add_argument("--openalex-select-fields", default=DEFAULT_SCOUT_SELECT,
                        help="Comma-separated OpenAlex fields for BFS scout API calls.")
    parser.add_argument("--artifact-root", default="artifacts/openalex_elt",
                        help="Directory for run artifacts and summaries.")
    parser.add_argument("--mysql-host", default="localhost")
    parser.add_argument("--mysql-port", type=int, default=3306)
    parser.add_argument("--mysql-user", default="research_agent")
    parser.add_argument("--mysql-password", default="")
    parser.add_argument("--mysql-database", default="research_agent")
    parser.add_argument("--init-schema", action="store_true", help="Create MySQL tables before writing.")
    parser.add_argument("--sync-neo4j", action="store_true", help="Synchronize MySQL normalized data to Neo4j.")
    parser.add_argument("--neo4j-uri", default="bolt://localhost:7687")
    parser.add_argument("--neo4j-user", default="neo4j")
    parser.add_argument("--neo4j-password", default="")
    parser.add_argument("--neo4j-database", default="neo4j")
    parser.add_argument("--openalex-email", default="", help="OpenAlex polite-pool email.")
    parser.add_argument("--openalex-cache-dir", default=".cache/openalex")
    parser.add_argument("--openalex-api-key", default="", help="OpenAlex API key for official CLI mode.")
    parser.add_argument("--openalex-cli-filter", default="", help="OpenAlex filter string for official CLI mode.")
    parser.add_argument("--openalex-cli-plan", default="", help="Path to a download_plan.json generated by BFS scout mode.")
    parser.add_argument("--openalex-cli-output", default="data/openalex_cli_downloads",
                        help="Output directory used by `openalex download`.")
    parser.add_argument("--openalex-cli-workers", type=int, default=50,
                        help="Concurrent workers for official OpenAlex CLI.")
    parser.add_argument("--openalex-cli-fresh", action="store_true",
                        help="Ignore existing OpenAlex CLI checkpoint and start fresh.")
    parser.add_argument("--openalex-cli-skip-download", action="store_true",
                        help="Skip `openalex download` and only ingest existing JSON files.")
    parser.add_argument("--openalex-cli-ingest-limit", type=int, default=0,
                        help="Max downloaded JSON works to ingest. 0 means no limit.")
    parser.add_argument("--write-openalex-cli-plan", default="",
                        help="In openalex BFS mode, write a recommended OpenAlex CLI download plan.")
    parser.add_argument("--openalex-api-bulk-filter", default="",
                        help="OpenAlex API filter string for provider=openalex_api_bulk.")
    parser.add_argument("--openalex-api-bulk-plan", default="",
                        help="Path to openalex_download_plan.json with recommended_filter for API bulk mode.")
    parser.add_argument("--openalex-api-bulk-output", default="data/openalex_api_bulk/metadata.jsonl",
                        help="JSONL output path for provider=openalex_api_bulk.")
    parser.add_argument("--openalex-api-bulk-target", type=int, default=0,
                        help="Max OpenAlex API bulk records to download. 0 means no cap.")
    parser.add_argument("--openalex-api-bulk-per-page", type=int, default=100,
                        help="OpenAlex API bulk per_page, capped at 100.")
    parser.add_argument("--openalex-api-bulk-sleep", type=float, default=0.0,
                        help="Seconds to sleep between OpenAlex API bulk cursor pages.")
    parser.add_argument("--openalex-api-bulk-select-fields", default=DEFAULT_BULK_SELECT,
                        help="Comma-separated OpenAlex fields for API bulk downloads.")
    parser.add_argument("--openalex-api-bulk-ingest-limit", type=int, default=0,
                        help="Max downloaded JSONL works to ingest. 0 means no limit.")
    parser.add_argument("--openalex-api-bulk-download-only", action="store_true",
                        help="Download API bulk JSONL and exit before MySQL/Neo4j ingestion.")
    parser.add_argument("--openalex-api-bulk-no-resume", action="store_true",
                        help="Do not resume API bulk download from checkpoint.")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--debug", action="store_true", help="Enable extra debug logging.")
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bars.")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def maybe_tqdm(iterable: Any, *, enabled: bool, **kwargs: Any) -> Any:
    if enabled and tqdm is not None:
        return tqdm(iterable, **kwargs)
    return iterable


def maybe_tqdm_write(message: str) -> None:
    if tqdm is not None:
        tqdm.write(message)
    else:
        print(message)


def build_source(provider: str, email: str, cache_dir: str) -> Any:
    if provider == "fixture":
        return FixtureOpenAlexClient()
    return OpenAlexSource(OpenAlexConfig(email=email, cache_dir=cache_dir))


def select_seed_work(source: Any, query: str, seed_search_limit: int, select_fields: str = DEFAULT_SCOUT_SELECT) -> Tuple[str, Dict[str, Any], int]:
    limit = max(1, seed_search_limit)
    logger.debug("Selecting seed from top %s OpenAlex search results for query=%r", limit, query)
    seed_candidates = _retry(
        lambda: list(_call_provider(source.search_works, query, max_results=limit, select_fields=select_fields)),
        attempts=3,
        delay=1.0,
        label=f"seed search for {query!r}",
    )
    if not seed_candidates:
        raise RuntimeError(f"No OpenAlex works found for query: {query!r}")
    logger.debug("Seed search returned %s candidate(s)", len(seed_candidates))

    seed_raw = dict(seed_candidates[0])
    seed_id = normalize_openalex_id(str(seed_raw.get("id", "")))
    if not seed_id:
        raise RuntimeError(f"Seed search returned a work without a valid OpenAlex ID: {seed_raw!r}")
    logger.debug("Selected seed work_id=%s title=%r", seed_id, seed_raw.get("title", ""))
    return seed_id, seed_raw, len(seed_candidates)


def crawl_bfs(
    source: Any,
    query: str,
    seed_work_id: str,
    seed_raw: Dict[str, Any],
    max_depth: int = 1,
    max_reference_fanout: int = 50,
    max_citing_fanout: int = 50,
    show_progress: bool = True,
    target_works: int = 5000,
    select_fields: str = DEFAULT_SCOUT_SELECT,
    bfs_batch_size: int = 50,
    max_frontier_per_depth: int = 1000,
    checkpoint_dir: str = "",
    resume: bool = False,
) -> Tuple[List[Dict[str, Any]], CrawlStats]:
    """Layered Batch BFS through references and citing works.

    The scout expands one depth layer at a time so each layer can be deduped,
    pruned, checkpointed, and summarized before the next API wave starts.
    """

    seed_work_id = normalize_openalex_id(seed_work_id)
    started = time.perf_counter()
    max_depth = max(0, int(max_depth or 0))
    target_works = max(0, int(target_works or 0))
    bfs_batch_size = max(1, int(bfs_batch_size or 1))
    max_frontier_per_depth = max(0, int(max_frontier_per_depth or 0))
    artifact_dir = Path(checkpoint_dir) if checkpoint_dir else None

    stats = CrawlStats(
        seed_query=query,
        seed_work_id=seed_work_id,
        seed_title=str(seed_raw.get("title", "")),
        seed_results_scanned=1,
    )

    raw_seed = dict(seed_raw)
    raw_seed["id"] = raw_seed.get("id") or f"https://openalex.org/{seed_work_id}"
    raw_by_id: Dict[str, Dict[str, Any]] = {seed_work_id: raw_seed}
    discovered_depth: Dict[str, int] = {seed_work_id: 0}
    visited: set[str] = set()
    processed_order: List[str] = []
    frontier: List[str] = [seed_work_id]
    start_depth = 0
    edges: List[Dict[str, Any]] = []

    if resume and artifact_dir:
        state = _load_bfs_checkpoint(artifact_dir)
        if state:
            raw_by_id = {str(k): dict(v) for k, v in (state.get("raw_by_id") or {}).items() if isinstance(v, dict)}
            discovered_depth = {str(k): int(v) for k, v in (state.get("discovered_depth") or {}).items()}
            visited = {str(v) for v in state.get("visited") or []}
            processed_order = [str(v) for v in state.get("processed_order") or []]
            frontier = [str(v) for v in state.get("frontier") or []]
            edges = [dict(v) for v in state.get("edges") or [] if isinstance(v, dict)]
            start_depth = int(state.get("next_depth") or 0)
            stored_stats = state.get("stats") or {}
            if isinstance(stored_stats, dict):
                for key, value in stored_stats.items():
                    if hasattr(stats, key) and key not in {"elapsed_seconds", "works_per_second"}:
                        setattr(stats, key, value)
            raw_by_id.setdefault(seed_work_id, raw_seed)
            discovered_depth.setdefault(seed_work_id, 0)

    progress = None
    if show_progress and tqdm is not None:
        total = target_works if target_works else None
        progress = tqdm(total=total, initial=len(processed_order), desc="Batch BFS scout", unit="work", dynamic_ncols=True)

    try:
        for depth in range(start_depth, max_depth + 1):
            if target_works and len(processed_order) >= target_works:
                break
            if not frontier:
                break

            frontier = _unique_ids(frontier)
            frontier = [wid for wid in frontier if wid and wid not in visited]
            if target_works:
                frontier = frontier[:max(0, target_works - len(processed_order))]
            if not frontier:
                break

            stats.max_depth_reached = max(stats.max_depth_reached, depth)
            _fetch_missing_works(source, frontier, raw_by_id, stats, select_fields)

            layer_ids: List[str] = []
            for work_id in frontier:
                if target_works and len(processed_order) >= target_works:
                    break
                raw = raw_by_id.get(work_id)
                if not raw:
                    stats.missing_works += 1
                    visited.add(work_id)
                    continue
                raw_id = normalize_openalex_id(str(raw.get("id", work_id))) or work_id
                if raw_id != work_id:
                    raw_by_id[raw_id] = dict(raw)
                    discovered_depth.setdefault(raw_id, depth)
                    work_id = raw_id
                if work_id in visited:
                    continue
                visited.add(work_id)
                processed_order.append(work_id)
                layer_ids.append(work_id)
                stats.fetched_works += 1
                if progress is not None:
                    progress.update(1)
                    progress.set_postfix({"depth": depth, "frontier": len(frontier), "fetched": stats.fetched_works})

            if artifact_dir:
                _write_bfs_layer_artifacts(artifact_dir, depth, layer_ids, raw_by_id, query, stats, edges, select_fields, max_depth, target_works, bfs_batch_size, max_frontier_per_depth)

            if depth >= max_depth or (target_works and len(processed_order) >= target_works):
                _save_bfs_checkpoint(artifact_dir, depth + 1, [], raw_by_id, discovered_depth, visited, processed_order, edges, stats)
                break

            next_candidates: List[str] = []
            for batch in _chunks(layer_ids, bfs_batch_size):
                candidate_budget = 0
                if target_works:
                    candidate_budget = max(0, target_works - len(processed_order) - len(next_candidates))
                    if candidate_budget <= 0:
                        break
                batch_candidates = _expand_bfs_batch(
                    source=source,
                    batch_ids=batch,
                    raw_by_id=raw_by_id,
                    visited=visited,
                    discovered_depth=discovered_depth,
                    next_depth=depth + 1,
                    max_reference_fanout=max_reference_fanout,
                    max_citing_fanout=max_citing_fanout,
                    select_fields=select_fields,
                    stats=stats,
                    edges=edges,
                    candidate_budget=candidate_budget,
                )
                next_candidates.extend(batch_candidates)

            next_frontier = _unique_ids([wid for wid in next_candidates if wid not in visited])
            if target_works:
                next_frontier = next_frontier[:max(0, target_works - len(processed_order))]
            if max_frontier_per_depth > 0:
                next_frontier = _rank_and_prune_frontier(next_frontier, raw_by_id, query, max_frontier_per_depth)
            frontier = next_frontier
            _save_bfs_checkpoint(artifact_dir, depth + 1, frontier, raw_by_id, discovered_depth, visited, processed_order, edges, stats)
    finally:
        if progress is not None:
            progress.close()

    stats.processed_works = len(processed_order)
    stats.elapsed_seconds = time.perf_counter() - started
    stats.works_per_second = stats.processed_works / stats.elapsed_seconds if stats.elapsed_seconds > 0 else 0.0
    source_stats = getattr(source, "stats", {}) or {}
    stats.request_count = int(source_stats.get("requests", 0) or 0)
    stats.cache_hits = int(source_stats.get("cache_hits", 0) or 0)
    stats.failures = int(source_stats.get("failures", 0) or 0)
    stats.rate_limited = int(source_stats.get("rate_limited", 0) or 0)

    ordered_raws = [raw_by_id[wid] for wid in processed_order if wid in raw_by_id]
    if artifact_dir:
        _write_bfs_final_artifacts(artifact_dir, ordered_raws, query, stats, edges)
    return ordered_raws, stats


def _expand_bfs_batch(
    *,
    source: Any,
    batch_ids: List[str],
    raw_by_id: Dict[str, Dict[str, Any]],
    visited: set[str],
    discovered_depth: Dict[str, int],
    next_depth: int,
    max_reference_fanout: int,
    max_citing_fanout: int,
    select_fields: str,
    stats: CrawlStats,
    edges: List[Dict[str, Any]],
    candidate_budget: int = 0,
) -> List[str]:
    candidates: List[str] = []
    reference_ids: List[str] = []
    for work_id in batch_ids:
        raw = raw_by_id.get(work_id) or {}
        refs = [normalize_openalex_id(str(ref)) for ref in (raw.get("referenced_works") or [])]
        refs = [ref_id for ref_id in refs if ref_id]
        if max_reference_fanout > 0:
            refs = refs[:max_reference_fanout]
        for ref_id in refs:
            stats.reference_edges += 1
            edges.append({"source": work_id, "target": ref_id, "type": "references"})
            if candidate_budget and len(candidates) >= candidate_budget:
                continue
            if _discover_candidate(ref_id, next_depth, visited, discovered_depth):
                reference_ids.append(ref_id)
                candidates.append(ref_id)
                stats.enqueued += 1

    reference_ids = _unique_ids(reference_ids)
    if reference_ids:
        _fetch_missing_works(source, reference_ids, raw_by_id, stats, select_fields)

    citing_limit = max_citing_fanout if max_citing_fanout > 0 else UNLIMITED_FANOUT
    for work_id in batch_ids:
        if candidate_budget and len(candidates) >= candidate_budget:
            break
        citing_raws = _retry(
            lambda: list(_call_provider(source.get_citing_works, work_id, max_results=citing_limit, select_fields=select_fields)),
            attempts=3,
            delay=1.0,
            label=f"fetch citing works for {work_id}",
        ) or []
        for citing_raw in citing_raws:
            citing_id = normalize_openalex_id(str(citing_raw.get("id", "")))
            if not citing_id:
                continue
            stats.citing_edges += 1
            raw_by_id.setdefault(citing_id, dict(citing_raw))
            edges.append({"source": citing_id, "target": work_id, "type": "cites"})
            if candidate_budget and len(candidates) >= candidate_budget:
                continue
            if _discover_candidate(citing_id, next_depth, visited, discovered_depth):
                candidates.append(citing_id)
                stats.enqueued += 1
    return candidates


def _discover_candidate(work_id: str, depth: int, visited: set[str], discovered_depth: Dict[str, int]) -> bool:
    if not work_id or work_id in visited or work_id in discovered_depth:
        return False
    discovered_depth[work_id] = depth
    return True


def _fetch_missing_works(source: Any, work_ids: Iterable[str], raw_by_id: Dict[str, Dict[str, Any]], stats: CrawlStats, select_fields: str) -> None:
    missing = _unique_ids([wid for wid in work_ids if wid and wid not in raw_by_id])
    if not missing:
        return
    get_works_batch = getattr(source, "get_works_batch", None)
    if callable(get_works_batch):
        try:
            for raw in _call_provider(get_works_batch, missing, select_fields=select_fields):
                raw_id = normalize_openalex_id(str(raw.get("id", "")))
                if raw_id:
                    raw_by_id.setdefault(raw_id, dict(raw))
        except Exception as exc:  # noqa: BLE001 - scout can fall back to singleton fetches
            stats.warnings.append(f"Batch work fetch failed: {exc}")
    for work_id in missing:
        if work_id in raw_by_id:
            continue
        raw = _retry(
            lambda: source.get_work(work_id, select_fields=select_fields),
            attempts=3,
            delay=1.0,
            label=f"fetch work {work_id}",
        )
        if raw:
            raw_id = normalize_openalex_id(str(raw.get("id", work_id))) or work_id
            raw_by_id.setdefault(raw_id, dict(raw))


def _rank_and_prune_frontier(frontier: List[str], raw_by_id: Dict[str, Dict[str, Any]], query: str, top_k: int) -> List[str]:
    if top_k <= 0 or len(frontier) <= top_k:
        return frontier
    ranked = sorted(enumerate(frontier), key=lambda item: (-_frontier_score(raw_by_id.get(item[1]) or {}, query), item[0]))
    return [work_id for _, work_id in ranked[:top_k]]


def _frontier_score(raw: Dict[str, Any], query: str) -> float:
    score = float(raw.get("cited_by_count") or 0)
    query_terms = {term.lower() for term in query.replace("-", " ").split() if term.strip()}
    title = str(raw.get("title") or "").lower()
    if query_terms and any(term in title for term in query_terms):
        score += 100.0
    for topic in raw.get("topics") or raw.get("concepts") or []:
        name = str(topic.get("display_name") or "").lower()
        if query_terms and any(term in name for term in query_terms):
            try:
                score += 50.0 * float(topic.get("score", 1.0) or 1.0)
            except (TypeError, ValueError):
                score += 50.0
    return score


def _unique_ids(values: Iterable[str]) -> List[str]:
    seen: set[str] = set()
    result: List[str] = []
    for value in values:
        work_id = normalize_openalex_id(str(value))
        if work_id and work_id not in seen:
            seen.add(work_id)
            result.append(work_id)
    return result


def _chunks(values: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(values), max(1, size)):
        yield values[index:index + max(1, size)]


def _write_bfs_layer_artifacts(
    artifact_dir: Path,
    depth: int,
    layer_ids: List[str],
    raw_by_id: Dict[str, Dict[str, Any]],
    query: str,
    stats: CrawlStats,
    edges: List[Dict[str, Any]],
    select_fields: str,
    max_depth: int,
    target_works: int,
    bfs_batch_size: int,
    max_frontier_per_depth: int,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    if depth == 0:
        _write_json(artifact_dir / "bfs_config.json", {
            "query": query,
            "select_fields": select_fields,
            "max_depth": max_depth,
            "target_works": target_works,
            "bfs_batch_size": bfs_batch_size,
            "max_frontier_per_depth": max_frontier_per_depth,
        })
    layer_works = [raw_by_id[wid] for wid in layer_ids if wid in raw_by_id]
    _write_jsonl(artifact_dir / f"layer_{depth}_works.jsonl", layer_works)
    if depth == 0:
        _write_jsonl(artifact_dir / "seed_works.jsonl", layer_works)
    _write_stats_files(artifact_dir, [raw_by_id[wid] for wid in raw_by_id if raw_by_id.get(wid)])
    _write_jsonl(artifact_dir / "edges_cites.jsonl", edges)
    _write_jsonl(artifact_dir / "frontier_scores.jsonl", [
        {"id": wid, "depth": depth, "score": _frontier_score(raw_by_id.get(wid) or {}, query)}
        for wid in layer_ids
    ])


def _write_bfs_final_artifacts(artifact_dir: Path, raw_works: List[Dict[str, Any]], query: str, stats: CrawlStats, edges: List[Dict[str, Any]]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    _write_stats_files(artifact_dir, raw_works)
    _write_jsonl(artifact_dir / "edges_cites.jsonl", edges)
    plan = OpenAlexCliPlanBuilder().write_plan(raw_works, query, artifact_dir / "openalex_download_plan.json")
    plan["bfs_depth"] = stats.max_depth_reached
    plan["bfs_work_count"] = len(raw_works)
    _write_json(artifact_dir / "openalex_download_plan.json", plan)


def _write_stats_files(artifact_dir: Path, raw_works: List[Dict[str, Any]]) -> None:
    topics: Counter[str] = Counter()
    sources: Counter[str] = Counter()
    years: Counter[str] = Counter()
    for work in raw_works:
        year = work.get("publication_year")
        if year:
            years[str(year)] += 1
        source = (work.get("primary_location") or {}).get("source") or {}
        source_id = str(source.get("id") or source.get("display_name") or "").rstrip("/").rsplit("/", 1)[-1]
        if source_id:
            sources[source_id] += 1
        for topic in work.get("topics") or work.get("concepts") or []:
            topic_id = str(topic.get("id") or topic.get("display_name") or "").rstrip("/").rsplit("/", 1)[-1]
            if topic_id:
                topics[topic_id] += 1
    _write_json(artifact_dir / "topics_stats.json", dict(topics.most_common()))
    _write_json(artifact_dir / "sources_stats.json", dict(sources.most_common()))
    _write_json(artifact_dir / "years_stats.json", dict(sorted(years.items())))


def _save_bfs_checkpoint(
    artifact_dir: Optional[Path],
    next_depth: int,
    frontier: List[str],
    raw_by_id: Dict[str, Dict[str, Any]],
    discovered_depth: Dict[str, int],
    visited: set[str],
    processed_order: List[str],
    edges: List[Dict[str, Any]],
    stats: CrawlStats,
) -> None:
    if not artifact_dir:
        return
    _write_json(artifact_dir / "checkpoint.json", {
        "next_depth": next_depth,
        "frontier": frontier,
        "raw_by_id": raw_by_id,
        "discovered_depth": discovered_depth,
        "visited": sorted(visited),
        "processed_order": processed_order,
        "edges": edges,
        "stats": asdict(stats),
    })


def _load_bfs_checkpoint(artifact_dir: Path) -> Dict[str, Any]:
    path = artifact_dir / "checkpoint.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

def _call_provider(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except TypeError as exc:
        unexpected = "unexpected keyword argument" in str(exc) or "got an unexpected keyword" in str(exc)
        if not kwargs or not unexpected:
            raise
        return fn(*args)


def clean_and_insert(
    raw_works: List[Dict[str, Any]],
    run_id: str,
    repo: MySQLResearchRepository,
    corpus_id: str,
    show_progress: bool = True,
) -> Tuple[BatchCleanedResult, BatchInsertStats]:
    cleaner = BatchCleaner()
    logger.debug("Cleaning %s raw works", len(raw_works))
    if show_progress and tqdm is not None:
        raw_works = list(tqdm(raw_works, desc="Cleaning", unit="work", dynamic_ncols=True))
    batch = cleaner.process_batch(raw_works)
    logger.debug(
        "Cleaning complete: papers=%s authors=%s institutions=%s venues=%s concepts=%s citations=%s",
        len(batch.papers),
        len(batch.authors),
        len(batch.institutions),
        len(batch.venues),
        len(batch.concepts),
        len(batch.citations),
    )
    inserter = MySQLInserter(repo)
    logger.debug("Writing cleaned batch to MySQL corpus_id=%s run_id=%s", corpus_id, run_id)
    stats = inserter.insert_batch(batch, corpus_id=corpus_id, run_id=run_id, membership_source="openalex_elt")
    logger.debug("MySQL insert stats: %s", asdict(stats))
    return batch, stats


def build_corpus(query: str, corpus_id: str, batch: BatchCleanedResult) -> Corpus:
    member_sources = {paper.work_id: ["bfs_openalex_elt"] for paper in batch.papers}
    return Corpus(corpus_id=corpus_id, query=query, papers=list(batch.papers), member_sources=member_sources)


def sync_to_neo4j(repo: MySQLResearchRepository, args: argparse.Namespace) -> Dict[str, Any]:
    logger.debug(
        "Starting Neo4j sync uri=%s database=%s user=%s",
        args.neo4j_uri,
        args.neo4j_database,
        args.neo4j_user,
    )
    syncer = Neo4jGraphSync(
        Neo4jConfig(
            uri=args.neo4j_uri,
            user=args.neo4j_user,
            password=args.neo4j_password,
            database=args.neo4j_database,
        ),
        repo,
    )
    stats = syncer.sync_all()
    logger.debug("Neo4j sync stats: %s", stats)
    return stats



def _retry(fn: Any, attempts: int, delay: float, label: str) -> Any:
    last_exc: Optional[BaseException] = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - CLI should surface provider failures cleanly
            last_exc = exc
            if attempt < attempts:
                logger.warning("%s failed on attempt %s/%s: %s", label, attempt, attempts, exc)
                time.sleep(delay * attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label} failed without an exception")


def ensure_repository(args: argparse.Namespace) -> MySQLResearchRepository:
    repo = MySQLResearchRepository(
        MySQLConfig(
            host=args.mysql_host,
            port=args.mysql_port,
            user=args.mysql_user,
            password=args.mysql_password,
            database=args.mysql_database,
        )
    )
    if args.init_schema:
        repo.init_schema()
    if not repo.health_check():
        raise RuntimeError("MySQL health check failed")
    return repo


def resolve_openalex_cli_filter(args: argparse.Namespace) -> str:
    explicit_filter = str(args.openalex_cli_filter or "").strip()
    if explicit_filter:
        return explicit_filter
    if args.openalex_cli_plan:
        return load_plan_filter(args.openalex_cli_plan)
    return ""


def load_openalex_cli_raw_works(args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], str]:
    filter_expr = resolve_openalex_cli_filter(args)
    source = OpenAlexCliSource(
        api_key=args.openalex_api_key,
        output_dir=args.openalex_cli_output,
        workers=args.openalex_cli_workers,
        nested=True,
        content="",
    )
    if not args.openalex_cli_skip_download:
        if not filter_expr:
            raise RuntimeError("--openalex-cli-filter or --openalex-cli-plan is required unless --openalex-cli-skip-download is set")
        maybe_tqdm_write(f"[openalex-elt] running OpenAlex CLI download with filter={filter_expr!r}")
        source.download_by_filter(filter_expr, fresh=args.openalex_cli_fresh)
    else:
        maybe_tqdm_write(f"[openalex-elt] loading existing OpenAlex CLI JSON from {source.output_dir}")

    raw_works = source.load_raw_works(limit=args.openalex_cli_ingest_limit)
    if not raw_works:
        raise RuntimeError(f"No OpenAlex metadata JSON works found under {source.output_dir}")
    return raw_works, filter_expr


def resolve_openalex_api_bulk_filter(args: argparse.Namespace) -> str:
    explicit_filter = str(args.openalex_api_bulk_filter or "").strip()
    if explicit_filter:
        return explicit_filter
    if args.openalex_api_bulk_plan:
        return load_plan_filter(args.openalex_api_bulk_plan)
    return ""


def download_openalex_api_bulk(args: argparse.Namespace) -> Tuple[List[Dict[str, Any]], str, Dict[str, Any]]:
    filter_expr = resolve_openalex_api_bulk_filter(args)
    if not filter_expr:
        raise RuntimeError("--openalex-api-bulk-filter or --openalex-api-bulk-plan is required for provider=openalex_api_bulk")

    output_jsonl = Path(args.openalex_api_bulk_output)
    source = OpenAlexApiBulkSource(api_key=args.openalex_api_key)
    maybe_tqdm_write(f"[openalex-elt] downloading OpenAlex API bulk metadata with filter={filter_expr!r}")
    stats = source.download_jsonl(
        filter_expr=filter_expr,
        output_jsonl=output_jsonl,
        target=args.openalex_api_bulk_target,
        select_fields=args.openalex_api_bulk_select_fields,
        per_page=args.openalex_api_bulk_per_page,
        sleep=args.openalex_api_bulk_sleep,
        resume=not args.openalex_api_bulk_no_resume,
    )

    raw_works: List[Dict[str, Any]] = []
    if not args.openalex_api_bulk_download_only:
        for index, row in enumerate(iter_jsonl(output_jsonl), start=1):
            raw_works.append(dict(row))
            if args.openalex_api_bulk_ingest_limit and index >= args.openalex_api_bulk_ingest_limit:
                break
        if not raw_works:
            raise RuntimeError(f"No OpenAlex metadata JSONL works found in {output_jsonl}")
    return raw_works, filter_expr, stats.to_dict()

def main() -> None:
    args = build_parser().parse_args()
    configure_logging("DEBUG" if args.debug else args.log_level)
    logger.debug("CLI args: %s", vars(args))

    source = None if args.provider in {"openalex_cli", "openalex_api_bulk"} else build_source(args.provider, args.openalex_email, args.openalex_cache_dir)

    if args.provider == "openalex_api_bulk" and args.openalex_api_bulk_download_only:
        _, bulk_filter, bulk_stats = download_openalex_api_bulk(args)
        print(json.dumps({"provider": "openalex_api_bulk", "filter": bulk_filter, "bulk_stats": bulk_stats}, ensure_ascii=False, indent=2))
        return

    repo = ensure_repository(args)

    run_id = f"oa_elt_{stable_hash({'query': args.query, 'ts': utc_now_iso()}, 12)}"
    artifact_store = ArtifactStore(args.artifact_root, run_id=run_id)
    run = ResearchRun(
        run_id=run_id,
        question=args.query,
        config=RunConfig(artifact_root=args.artifact_root),
        status="running",
        agent_mode="react",
    )
    repo.create_run(run)

    try:
        cli_filter = ""
        if args.provider == "openalex_cli":
            raw_works, cli_filter = load_openalex_cli_raw_works(args)
            seed_work_id = ""
            seed_title = "OpenAlex CLI metadata"
            crawl_stats = CrawlStats(
                seed_query=args.query,
                seed_work_id=seed_work_id,
                seed_title=seed_title,
                fetched_works=len(raw_works),
                processed_works=len(raw_works),
                warnings=["Loaded metadata from OpenAlex CLI output; BFS expansion was skipped."],
            )
        elif args.provider == "openalex_api_bulk":
            raw_works, cli_filter, bulk_stats = download_openalex_api_bulk(args)
            seed_work_id = ""
            seed_title = "OpenAlex API bulk metadata"
            crawl_stats = CrawlStats(
                seed_query=args.query,
                seed_work_id=seed_work_id,
                seed_title=seed_title,
                fetched_works=len(raw_works),
                processed_works=len(raw_works),
                request_count=int(bulk_stats.get("requests", 0) or 0),
                rate_limited=int(bulk_stats.get("rate_limited", 0) or 0),
                failures=int(bulk_stats.get("failures", 0) or 0),
                elapsed_seconds=float(bulk_stats.get("elapsed_seconds", 0.0) or 0.0),
                works_per_second=float(bulk_stats.get("records_per_second", 0.0) or 0.0),
                warnings=["Loaded metadata from OpenAlex API bulk JSONL; BFS expansion was skipped."],
            )
        else:
            if source is None:
                raise RuntimeError("OpenAlex source was not configured")
            maybe_tqdm_write(f"[openalex-elt] querying OpenAlex with seed query={args.query!r}")
            seed_work_id, seed_raw, scanned = select_seed_work(source, args.query, args.seed_search_limit, select_fields=args.openalex_select_fields)
            seed_title = str(seed_raw.get("title", ""))
            logger.info("Seed selected: %s | %s", seed_work_id, seed_title or "(untitled)")
            logger.debug("Seed raw payload keys: %s", sorted(seed_raw.keys()))

            raw_works, crawl_stats = crawl_bfs(
                source=source,
                query=args.query,
                seed_work_id=seed_work_id,
                seed_raw=seed_raw,
                max_depth=max(1, args.max_depth),
                max_reference_fanout=args.max_reference_fanout,
                max_citing_fanout=args.max_citing_fanout,
                show_progress=not args.no_progress,
                target_works=args.target_works,
                select_fields=args.openalex_select_fields,
                bfs_batch_size=args.bfs_batch_size,
                max_frontier_per_depth=args.max_frontier_per_depth,
                checkpoint_dir=args.checkpoint_dir,
                resume=args.resume,
            )
            crawl_stats.seed_results_scanned = scanned
            crawl_stats.seed_title = seed_title
            if args.write_openalex_cli_plan:
                plan = OpenAlexCliPlanBuilder().write_plan(raw_works, args.query, args.write_openalex_cli_plan)
                maybe_tqdm_write(
                    f"[openalex-elt] wrote OpenAlex CLI plan to {args.write_openalex_cli_plan} "
                    f"with filter={plan.get('recommended_filter', '')!r}"
                )
            logger.debug("Crawl stats: %s", asdict(crawl_stats))

        corpus_id = f"oa_elt_{stable_hash({'query': args.query, 'provider': args.provider, 'seed': seed_work_id, 'filter': cli_filter, 'depth': max(1, args.max_depth)}, 12)}"
        batch, insertion_stats = clean_and_insert(
            raw_works,
            run_id,
            repo,
            corpus_id,
            show_progress=not args.no_progress,
        )

        corpus = build_corpus(args.query, corpus_id, batch)
        repo.create_corpus(corpus, run_id=run_id)
        logger.debug("Corpus created: corpus_id=%s papers=%s", corpus_id, len(corpus.papers))

        neo4j_stats: Dict[str, Any] = {"status": "skipped", "reason": "disabled"}
        if args.sync_neo4j:
            maybe_tqdm_write("[openalex-elt] syncing cleaned data to Neo4j")
            neo4j_stats = sync_to_neo4j(repo, args)

        summary = ELTArtifacts(
            run_id=run_id,
            corpus_id=corpus_id,
            query=args.query,
            seed_work_id=seed_work_id,
            seed_title=seed_title,
            raw_work_count=len(raw_works),
            cleaned_work_count=len(batch.papers),
            cleaned_entity_counts={
                "papers": len(batch.papers),
                "authors": len(batch.authors),
                "institutions": len(batch.institutions),
                "venues": len(batch.venues),
                "concepts": len(batch.concepts),
                "countries": len(batch.countries),
                "work_types": len(batch.work_types),
                "citations": len(batch.citations),
            },
            insertion_stats=asdict(insertion_stats),
            neo4j_stats=neo4j_stats,
            crawl_stats=asdict(crawl_stats),
            warnings=list(crawl_stats.warnings),
        )

        artifact_ref = artifact_store.write_json(
            "openalex_elt",
            f"{run_id}_summary.json",
            summary,
            "openalex_elt_summary",
            {
                "query": args.query,
                "seed_work_id": seed_work_id,
                "corpus_id": corpus_id,
                "raw_work_count": len(raw_works),
                "cleaned_work_count": len(batch.papers),
            },
        )
        run.artifacts.append(artifact_ref)
        run.status = "completed"
        run.completed_at = utc_now_iso()
        repo.update_run_status(run_id, "completed", completed_at=run.completed_at)
        try:
            repo.save_run_outputs(run)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Saving run outputs failed: %s", exc)

        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        logger.info("Completed ETL run %s", run_id)
    except Exception as exc:
        run.status = "failed"
        run.completed_at = utc_now_iso()
        try:
            repo.update_run_status(run_id, "failed", completed_at=run.completed_at)
        except Exception:
            pass
        logger.exception("OpenAlex ELT failed")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
