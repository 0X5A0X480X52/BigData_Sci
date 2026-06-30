from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

from research_agent.core.utils import normalize_openalex_id
from research_agent.data.cleaners import BatchCleaner


CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "openalex_elt_cli.py"


def load_cli_module():
    spec = importlib.util.spec_from_file_location("openalex_elt_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


class StubOpenAlexProvider:
    def __init__(self, works: dict[str, dict]):
        self.works = works
        self.select_fields_seen: list[str] = []
        self.batch_calls: list[list[str]] = []
        self.stats = {"requests": 0, "cache_hits": 0, "failures": 0, "rate_limited": 0}

    def _record_select(self, select_fields: str = ""):
        if select_fields:
            self.select_fields_seen.append(select_fields)

    def search_works(self, query: str, max_results: int, select_fields: str = ""):
        self._record_select(select_fields)
        self.stats["requests"] += 1
        seed_order = ["W1", "W2", "W3"]
        return [self.works[wid] for wid in seed_order if wid in self.works][:max_results]

    def get_work(self, work_id: str, select_fields: str = ""):
        self._record_select(select_fields)
        self.stats["requests"] += 1
        return self.works.get(normalize_openalex_id(work_id))

    def get_works_batch(self, work_ids: list[str], select_fields: str = ""):
        self._record_select(select_fields)
        self.batch_calls.append([normalize_openalex_id(wid) for wid in work_ids])
        self.stats["requests"] += 1
        return [self.works[normalize_openalex_id(wid)] for wid in work_ids if normalize_openalex_id(wid) in self.works]

    def get_citing_works(self, work_id: str, max_results: int, select_fields: str = ""):
        self._record_select(select_fields)
        self.stats["requests"] += 1
        normalized = normalize_openalex_id(work_id)
        results = [
            work for work in self.works.values()
            if normalized in [normalize_openalex_id(ref) for ref in work.get("referenced_works", [])]
        ]
        return results[:max_results]


def sample_work(work_id: str, refs: list[str] | None = None):
    refs = refs or []
    idx = int(work_id[1:])
    return {
        "id": f"https://openalex.org/{work_id}",
        "title": f"Paper {work_id}",
        "abstract": f"Abstract for {work_id}",
        "publication_year": 2020 + (idx % 3),
        "cited_by_count": idx,
        "authorships": [
            {"author": {"display_name": f"Author {idx}"}},
        ],
        "concepts": [{"display_name": "bert", "score": 0.9}],
        "referenced_works": refs,
        "primary_location": {"source": {"id": f"https://openalex.org/S{idx}", "display_name": f"Venue {idx}"}},
    }


def test_seed_selection_uses_first_search_hit():
    cli = load_cli_module()
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1"),
        "W2": sample_work("W2"),
        "W3": sample_work("W3"),
    })

    seed_id, seed_raw, scanned = cli.select_seed_work(provider, "bert", 1)

    assert seed_id == "W1"
    assert seed_raw["id"] == "https://openalex.org/W1"
    assert scanned == 1


def test_bfs_expands_references_and_citations_with_deduplication():
    cli = load_cli_module()
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1", ["W2", "W3"]),
        "W2": sample_work("W2", ["W4"]),
        "W3": sample_work("W3", ["W4"]),
        "W4": sample_work("W4"),
        "W5": sample_work("W5", ["W1"]),
        "W6": sample_work("W6", ["W2"]),
    })

    raw_works, stats = cli.crawl_bfs(
        source=provider,
        query="bert",
        seed_work_id="W1",
        seed_raw=provider.works["W1"],
        max_depth=2,
        max_reference_fanout=0,
        max_citing_fanout=0,
    )

    assert stats.max_depth_reached == 2
    assert stats.processed_works == 6
    assert {normalize_openalex_id(work["id"]) for work in raw_works} == {"W1", "W2", "W3", "W4", "W5", "W6"}

    batch = BatchCleaner().process_batch(raw_works)
    assert len(batch.citations) >= 5


def test_reference_fanout_limits_bfs_expansion():
    cli = load_cli_module()
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1", ["W2", "W3", "W4"]),
        "W2": sample_work("W2"),
        "W3": sample_work("W3"),
        "W4": sample_work("W4"),
    })

    raw_works, stats = cli.crawl_bfs(
        source=provider,
        query="bert",
        seed_work_id="W1",
        seed_raw=provider.works["W1"],
        max_depth=1,
        max_reference_fanout=1,
        max_citing_fanout=0,
    )

    assert stats.processed_works == 2
    assert {normalize_openalex_id(work["id"]) for work in raw_works} == {"W1", "W2"}


def test_parser_accepts_openalex_cli_options_and_filter_precedence():
    cli = load_cli_module()
    tmp_path = Path("outputs/test_artifacts/openalex_cli_parser")
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text('{"recommended_filter": "topics.id:T1,type:article"}', encoding="utf-8")

    args = cli.build_parser().parse_args([
        "deep learning",
        "--provider", "openalex_cli",
        "--openalex-cli-filter", "topics.id:T2,type:article",
        "--openalex-cli-plan", str(plan_path),
        "--openalex-cli-output", str(tmp_path),
        "--openalex-cli-workers", "7",
        "--openalex-cli-skip-download",
        "--openalex-cli-ingest-limit", "3",
    ])

    assert args.provider == "openalex_cli"
    assert args.openalex_cli_workers == 7
    assert args.openalex_cli_skip_download is True
    assert args.openalex_cli_ingest_limit == 3
    assert cli.resolve_openalex_cli_filter(args) == "topics.id:T2,type:article"

    args.openalex_cli_filter = ""
    assert cli.resolve_openalex_cli_filter(args) == "topics.id:T1,type:article"



def test_bfs_target_limit_and_select_stats_are_applied():
    cli = load_cli_module()
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1", ["W2", "W3", "W4"]),
        "W2": sample_work("W2"),
        "W3": sample_work("W3"),
        "W4": sample_work("W4"),
        "W5": sample_work("W5", ["W1"]),
    })

    raw_works, stats = cli.crawl_bfs(
        source=provider,
        query="bert",
        seed_work_id="W1",
        seed_raw=provider.works["W1"],
        max_depth=1,
        max_reference_fanout=50,
        max_citing_fanout=50,
        target_works=3,
        select_fields="id,title,referenced_works",
        show_progress=False,
    )

    assert len(raw_works) == 3
    assert stats.processed_works == 3
    assert stats.request_count == provider.stats["requests"]
    assert stats.works_per_second >= 0
    assert "id,title,referenced_works" in provider.select_fields_seen



def test_batch_bfs_dedupes_references_across_frontier_and_writes_artifacts():
    cli = load_cli_module()
    tmp_path = Path("outputs/test_artifacts/openalex_batch_bfs")
    shutil.rmtree(tmp_path, ignore_errors=True)
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1", ["W2", "W3"]),
        "W2": sample_work("W2", ["W4"]),
        "W3": sample_work("W3", ["W4"]),
        "W4": sample_work("W4"),
    })

    raw_works, stats = cli.crawl_bfs(
        source=provider,
        query="bert",
        seed_work_id="W1",
        seed_raw=provider.works["W1"],
        max_depth=2,
        max_reference_fanout=50,
        max_citing_fanout=0,
        bfs_batch_size=50,
        max_frontier_per_depth=100,
        checkpoint_dir=str(tmp_path),
        show_progress=False,
    )

    assert {normalize_openalex_id(work["id"]) for work in raw_works} == {"W1", "W2", "W3", "W4"}
    assert ["W4"] in provider.batch_calls
    assert stats.reference_edges == 4
    assert (tmp_path / "bfs_config.json").exists()
    assert (tmp_path / "layer_1_works.jsonl").exists()
    assert (tmp_path / "edges_cites.jsonl").exists()
    assert (tmp_path / "checkpoint.json").exists()
    assert (tmp_path / "openalex_download_plan.json").exists()


def test_batch_bfs_target_limit_trims_before_reference_batch_fetch():
    cli = load_cli_module()
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1", ["W2", "W3", "W4"]),
        "W2": sample_work("W2"),
        "W3": sample_work("W3"),
        "W4": sample_work("W4"),
        "W5": sample_work("W5", ["W1"]),
    })

    raw_works, stats = cli.crawl_bfs(
        source=provider,
        query="bert",
        seed_work_id="W1",
        seed_raw=provider.works["W1"],
        max_depth=1,
        max_reference_fanout=50,
        max_citing_fanout=50,
        target_works=3,
        show_progress=False,
    )

    assert ["W2", "W3"] in provider.batch_calls
    assert all("W4" not in call for call in provider.batch_calls)
    assert len(raw_works) == 3
    assert stats.processed_works == 3


def test_batch_bfs_resume_from_checkpoint_frontier():
    cli = load_cli_module()
    tmp_path = Path("outputs/test_artifacts/openalex_batch_bfs_resume")
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    provider = StubOpenAlexProvider({
        "W1": sample_work("W1", ["W2"]),
        "W2": sample_work("W2"),
    })
    checkpoint = {
        "next_depth": 1,
        "frontier": ["W2"],
        "raw_by_id": {"W1": provider.works["W1"], "W2": provider.works["W2"]},
        "discovered_depth": {"W1": 0, "W2": 1},
        "visited": ["W1"],
        "processed_order": ["W1"],
        "edges": [{"source": "W1", "target": "W2", "type": "references"}],
        "stats": {"seed_query": "bert", "seed_work_id": "W1", "fetched_works": 1, "reference_edges": 1},
    }
    (tmp_path / "checkpoint.json").write_text(json.dumps(checkpoint), encoding="utf-8")

    raw_works, stats = cli.crawl_bfs(
        source=provider,
        query="bert",
        seed_work_id="W1",
        seed_raw=provider.works["W1"],
        max_depth=1,
        checkpoint_dir=str(tmp_path),
        resume=True,
        show_progress=False,
    )

    assert [normalize_openalex_id(work["id"]) for work in raw_works] == ["W1", "W2"]
    assert stats.processed_works == 2


def test_openalex_api_bulk_parser_and_filter_precedence():
    cli = load_cli_module()
    tmp_path = Path("outputs/test_artifacts/openalex_api_bulk_parser")
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    plan_path = tmp_path / "openalex_download_plan.json"
    plan_path.write_text('{"recommended_filter": "topics.id:T1,type:article"}', encoding="utf-8")

    args = cli.build_parser().parse_args([
        "deep learning",
        "--provider", "openalex_api_bulk",
        "--openalex-api-bulk-filter", "topics.id:T2,type:article",
        "--openalex-api-bulk-plan", str(plan_path),
        "--openalex-api-bulk-output", str(tmp_path / "metadata.jsonl"),
        "--openalex-api-bulk-target", "100",
        "--openalex-api-bulk-download-only",
    ])

    assert args.provider == "openalex_api_bulk"
    assert cli.resolve_openalex_api_bulk_filter(args) == "topics.id:T2,type:article"
    args.openalex_api_bulk_filter = ""
    assert cli.resolve_openalex_api_bulk_filter(args) == "topics.id:T1,type:article"
