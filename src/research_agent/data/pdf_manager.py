"""PDF discovery, download, SHA-256 dedup, and local storage.

Supports Open Access URL → download → SHA-256 hash → LocalObjectStorage.
When download fails, gracefully returns None (caller falls back to abstract).
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from research_agent.core.models import Paper


@dataclass
class PDFAsset:
    work_id: str
    storage_key: str          # relative path under storage root
    sha256: str
    file_size_bytes: int
    downloaded_at: str


class LocalObjectStorage:
    """Minimal local-filesystem object storage (S3-compatible interface stub)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, key: str) -> Optional[bytes]:
        path = self.root / key
        if path.exists():
            return path.read_bytes()
        return None

    def exists(self, key: str) -> bool:
        return (self.root / key).exists()

    def delete(self, key: str) -> bool:
        path = self.root / key
        if path.exists():
            path.unlink()
            return True
        return False


class PDFManager:
    """Downloads and stores PDFs with SHA-256 deduplication."""

    def __init__(self, storage_root: str | Path, repository: Any = None,
                 http_timeout: int = 30) -> None:
        self.storage = LocalObjectStorage(storage_root)
        self._repo = repository
        self._timeout = http_timeout
        self._downloaded: Dict[str, str] = {}  # work_id → sha256

    # ── Public API ───────────────────────────────────────────

    def discover_pdf_url(self, paper: Paper) -> Optional[str]:
        """Try multiple sources for a PDF URL."""
        # 1. Open Access URL from OpenAlex
        if paper.open_access_pdf_url:
            return paper.open_access_pdf_url
        # 2. DOI-based lookup
        if paper.doi:
            return f"https://doi.org/{paper.doi}"
        return None

    def download(self, paper: Paper) -> Optional[PDFAsset]:
        """Download a paper's PDF, dedup by SHA-256, store locally."""
        url = self.discover_pdf_url(paper)
        if not url:
            return None

        # Check cache
        if self._repo:
            existing = self._repo.get_paper_file(paper.work_id)
            if existing:
                return PDFAsset(
                    work_id=paper.work_id,
                    storage_key=existing.get("storage_key", ""),
                    sha256=existing.get("pdf_sha256", ""),
                    file_size_bytes=existing.get("file_size_bytes", 0),
                    downloaded_at=existing.get("downloaded_at", ""),
                )

        try:
            data = self._fetch_url(url)
        except Exception:
            return None

        sha256 = hashlib.sha256(data).hexdigest()
        storage_key = f"pdfs/{sha256[:2]}/{sha256}.pdf"

        # Check SHA-256 dedup across works
        if self.storage.exists(storage_key):
            return self._record(paper.work_id, storage_key, sha256, len(data))

        # Store
        self.storage.put(storage_key, data)
        return self._record(paper.work_id, storage_key, sha256, len(data))

    def is_downloaded(self, work_id: str) -> bool:
        if work_id in self._downloaded:
            return True
        if self._repo:
            existing = self._repo.get_paper_file(work_id)
            return existing is not None
        return False

    def get_pdf_path(self, work_id: str) -> Optional[Path]:
        """Return the local filesystem path to a downloaded PDF."""
        if self._repo:
            record = self._repo.get_paper_file(work_id)
            if record:
                return self.storage.root / record.get("storage_key", "")
        return None

    def health_check(self) -> bool:
        return self.storage.root.exists()

    # ── Internal ─────────────────────────────────────────────

    def _fetch_url(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "ResearchAgent/0.2"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return resp.read()

    def _record(self, work_id: str, storage_key: str, sha256: str,
                file_size: int) -> PDFAsset:
        from research_agent.core.utils import utc_now_iso
        asset = PDFAsset(
            work_id=work_id, storage_key=storage_key, sha256=sha256,
            file_size_bytes=file_size, downloaded_at=utc_now_iso(),
        )
        self._downloaded[work_id] = sha256
        if self._repo:
            try:
                self._repo.save_paper_file(work_id, sha256, storage_key, file_size)
            except Exception:
                pass
        return asset
