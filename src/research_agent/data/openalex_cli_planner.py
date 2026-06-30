"""Build OpenAlex CLI download plans from BFS scout results."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from research_agent.core.utils import normalize_openalex_id


class OpenAlexCliPlanBuilder:
    """Summarize BFS raw works and produce a reproducible CLI download plan."""

    def build_from_bfs(
        self,
        raw_works: List[Dict[str, Any]],
        query: str,
        top_k_topics: int = 10,
        min_topic_count: int = 5,
    ) -> Dict[str, Any]:
        topic_counter: Counter[str] = Counter()
        topic_names: Dict[str, str] = {}
        year_counter: Counter[str] = Counter()
        source_counter: Counter[str] = Counter()
        source_names: Dict[str, str] = {}
        work_ids: List[str] = []

        for work in raw_works:
            work_id = normalize_openalex_id(str(work.get("id", "")))
            if work_id:
                work_ids.append(work_id)

            year = work.get("publication_year")
            if isinstance(year, int):
                year_counter[str(year)] += 1

            source = (work.get("primary_location") or {}).get("source") or {}
            source_id = _openalex_tail(source.get("id"))
            if source_id:
                source_counter[source_id] += 1
                source_names[source_id] = str(source.get("display_name") or source_id)

            for topic in work.get("topics") or work.get("concepts") or []:
                topic_id = _openalex_tail(topic.get("id"))
                if not topic_id:
                    continue
                score = topic.get("score", 1.0)
                try:
                    topic_counter[topic_id] += float(score if score is not None else 1.0)
                except (TypeError, ValueError):
                    topic_counter[topic_id] += 1.0
                topic_names[topic_id] = str(topic.get("display_name") or topic_id)

        selected_topic_ids = [
            topic_id
            for topic_id, score in topic_counter.most_common(max(1, top_k_topics))
            if score >= min_topic_count
        ]

        years = sorted(int(year) for year in year_counter)
        start_year = max(2012, years[0]) if years else 2012
        end_year = years[-1] if years else 2026
        type_filter = ["article"]
        topic_expr = "|".join(selected_topic_ids)
        recommended_filter = (
            f"topics.id:{topic_expr},publication_year:{start_year}-{end_year},type:{type_filter[0]}"
            if topic_expr
            else f"publication_year:{start_year}-{end_year},type:{type_filter[0]}"
        )

        unique_work_ids = list(dict.fromkeys(work_ids))
        return {
            "query": query,
            "seed_work_ids": unique_work_ids[:10],
            "selected_topic_ids": selected_topic_ids,
            "year_range": [start_year, end_year],
            "type_filter": type_filter,
            "recommended_filter": recommended_filter,
            "ids_file": "",
            "rationale": {
                "bfs_work_count": len(raw_works),
                "seed_count": len(unique_work_ids),
                "top_topics": _ranked(topic_counter, topic_names),
                "top_sources": _ranked(source_counter, source_names),
                "year_distribution": dict(sorted(year_counter.items())),
            },
        }

    def write_plan(
        self,
        raw_works: List[Dict[str, Any]],
        query: str,
        plan_path: str | Path,
        top_k_topics: int = 10,
        min_topic_count: int = 5,
    ) -> Dict[str, Any]:
        path = Path(plan_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        plan = self.build_from_bfs(raw_works, query, top_k_topics=top_k_topics, min_topic_count=min_topic_count)

        ids_path = path.with_name(f"{path.stem.replace('_cli_plan', '')}_work_ids.txt")
        work_ids = [
            normalize_openalex_id(str(work.get("id", "")))
            for work in raw_works
            if normalize_openalex_id(str(work.get("id", "")))
        ]
        unique_work_ids = list(dict.fromkeys(work_ids))
        ids_path.write_text("\n".join(unique_work_ids) + ("\n" if unique_work_ids else ""), encoding="utf-8")
        plan["ids_file"] = str(ids_path)
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        return plan


def load_plan_filter(plan_path: str | Path) -> str:
    data = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("OpenAlex CLI plan must be a JSON object")
    filter_expr = data.get("recommended_filter")
    if not isinstance(filter_expr, str) or not filter_expr.strip():
        raise ValueError("OpenAlex CLI plan is missing recommended_filter")
    return filter_expr.strip()


def _openalex_tail(value: Any) -> str:
    if not value:
        return ""
    return str(value).rstrip("/").rsplit("/", 1)[-1].strip()


def _ranked(counter: Counter[str], names: Dict[str, str], limit: int = 20) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key, score in counter.most_common(limit):
        count: float | int = score
        if isinstance(score, float) and score.is_integer():
            count = int(score)
        rows.append({"id": key, "name": names.get(key, key), "count": count})
    return rows
