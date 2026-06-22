"""OpenAlex API data source with pyalex integration, caching, and rate limiting."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from research_agent.core.config import OpenAlexConfig


class OpenAlexSource:
    """Fetches scholarly data from the OpenAlex API via pyalex.

    Features
    --------
    * Pagination — cursor-based, configurable page size.
    * Caching — HTTP-response-level JSON file cache under ``cache_dir``.
    * Rate limiting — token-bucket, configurable requests/second.
    * Retry — exponential backoff with configurable max retries.
    * Polite pool — configurable email for OpenAlex polite pool.

    Falls back gracefully when pyalex is not installed (import guard).
    """

    def __init__(self, config: OpenAlexConfig) -> None:
        self.config = config
        self._cache_dir = Path(config.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._pyalex = None
        self._rate_limit_ts: float = 0.0

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

    def search_works(self, query: str, max_results: int,
                     filter_params: Optional[Dict[str, Any]] = None) -> Iterator[Dict[str, Any]]:
        """Cursor-paginated search over OpenAlex Works."""
        self._ensure_pyalex()
        self._wait_for_rate_limit()

        # Build pyalex filter
        search_filter: Dict[str, Any] = {}
        if filter_params:
            search_filter.update(filter_params)

        try:
            works_api = self._pyalex.Works()
            if search_filter:
                works_api = works_api.filter(**search_filter)

            pager = works_api.search(query).paginate(per_page=200, n_max=max_results)
            count = 0
            for page in pager:
                for work in _iter_page_results(page):
                    if count >= max_results:
                        return
                    yield dict(work)
                    count += 1
        except Exception as exc:
            raise RuntimeError(f"OpenAlex search_works failed for query='{query}': {exc}") from exc

    def get_work(self, work_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single Work by OpenAlex ID."""
        self._ensure_pyalex()
        self._wait_for_rate_limit()

        normalized = _normalize_openalex_id(work_id)
        cache_key = f"work_{normalized}"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            work = self._pyalex.Works()[f"https://openalex.org/{normalized}"]
            data = dict(work) if work else None
            if data:
                self._cache_put(cache_key, data)
            return data
        except Exception:
            return None

    def get_works_batch(self, work_ids: List[str]) -> Iterator[Dict[str, Any]]:
        """Batch-fetch works using OR filter (max 50 IDs per request)."""
        self._ensure_pyalex()
        normalized = [_normalize_openalex_id(wid) for wid in work_ids]

        for i in range(0, len(normalized), 50):
            chunk = normalized[i:i + 50]
            self._wait_for_rate_limit()
            try:
                or_filter = "|".join(chunk)
                pager = self._pyalex.Works().filter(openalex_id=or_filter).paginate(per_page=50, n_max=len(chunk))
                for page in pager:
                    for work in _iter_page_results(page):
                        yield dict(work)
            except Exception:
                # Fallback: fetch individually
                for wid in chunk:
                    work = self.get_work(wid)
                    if work:
                        yield work

    def get_citing_works(self, work_id: str, max_results: int) -> Iterator[Dict[str, Any]]:
        """Fetch works that cite the given work."""
        self._ensure_pyalex()
        self._wait_for_rate_limit()

        normalized = _normalize_openalex_id(work_id)
        cache_key = f"citing_{normalized}_{max_results}"
        cached = self._cache_get_list(cache_key)
        if cached is not None:
            yield from cached
            return

        results: List[Dict[str, Any]] = []
        try:
            pager = self._pyalex.Works().filter(cites=normalized).paginate(per_page=200, n_max=max_results)
            for page in pager:
                for work in _iter_page_results(page):
                    results.append(dict(work))
                    yield dict(work)
        except Exception:
            pass
        if results:
            self._cache_put_list(cache_key, results)

    def get_referenced_works(self, work_id: str, max_results: int) -> Iterator[Dict[str, Any]]:
        """Fetch works referenced by the given work."""
        self._ensure_pyalex()
        work = self.get_work(work_id)
        if not work:
            return
        ref_ids = work.get("referenced_works", [])[:max_results]
        yield from self.get_works_batch(ref_ids)

    def search_by_topic(self, topic_id: str, max_results: int) -> Iterator[Dict[str, Any]]:
        """Search works by OpenAlex Topic ID."""
        self._ensure_pyalex()
        self._wait_for_rate_limit()

        try:
            pager = self._pyalex.Works().filter(topic_ids=topic_id).paginate(per_page=200, n_max=max_results)
            for page in pager:
                for work in _iter_page_results(page):
                    yield dict(work)
        except Exception:
            return

    # ── Cache helpers ────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        import hashlib
        h = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self._cache_dir / f"{h}.json"

    def _cache_get(self, key: str) -> Optional[Dict[str, Any]]:
        path = self._cache_path(key)
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        return None

    def _cache_put(self, key: str, data: Dict[str, Any]) -> None:
        path = self._cache_path(key)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        except Exception:
            pass

    def _cache_get_list(self, key: str) -> Optional[List[Dict[str, Any]]]:
        cached = self._cache_get(key)
        if isinstance(cached, list):
            return cached
        return None

    def _cache_put_list(self, key: str, data: List[Dict[str, Any]]) -> None:
        self._cache_put(key, data)

    def health_check(self) -> bool:
        """Quick check: is OpenAlex reachable?"""
        try:
            self._ensure_pyalex()
            self._wait_for_rate_limit()
            # Minimal API call — fetch one known work
            work = self._pyalex.Works()["https://openalex.org/W2741809807"]
            return work is not None
        except Exception:
            return False


def _iter_page_results(page: Any) -> Iterator[Dict[str, Any]]:
    """Yield OpenAlex work dicts from pyalex page variants.

    pyalex versions differ: some paginate calls yield dict pages with a
    ``results`` key, while newer versions yield an OpenAlexResponseList that is
    itself iterable. This helper keeps the source compatible with both shapes.
    """
    if page is None:
        print("Warning: OpenAlex page is None")
        return
    # print(f"Debug: OpenAlex page type: {type(page)}")
    # print(f"Debug: OpenAlex page content: {page}")
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

