"""OpenAlex API data source with pyalex integration, caching, and rate limiting."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from research_agent.core.config import OpenAlexConfig

DEFAULT_SCOUT_SELECT = (
    "id,doi,title,publication_year,publication_date,type,cited_by_count,"
    "authorships,primary_location,topics,concepts,referenced_works,open_access"
)
OPENALEX_LIST_PER_PAGE = 100


class OpenAlexSource:
    """Fetches scholarly data from the OpenAlex API via pyalex.

    Features
    --------
    * Pagination — cursor-based, conservative page size.
    * Caching — HTTP-response-level JSON file cache under ``cache_dir``.
    * Rate limiting — token-bucket, configurable requests/second.
    * Retry — delegated to pyalex, plus local failure accounting.
    * Polite pool — configurable email for OpenAlex polite pool.
    """

    def __init__(self, config: OpenAlexConfig) -> None:
        self.config = config
        self._cache_dir = Path(config.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._pyalex = None
        self._rate_limit_ts: float = 0.0
        self.stats: Dict[str, int] = {
            "requests": 0,
            "cache_hits": 0,
            "cache_writes": 0,
            "failures": 0,
            "rate_limited": 0,
        }

    # ── Lazy pyalex import ───────────────────────────────────

    def _ensure_pyalex(self) -> None:
        if self._pyalex is not None:
            return
        try:
            import pyalex
            if self.config.email:
                pyalex.config.email = self.config.email
            pyalex.config.max_retries = self.config.max_retries
            self._pyalex = pyalex
        except ImportError:
            raise ImportError(
                "pyalex is required for real OpenAlex access. "
                "Install with: pip install pyalex. "
                "Or use --provider fixture for offline mode."
            )

    # ── Rate limiting ────────────────────────────────────────

    def _wait_for_rate_limit(self) -> None:
        """Simple token-bucket rate limiter."""
        if self.config.rate_limit_per_second <= 0:
            return
        now = time.monotonic()
        elapsed = now - self._rate_limit_ts
        min_interval = 1.0 / self.config.rate_limit_per_second
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._rate_limit_ts = time.monotonic()

    # ── Public API ───────────────────────────────────────────

    def search_works(
        self,
        query: str,
        max_results: int,
        filter_params: Optional[Dict[str, Any]] = None,
        select_fields: str = DEFAULT_SCOUT_SELECT,
    ) -> Iterator[Dict[str, Any]]:
        """Cursor-paginated search over OpenAlex Works."""
        self._ensure_pyalex()
        search_filter: Dict[str, Any] = {}
        if filter_params:
            search_filter.update(filter_params)
        cache_key = _cache_key("search", {"query": query, "max": max_results, "filter": search_filter, "select": select_fields})
        cached = self._cache_get_list(cache_key)
        if cached is not None:
            yield from cached[:max_results]
            return

        results: List[Dict[str, Any]] = []
        try:
            works_api = self._pyalex.Works()
            if search_filter:
                works_api = works_api.filter(**search_filter)
            works_api = _apply_select(works_api, select_fields)

            pager = works_api.search(query).paginate(per_page=OPENALEX_LIST_PER_PAGE, n_max=max_results)
            count = 0
            for page in self._iter_pager_pages(pager):
                for work in _iter_page_results(page):
                    if count >= max_results:
                        self._cache_put_list(cache_key, results)
                        return
                    row = dict(work)
                    results.append(row)
                    yield row
                    count += 1
            self._cache_put_list(cache_key, results)
        except Exception as exc:
            self._record_failure(exc)
            raise RuntimeError(f"OpenAlex search_works failed for query='{query}': {exc}") from exc

    def get_work(self, work_id: str, select_fields: str = DEFAULT_SCOUT_SELECT) -> Optional[Dict[str, Any]]:
        """Fetch a single Work by OpenAlex ID."""
        self._ensure_pyalex()

        normalized = _normalize_openalex_id(work_id)
        cache_key = _cache_key("work", {"id": normalized, "select": select_fields})
        cached = self._cache_get(cache_key)
        if isinstance(cached, dict):
            return cached

        try:
            self._wait_for_rate_limit()
            works_api = _apply_select(self._pyalex.Works(), select_fields)
            work = works_api[f"https://openalex.org/{normalized}"]
            self.stats["requests"] += 1
            data = dict(work) if work else None
            if data:
                self._cache_put(cache_key, data)
            return data
        except Exception as exc:
            self._record_failure(exc)
            return None

    def get_works_batch(self, work_ids: List[str], select_fields: str = DEFAULT_SCOUT_SELECT) -> Iterator[Dict[str, Any]]:
        """Batch-fetch works using OR filter (max 50 IDs per request)."""
        self._ensure_pyalex()
        normalized = [_normalize_openalex_id(wid) for wid in work_ids]

        for i in range(0, len(normalized), 50):
            chunk = normalized[i:i + 50]
            try:
                self._wait_for_rate_limit()
                or_filter = "|".join(chunk)
                works_api = _apply_select(self._pyalex.Works(), select_fields)
                pager = works_api.filter(openalex_id=or_filter).paginate(per_page=min(50, OPENALEX_LIST_PER_PAGE), n_max=len(chunk))
                for page in self._iter_pager_pages(pager):
                    for work in _iter_page_results(page):
                        yield dict(work)
            except Exception as exc:
                self._record_failure(exc)
                for wid in chunk:
                    work = self.get_work(wid, select_fields=select_fields)
                    if work:
                        yield work

    def get_citing_works(
        self,
        work_id: str,
        max_results: int,
        select_fields: str = DEFAULT_SCOUT_SELECT,
    ) -> Iterator[Dict[str, Any]]:
        """Fetch works that cite the given work."""
        self._ensure_pyalex()

        normalized = _normalize_openalex_id(work_id)
        cache_key = _cache_key("citing", {"id": normalized, "max": max_results, "select": select_fields})
        cached = self._cache_get_list(cache_key)
        if cached is not None:
            yield from cached[:max_results]
            return

        results: List[Dict[str, Any]] = []
        try:
            self._wait_for_rate_limit()
            works_api = _apply_select(self._pyalex.Works(), select_fields)
            pager = works_api.filter(cites=normalized).paginate(per_page=OPENALEX_LIST_PER_PAGE, n_max=max_results)
            for page in self._iter_pager_pages(pager):
                for work in _iter_page_results(page):
                    row = dict(work)
                    results.append(row)
                    yield row
        except Exception as exc:
            self._record_failure(exc)
            raise RuntimeError(f"OpenAlex get_citing_works failed for work_id='{work_id}': {exc}") from exc
        if results:
            self._cache_put_list(cache_key, results)

    def get_referenced_works(
        self,
        work_id: str,
        max_results: int,
        select_fields: str = DEFAULT_SCOUT_SELECT,
    ) -> Iterator[Dict[str, Any]]:
        """Fetch works referenced by the given work."""
        self._ensure_pyalex()
        work = self.get_work(work_id, select_fields=select_fields)
        if not work:
            return
        ref_ids = work.get("referenced_works", [])[:max_results]
        yield from self.get_works_batch(ref_ids, select_fields=select_fields)

    def search_by_topic(
        self,
        topic_id: str,
        max_results: int,
        select_fields: str = DEFAULT_SCOUT_SELECT,
    ) -> Iterator[Dict[str, Any]]:
        """Search works by OpenAlex Topic ID."""
        self._ensure_pyalex()
        self._wait_for_rate_limit()

        try:
            works_api = _apply_select(self._pyalex.Works(), select_fields)
            pager = works_api.filter(topic_ids=topic_id).paginate(per_page=OPENALEX_LIST_PER_PAGE, n_max=max_results)
            for page in self._iter_pager_pages(pager):
                for work in _iter_page_results(page):
                    yield dict(work)
        except Exception as exc:
            self._record_failure(exc)
            return

    # ── Cache helpers ────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        import hashlib
        h = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self._cache_dir / f"{h}.json"

    def _cache_get(self, key: str) -> Any:
        path = self._cache_path(key)
        if path.exists():
            try:
                self.stats["cache_hits"] += 1
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _cache_put(self, key: str, data: Any) -> None:
        path = self._cache_path(key)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
            self.stats["cache_writes"] += 1
        except Exception:
            pass

    def _cache_get_list(self, key: str) -> Optional[List[Dict[str, Any]]]:
        cached = self._cache_get(key)
        if isinstance(cached, list):
            return cached
        return None

    def _cache_put_list(self, key: str, data: List[Dict[str, Any]]) -> None:
        self._cache_put(key, data)


    def _iter_pager_pages(self, pager: Any) -> Iterator[Any]:
        iterator = iter(pager)
        while True:
            self._wait_for_rate_limit()
            try:
                page = next(iterator)
            except StopIteration:
                return
            self.stats["requests"] += 1
            yield page

    def _record_failure(self, exc: BaseException) -> None:
        self.stats["failures"] += 1
        text = str(exc).lower()
        if "429" in text or "rate limited" in text or "too many requests" in text:
            self.stats["rate_limited"] += 1

    def health_check(self) -> bool:
        """Quick check: is OpenAlex reachable?"""
        try:
            self._ensure_pyalex()
            self._wait_for_rate_limit()
            work = self._pyalex.Works()["https://openalex.org/W2741809807"]
            self.stats["requests"] += 1
            return work is not None
        except Exception as exc:
            self._record_failure(exc)
            return False


def _iter_page_results(page: Any) -> Iterator[Dict[str, Any]]:
    """Yield OpenAlex work dicts from pyalex page variants."""
    if page is None:
        return
    if isinstance(page, dict):
        results = page.get("results")
        if isinstance(results, list):
            for item in results:
                if item:
                    yield dict(item)
        elif page.get("id"):
            yield dict(page)
        return
    try:
        for item in page:
            if item:
                yield dict(item)
    except TypeError:
        return


def _normalize_openalex_id(work_id: str) -> str:
    """Strip URL prefix to get the bare OpenAlex ID."""
    return work_id.rsplit("/", 1)[-1].strip()


def _apply_select(works_api: Any, select_fields: str) -> Any:
    if not select_fields:
        return works_api
    select = getattr(works_api, "select", None)
    if select is None:
        return works_api
    return select(select_fields)


def _cache_key(prefix: str, payload: Dict[str, Any]) -> str:
    return f"{prefix}:{json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)}"
