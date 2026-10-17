"""Append-only evidence store. The application writes it; models only cite its ids."""
from __future__ import annotations

import json
from pathlib import Path

from greenstove.agent_models import EvidenceRecord, RunRecord


class EvidenceStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._file = self.root / "evidence.jsonl"
        self._runs = self.root / "runs"
        self._runs.mkdir(exist_ok=True)
        self._cache: dict[str, EvidenceRecord] | None = None

    def _load(self) -> dict[str, EvidenceRecord]:
        if self._cache is None:
            self._cache = {}
            if self._file.exists():
                for line in self._file.read_text().splitlines():
                    if line.strip():
                        rec = EvidenceRecord.model_validate_json(line)
                        self._cache[rec.run_id] = rec
        return self._cache

    def put(self, rec: EvidenceRecord) -> None:
        recs = self._load()
        if rec.run_id in recs:
            raise ValueError(f"evidence {rec.run_id} already exists (append-only)")
        with self._file.open("a") as f:
            f.write(rec.model_dump_json() + "\n")
        recs[rec.run_id] = rec

    def get(self, run_id: str) -> EvidenceRecord | None:
        return self._load().get(run_id)

    def all(self) -> list[EvidenceRecord]:
        return list(self._load().values())

    def save_run(self, run: RunRecord) -> Path:
        path = self._runs / f"{run.agent_run_id}.json"
        path.write_text(run.model_dump_json(indent=2))
        return path

    def runs(self) -> list[RunRecord]:
        return [RunRecord.model_validate_json(p.read_text()) for p in sorted(self._runs.glob("*.json"))]
