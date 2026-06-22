from __future__ import annotations

import importlib.util
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

    def search_works(self, query: str, max_results: int):
        seed_order = ["W1", "W2", "W3"]
        return [self.works[wid] for wid in seed_order if wid in self.works][:max_results]

    def get_work(self, work_id: str):
        return self.works.get(normalize_openalex_id(work_id))

    def get_citing_works(self, work_id: str, max_results: int):
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
