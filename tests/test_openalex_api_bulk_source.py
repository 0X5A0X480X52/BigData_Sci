from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from research_agent.data.openalex_api_bulk_source import OpenAlexApiBulkSource, iter_jsonl


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self):
        self.calls = []
        self.pages = [
            {"results": [{"id": "https://openalex.org/W1"}, {"id": "https://openalex.org/W2"}], "meta": {"next_cursor": "abc"}},
            {"results": [{"id": "https://openalex.org/W3"}], "meta": {"next_cursor": ""}},
        ]

    def get(self, url, params, timeout):
        self.calls.append({"url": url, "params": dict(params), "timeout": timeout})
        return FakeResponse(self.pages.pop(0))


def test_openalex_api_bulk_source_writes_jsonl_and_checkpoint():
    output_dir = Path("outputs/test_artifacts") / f"openalex_api_bulk_{uuid.uuid4().hex}"
    shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = output_dir / "metadata.jsonl"
    checkpoint = output_dir / "metadata.checkpoint.json"
    session = FakeSession()

    source = OpenAlexApiBulkSource(api_key="KEY", session=session)
    stats = source.download_jsonl(
        filter_expr="publication_year:2024,type:article",
        output_jsonl=output_jsonl,
        checkpoint_path=checkpoint,
        target=3,
        select_fields="id,title",
        per_page=200,
        sleep=0,
        resume=False,
    )

    assert stats.records == 3
    assert stats.requests == 2
    assert stats.output_jsonl == str(output_jsonl)
    assert [row["id"] for row in iter_jsonl(output_jsonl)] == [
        "https://openalex.org/W1",
        "https://openalex.org/W2",
        "https://openalex.org/W3",
    ]
    first_params = session.calls[0]["params"]
    assert first_params["filter"] == "publication_year:2024,type:article"
    assert first_params["select"] == "id,title"
    assert first_params["per_page"] == 100
    assert first_params["cursor"] == "*"
    assert first_params["api_key"] == "KEY"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["records"] == 3
