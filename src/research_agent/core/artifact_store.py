"""Local filesystem artifact store for auditable agent runs."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import ArtifactRef, to_dict
from .utils import stable_hash, utc_now_iso


class ArtifactStore:
    """Persist large tool outputs outside the prompt context."""

    def __init__(self, root: str | Path = "artifacts", run_id: str = "default") -> None:
        self.root = Path(root)
        self.run_id = run_id
        self.run_root = self.root / run_id
        self.refs: list[ArtifactRef] = []
        self.run_root.mkdir(parents=True, exist_ok=True)

    def path_for(self, category: str, filename: str) -> Path:
        path = self.run_root / category / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _ref(self, path: Path, result_type: str, media_type: str, summary: Mapping[str, Any] | None = None) -> ArtifactRef:
        rel = str(path.as_posix())
        artifact_id = f"A_{stable_hash({'path': rel, 'created_at': utc_now_iso()}, 12)}"
        ref = ArtifactRef(
            artifact_id=artifact_id,
            path=rel,
            result_type=result_type,
            media_type=media_type,
            summary=dict(summary or {}),
        )
        self.refs.append(ref)
        return ref

    def write_json(self, category: str, filename: str, data: Any, result_type: str, summary: Mapping[str, Any] | None = None) -> ArtifactRef:
        path = self.path_for(category, filename)
        with path.open("w", encoding="utf-8") as f:
            json.dump(to_dict(data), f, ensure_ascii=False, indent=2)
        return self._ref(path, result_type, "application/json", summary)

    def write_jsonl(self, category: str, filename: str, rows: Iterable[Any], result_type: str, summary: Mapping[str, Any] | None = None) -> ArtifactRef:
        path = self.path_for(category, filename)
        count = 0
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(to_dict(row), ensure_ascii=False) + "\n")
                count += 1
        merged = {"rows": count}
        merged.update(dict(summary or {}))
        return self._ref(path, result_type, "application/x-jsonlines", merged)

    def write_csv(self, category: str, filename: str, rows: Sequence[Mapping[str, Any]], result_type: str, summary: Mapping[str, Any] | None = None) -> ArtifactRef:
        path = self.path_for(category, filename)
        fieldnames = sorted({key for row in rows for key in row.keys()}) if rows else []
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        merged = {"rows": len(rows)}
        merged.update(dict(summary or {}))
        return self._ref(path, result_type, "text/csv", merged)

    def write_text(self, category: str, filename: str, text: str, result_type: str, summary: Mapping[str, Any] | None = None) -> ArtifactRef:
        path = self.path_for(category, filename)
        path.write_text(text, encoding="utf-8")
        return self._ref(path, result_type, "text/plain", summary)
