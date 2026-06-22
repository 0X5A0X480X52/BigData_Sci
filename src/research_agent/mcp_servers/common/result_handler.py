"""Artifact-isation of large MCP results.

When a tool result exceeds a size threshold it is written to the ArtifactStore
and the caller receives an ArtifactRef instead of the raw payload.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from research_agent.core.artifact_store import ArtifactStore
from research_agent.core.models import ArtifactRef

# ── Thresholds ──
ARTIFACT_SIZE_THRESHOLD_BYTES = 1_000_000   # 1 MB
ARTIFACT_ITEM_COUNT_THRESHOLD = 500          # >500 items → artifactise


class ArtifactResultHandler:
    """Handles large-result detection and artifactisation."""

    def __init__(self, artifact_store: ArtifactStore, size_threshold: int = ARTIFACT_SIZE_THRESHOLD_BYTES) -> None:
        self._store = artifact_store
        self._size_threshold = size_threshold

    def maybe_artifactize(
        self, result: Any, category: str, filename: str, result_type: str, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Inspect *result* and either return it inline or write to the artifact store.

        Returns a dict with keys ``"inline"`` (bool), ``"data"`` (the inline value or
        the ArtifactRef), and ``"artifact_id"`` (str | None).
        """
        size = _estimate_size_bytes(result)
        if size < self._size_threshold:
            return {"inline": True, "data": result, "artifact_id": None}

        ref = self._store.write_json(category, filename, result, result_type, {"context": context})
        return {"inline": False, "data": ref, "artifact_id": ref.artifact_id}


def maybe_artifactize(
    result: Any, artifact_store: ArtifactStore, category: str, filename: str,
    result_type: str, context: Dict[str, Any],
) -> Dict[str, Any]:
    """Convenience wrapper that creates an ArtifactResultHandler and calls it once."""
    handler = ArtifactResultHandler(artifact_store)
    return handler.maybe_artifactize(result, category, filename, result_type, context)


# ── internal helpers ──

def _estimate_size_bytes(value: Any) -> int:
    """Approximate serialised size of *value*."""
    try:
        if isinstance(value, (str, bytes)):
            return len(value)
        if isinstance(value, list) and len(value) > ARTIFACT_ITEM_COUNT_THRESHOLD:
            return ARTIFACT_SIZE_THRESHOLD_BYTES + 1  # force artifact
        return len(json.dumps(value, default=str, ensure_ascii=False))
    except Exception:
        return 0
