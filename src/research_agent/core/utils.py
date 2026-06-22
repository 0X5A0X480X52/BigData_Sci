"""Small deterministic helpers used across the MVP."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value: Any, length: int = 16) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def content_hash(text: str, length: int = 32) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def normalize_openalex_id(value: str) -> str:
    if not value:
        return ""
    value = str(value).strip()
    if "/" in value:
        value = value.rstrip("/").split("/")[-1]
    return value


def abstract_from_inverted_index(index: Mapping[str, Iterable[int]] | None) -> str:
    if not index:
        return ""
    positions: Dict[int, str] = {}
    for token, offsets in index.items():
        for offset in offsets:
            positions[int(offset)] = str(token)
    return " ".join(positions[i] for i in sorted(positions))


def simple_tokenize(text: str) -> List[str]:
    return re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower())


def slugify(value: str, max_len: int = 80) -> str:
    value = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "_", value.strip())
    value = value.strip("_") or "item"
    return value[:max_len]
