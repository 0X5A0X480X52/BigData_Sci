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
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

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
from research_agent.data.openalex_source import OpenAlexSource
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
    parser.add_argument("--provider", choices=["openalex", "fixture"], default="openalex",
                        help="Data provider. Use fixture for offline smoke tests.")
    parser.add_argument("--seed-search-limit", type=int, default=1,
                        help="How many search results to inspect when auto-selecting the seed.")
    parser.add_argument("--max-depth", type=int, default=2, help="BFS depth limit. Default: 2")
    parser.add_argument("--max-reference-fanout", type=int, default=0,
                        help="Per-node reference fanout cap. 0 means unlimited.")
    parser.add_argument("--max-citing-fanout", type=int, default=0,
                        help="Per-node citing-work fanout cap. 0 means unlimited.")
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


def select_seed_work(source: Any, query: str, seed_search_limit: int) -> Tuple[str, Dict[str, Any], int]:
    limit = max(1, seed_search_limit)
    logger.debug("Selecting seed from top %s OpenAlex search results for query=%r", limit, query)
    seed_candidates = _retry(
        lambda: list(source.search_works(query, max_results=limit)),
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
    max_depth: int = 2,
    max_reference_fanout: int = 0,
    max_citing_fanout: int = 0,
    show_progress: bool = True,
) -> Tuple[List[Dict[str, Any]], CrawlStats]:
    """Breadth-first crawl through references and citing works.

    Progress bar semantics:
    - progress.n: processed works
    - progress.total: discovered works
    """

    seed_work_id = normalize_openalex_id(seed_work_id)

    stats = CrawlStats(
        seed_query=query,
        seed_work_id=seed_work_id,
        seed_title=str(seed_raw.get("title", "")),
        seed_results_scanned=1,
    )

    raw_by_id: Dict[str, Dict[str, Any]] = {}
    discovered_depth: Dict[str, int] = {seed_work_id: 0}
    processed: set[str] = set()
    processed_order: List[str] = []
    queue: Deque[Tuple[str, int]] = deque([(seed_work_id, 0)])

    raw_seed = dict(seed_raw)
    raw_seed["id"] = raw_seed.get("id") or f"https://openalex.org/{seed_work_id}"
    raw_by_id[seed_work_id] = raw_seed

    progress = None
    if show_progress and tqdm is not None:
        progress = tqdm(
            total=1,  # seed work
            desc="BFS crawl",
            unit="work",
            dynamic_ncols=True,
        )

    def _enqueue_new(next_work_id: str, next_depth: int) -> bool:
        """Enqueue a work only if it has not been discovered before."""
        if not next_work_id:
            return False

        if next_work_id in discovered_depth:
            return False

        discovered_depth[next_work_id] = next_depth
        queue.append((next_work_id, next_depth))
        return True

    def _bump_total(delta: int = 1) -> None:
        """Increase tqdm total dynamically."""
        if progress is not None and delta > 0:
            progress.total += delta
            progress.refresh()

    def _update_progress(depth: int) -> None:
        """Update processed progress and display useful BFS state."""
        if progress is not None:
            progress.update(1)
            progress.set_postfix(
                {
                    "depth": depth,
                    "queue": len(queue),
                    "fetched": stats.fetched_works,
                    "missing": stats.missing_works,
                    "edges": stats.reference_edges + stats.citing_edges,
                }
            )

    try:
        while queue:
            work_id, depth = queue.popleft()

            if work_id in processed:
                continue

            stats.max_depth_reached = max(stats.max_depth_reached, depth)

            logger.debug(
                "BFS visit work_id=%s depth=%s queue=%s processed=%s",
                work_id,
                depth,
                len(queue),
                len(processed),
            )

            raw = raw_by_id.get(work_id)

            if raw is None:
                raw = _retry(
                    lambda: source.get_work(work_id),
                    attempts=3,
                    delay=1.0,
                    label=f"fetch work {work_id}",
                )

                if raw:
                    raw_by_id[work_id] = dict(raw)

            if not raw:
                stats.missing_works += 1
                processed.add(work_id)
                _update_progress(depth)
                continue

            raw_id = normalize_openalex_id(str(raw.get("id", work_id)))
            if raw_id and raw_id != work_id:
                raw_by_id[raw_id] = dict(raw)
                work_id = raw_id

            if work_id in processed:
                continue

            processed.add(work_id)
            processed_order.append(work_id)
            stats.fetched_works += 1

            _update_progress(depth)

            if depth >= max_depth:
                continue

            # ----------------------------
            # Expand references
            # ----------------------------
            refs = list(raw.get("referenced_works") or [])

            if max_reference_fanout > 0:
                refs = refs[:max_reference_fanout]

            logger.debug(
                "Expanding %s references from work_id=%s",
                len(refs),
                work_id,
            )

            for ref in maybe_tqdm(
                refs,
                enabled=show_progress,
                desc=f"refs {work_id}",
                leave=False,
                unit="ref",
            ):
                ref_id = normalize_openalex_id(str(ref))
                if not ref_id:
                    continue

                stats.reference_edges += 1

                added = _enqueue_new(ref_id, depth + 1)
                if added:
                    stats.enqueued += 1
                    _bump_total(1)

            logger.debug(
                "Reference expansion complete for %s: enqueued=%s total_queue=%s",
                work_id,
                stats.enqueued,
                len(queue),
            )

            # ----------------------------
            # Expand citing works
            # ----------------------------
            citing_limit = (
                max_citing_fanout
                if max_citing_fanout > 0
                else UNLIMITED_FANOUT
            )

            citing_raws = _retry(
                lambda: list(
                    source.get_citing_works(
                        work_id,
                        max_results=citing_limit,
                    )
                ),
                attempts=3,
                delay=1.0,
                label=f"fetch citing works for {work_id}",
            ) or []

            logger.debug(
                "Expanding %s citing works from work_id=%s",
                len(citing_raws),
                work_id,
            )

            for citing_raw in maybe_tqdm(
                citing_raws,
                enabled=show_progress,
                desc=f"cites {work_id}",
                leave=False,
                unit="cite",
            ):
                citing_id = normalize_openalex_id(str(citing_raw.get("id", "")))
                if not citing_id:
                    continue

                stats.citing_edges += 1
                raw_by_id.setdefault(citing_id, dict(citing_raw))

                added = _enqueue_new(citing_id, depth + 1)
                if added:
                    stats.enqueued += 1
                    _bump_total(1)

            logger.debug(
                "Citing expansion complete for %s: citing_edges=%s total_queue=%s",
                work_id,
                stats.citing_edges,
                len(queue),
            )

    finally:
        if progress is not None:
            progress.close()

    stats.processed_works = len(processed_order)

    ordered_raws = [
        raw_by_id[wid]
        for wid in processed_order
        if wid in raw_by_id
    ]

    return ordered_raws, stats

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


def _enqueue(discovered_depth: Dict[str, int], queue: Deque[Tuple[str, int]], work_id: str, depth: int) -> None:
    prev = discovered_depth.get(work_id)
    if prev is not None and prev <= depth:
        return
    discovered_depth[work_id] = depth
    queue.append((work_id, depth))


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


def main() -> None:
    args = build_parser().parse_args()
    configure_logging("DEBUG" if args.debug else args.log_level)
    logger.debug("CLI args: %s", vars(args))

    source = build_source(args.provider, args.openalex_email, args.openalex_cache_dir)
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
        maybe_tqdm_write(f"[openalex-elt] querying OpenAlex with seed query={args.query!r}")
        seed_work_id, seed_raw, scanned = select_seed_work(source, args.query, args.seed_search_limit)
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
        )
        crawl_stats.seed_results_scanned = scanned
        crawl_stats.seed_title = seed_title
        logger.debug("Crawl stats: %s", asdict(crawl_stats))

        corpus_id = f"oa_elt_{stable_hash({'query': args.query, 'seed': seed_work_id, 'depth': max(1, args.max_depth)}, 12)}"
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
