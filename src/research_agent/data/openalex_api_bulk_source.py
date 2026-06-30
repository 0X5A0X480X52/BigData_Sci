"""Bulk OpenAlex metadata fetcher using cursor paging and JSONL output."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:  # pragma: no cover - import guard exercised through normal environments
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]


DEFAULT_BULK_SELECT = (
    "id,doi,title,publication_year,publication_date,type,cited_by_count,"
    "authorships,primary_location,topics,keywords,referenced_works,open_access"
)


@dataclass
class OpenAlexApiBulkStats:
    records: int = 0
    bytes_written: int = 0
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0
    failures: int = 0
    elapsed_seconds: float = 0.0
    output_jsonl: str = ""
    checkpoint_path: str = ""
    next_cursor: str = ""

    @property
    def records_per_second(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.records / self.elapsed_seconds

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["records_per_second"] = self.records_per_second
        return data


class OpenAlexApiBulkSource:
    """Fetch Works metadata through the OpenAlex list API into JSONL."""

    def __init__(
        self,
        *,
        api_key: str = "",
        base_url: str = "https://api.openalex.org/works",
        user_agent: str = "BigData_Sci OpenAlex bulk metadata fetcher",
        session: Any = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.user_agent = user_agent
        self.session = session or self._build_session()

    def download_jsonl(
        self,
        *,
        filter_expr: str,
        output_jsonl: str | Path,
        target: int = 0,
        select_fields: str = DEFAULT_BULK_SELECT,
        per_page: int = 100,
        sleep: float = 0.0,
        checkpoint_path: str | Path | None = None,
        resume: bool = True,
        max_retries: int = 5,
        backoff_factor: float = 1.5,
        timeout: tuple[int, int] = (10, 90),
    ) -> OpenAlexApiBulkStats:
        """Download metadata JSONL using cursor paging.

        ``target=0`` means continue until OpenAlex returns no more results.
        """
        if not filter_expr:
            raise ValueError("filter_expr is required")
        per_page = min(100, max(1, int(per_page or 100)))
        max_records = max(0, int(target or 0))
        output_path = Path(output_jsonl)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint = Path(checkpoint_path) if checkpoint_path else output_path.with_suffix(output_path.suffix + ".checkpoint.json")

        cursor = "*"
        mode = "w"
        existing_records = 0
        if resume and checkpoint.exists() and output_path.exists():
            state = _read_json(checkpoint)
            cursor = str(state.get("next_cursor") or "*")
            existing_records = int(state.get("records", 0) or 0)
            mode = "a"

        stats = OpenAlexApiBulkStats(
            records=existing_records,
            output_jsonl=str(output_path),
            checkpoint_path=str(checkpoint),
            next_cursor=cursor,
        )
        started = time.perf_counter()

        with output_path.open(mode, encoding="utf-8") as handle:
            while max_records <= 0 or stats.records < max_records:
                params: Dict[str, Any] = {
                    "filter": filter_expr,
                    "select": select_fields,
                    "per_page": per_page,
                    "cursor": cursor,
                }
                if self.api_key:
                    params["api_key"] = self.api_key

                data = self._get_page(params, timeout=timeout, stats=stats, max_retries=max_retries, backoff_factor=backoff_factor)
                results = data.get("results") or []
                if not results:
                    break

                for item in results:
                    if max_records > 0 and stats.records >= max_records:
                        break
                    line = json.dumps(item, ensure_ascii=False)
                    handle.write(line + "\n")
                    stats.records += 1
                    stats.bytes_written += len(line.encode("utf-8")) + 1

                cursor = str((data.get("meta") or {}).get("next_cursor") or "")
                stats.next_cursor = cursor
                _write_json(checkpoint, {"next_cursor": cursor, "records": stats.records, "filter": filter_expr, "select": select_fields})
                if not cursor:
                    break
                if sleep > 0:
                    time.sleep(sleep)

        stats.elapsed_seconds = time.perf_counter() - started
        return stats

    def _get_page(
        self,
        params: Dict[str, Any],
        *,
        timeout: tuple[int, int],
        stats: OpenAlexApiBulkStats,
        max_retries: int,
        backoff_factor: float,
    ) -> Dict[str, Any]:
        last_error: Optional[BaseException] = None
        for attempt in range(max(1, max_retries) + 1):
            try:
                stats.requests += 1
                response = self.session.get(self.base_url, params=params, timeout=timeout)
                if getattr(response, "status_code", 200) == 429:
                    stats.rate_limited += 1
                    retry_after = getattr(response, "headers", {}).get("Retry-After")
                    wait = float(retry_after) if retry_after and str(retry_after).isdigit() else backoff_factor * (attempt + 1)
                    stats.retries += 1
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                return dict(response.json())
            except BaseException as exc:  # noqa: BLE001 - preserve retry context for CLI/reporting
                last_error = exc
                if attempt >= max_retries:
                    break
                stats.retries += 1
                time.sleep(backoff_factor * (attempt + 1))
        stats.failures += 1
        if last_error is not None:
            raise RuntimeError(f"OpenAlex API bulk request failed: {last_error}") from last_error
        raise RuntimeError("OpenAlex API bulk request failed")

    def _build_session(self) -> Any:
        if requests is None:
            raise ImportError("requests is required for OpenAlexApiBulkSource. Install with: pip install requests")
        session = requests.Session()
        session.headers.update({"User-Agent": self.user_agent})
        return session


def iter_jsonl(path: str | Path) -> Iterable[Dict[str, Any]]:
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
