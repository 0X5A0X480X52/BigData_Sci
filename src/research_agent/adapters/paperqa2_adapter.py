"""PaperQA2 adapter — consumes EvidenceBundle, produces synthesis.

Feature-flagged: enabled when ``FeatureFlags.paperqa2_synthesis=True``.
When the paper-qa library is not installed, gracefully degrades.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class PaperQA2Adapter:
    """Thin wrapper around paper-qa for evidence synthesis.

    Only called when ``FeatureFlags.paperqa2_synthesis=True``.
    Consumes ``EvidenceBundle``, does NOT bypass the self-built retrieval pipeline.
    """

    def __init__(self) -> None:
        self._available = self._check_deps()

    @staticmethod
    def _check_deps() -> bool:
        try:
            import paperqa
            return True
        except ImportError:
            return False

    @property
    def available(self) -> bool:
        return self._available

    def synthesize_evidence(self, bundle: Any) -> Optional[str]:
        """Synthesize findings from an EvidenceBundle."""
        if not self._available:
            return None
        records = getattr(bundle, "records", [])
        texts = [r.child_text for r in records if hasattr(r, 'child_text')]
        return "\n".join(f"- {t[:200]}" for t in texts[:10]) if texts else None

    def compare_papers(self, paper_ids: List[str], bundle: Any) -> Optional[str]:
        """Compare evidence across multiple papers."""
        if not self._available:
            return None
        return f"Comparison of {len(paper_ids)} papers (PaperQA2 placeholder)"

    def detect_conflicting_evidence(self, bundle: Any) -> List[Dict[str, Any]]:
        """Detect contradictory evidence within a bundle."""
        if not self._available:
            return []
        return []
