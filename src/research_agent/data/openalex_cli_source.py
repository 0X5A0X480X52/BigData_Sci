"""OpenAlex official CLI metadata source."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List


class OpenAlexCliSource:
    """Download and load OpenAlex works metadata produced by ``openalex download``."""

    def __init__(
        self,
        api_key: str = "",
        output_dir: str = "data/openalex_cli_downloads",
        workers: int = 50,
        nested: bool = True,
        content: str = "",
    ) -> None:
        self.api_key = api_key
        self.output_dir = Path(output_dir)
        self.workers = max(1, int(workers or 50))
        self.nested = nested
        self.content = content

    def download_by_filter(
        self,
        filter_expr: str,
        *,
        fresh: bool = False,
        quiet: bool = False,
    ) -> None:
        """Run ``openalex download`` for a filter expression."""
        if not filter_expr:
            raise ValueError("filter_expr is required for OpenAlex CLI downloads")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "openalex",
            "download",
            "--output",
            str(self.output_dir),
            "--filter",
            filter_expr,
            "--workers",
            str(self.workers),
        ]
        if self.api_key:
            cmd.extend(["--api-key", self.api_key])
        if self.nested:
            cmd.append("--nested")
        if fresh:
            cmd.append("--fresh")
        if quiet:
            cmd.append("--quiet")
        if self.content:
            cmd.extend(["--content", self.content])

        subprocess.run(cmd, check=True)

    def iter_metadata_files(self) -> Iterable[Path]:
        """Yield JSON files below the output directory in stable order."""
        if not self.output_dir.exists():
            return
        yield from sorted(path for path in self.output_dir.rglob("*.json") if path.is_file())

    def load_raw_works(self, limit: int = 0) -> List[Dict[str, Any]]:
        """Load OpenAlex Work JSON dictionaries, skipping malformed files."""
        max_items = max(0, int(limit or 0))
        works: List[Dict[str, Any]] = []
        for path in self.iter_metadata_files():
            for work in _load_work_records(path):
                if not work.get("id"):
                    continue
                works.append(work)
                if max_items and len(works) >= max_items:
                    return works
        return works


def _load_work_records(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return

    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            for item in data["results"]:
                if isinstance(item, dict):
                    yield item
            return
        yield data
        return

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item
