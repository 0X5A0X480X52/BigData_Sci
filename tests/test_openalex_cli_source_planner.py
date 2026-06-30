from __future__ import annotations

import json
import shutil
from pathlib import Path

from research_agent.data.cleaners import BatchCleaner
from research_agent.data.openalex_cli_planner import OpenAlexCliPlanBuilder
from research_agent.data.openalex_cli_source import OpenAlexCliSource


def openalex_work(work_id: str, year: int = 2020, topic_id: str = "T1", score: float = 1.0):
    return {
        "id": f"https://openalex.org/{work_id}",
        "title": f"Paper {work_id}",
        "abstract": f"Abstract {work_id}",
        "publication_year": year,
        "type": "article",
        "authorships": [{"author": {"id": "https://openalex.org/A1", "display_name": "Author One"}}],
        "topics": [{"id": f"https://openalex.org/{topic_id}", "display_name": "Deep Learning", "score": score}],
        "referenced_works": ["https://openalex.org/W0"],
        "primary_location": {"source": {"id": "https://openalex.org/S1", "display_name": "Venue One"}},
    }


def test_openalex_cli_source_loads_flat_nested_and_limited_json():
    tmp_path = Path("outputs/test_artifacts/openalex_cli_source")
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "flat.json").write_text(json.dumps(openalex_work("W1")), encoding="utf-8")
    nested = tmp_path / "nested" / "T1"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "work.json").write_text(json.dumps(openalex_work("W2")), encoding="utf-8")
    (tmp_path / "bad.json").write_text("not json", encoding="utf-8")
    (tmp_path / "not_work.json").write_text(json.dumps({"meta": {"count": 2}}), encoding="utf-8")

    source = OpenAlexCliSource(output_dir=str(tmp_path))

    assert len(list(source.iter_metadata_files())) == 4
    works = source.load_raw_works(limit=1)
    assert [work["id"] for work in works] == ["https://openalex.org/W1"]

    all_works = source.load_raw_works()
    assert {work["id"] for work in all_works} == {"https://openalex.org/W1", "https://openalex.org/W2"}
    assert len(BatchCleaner().process_batch(all_works).papers) == 2


def test_openalex_cli_planner_builds_and_writes_download_plan():
    tmp_path = Path("outputs/test_artifacts/openalex_cli_planner")
    shutil.rmtree(tmp_path, ignore_errors=True)
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw_works = [
        openalex_work("W1", year=2010, topic_id="T1", score=2.0),
        openalex_work("W2", year=2024, topic_id="T1", score=2.0),
        openalex_work("W3", year=2022, topic_id="T2", score=0.5),
    ]
    builder = OpenAlexCliPlanBuilder()

    plan = builder.build_from_bfs(raw_works, "deep learning", top_k_topics=5, min_topic_count=2)

    assert plan["selected_topic_ids"] == ["T1"]
    assert plan["year_range"] == [2012, 2024]
    assert plan["recommended_filter"] == "topics.id:T1,publication_year:2012-2024,type:article"
    assert plan["rationale"]["bfs_work_count"] == 3
    assert plan["rationale"]["top_sources"][0]["id"] == "S1"

    plan_path = tmp_path / "deep_learning_cli_plan.json"
    written = builder.write_plan(raw_works, "deep learning", plan_path, top_k_topics=5, min_topic_count=2)
    ids_path = tmp_path / "deep_learning_work_ids.txt"

    assert plan_path.exists()
    assert ids_path.read_text(encoding="utf-8").splitlines() == ["W1", "W2", "W3"]
    assert written["ids_file"] == str(ids_path)
    assert json.loads(plan_path.read_text(encoding="utf-8"))["recommended_filter"] == plan["recommended_filter"]
