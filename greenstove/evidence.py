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

    def run_dir(self, run_id: str) -> Path:
        d = self._runs / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, run_id: str, name: str, obj) -> Path:
        """runs/<run_id>/<name>.json: requests, tariff_N, reservations_N, verdict_N_<phase>, status."""
        path = self.run_dir(run_id) / f"{name}.json"
        data = obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj
        if isinstance(data, list):
            data = [x.model_dump(mode="json") if hasattr(x, "model_dump") else x for x in data]
        path.write_text(json.dumps(data, indent=1, default=str))
        return path

    def save_run(self, run: RunRecord) -> Path:
        self.write(run.agent_run_id, "status", {"status": run.status, "reason": run.reason,
                                                 "selected_proposal_id": run.selected_proposal_id,
                                                 "selected_tariff": run.selected_tariff,
                                                 "execution": run.execution.model_dump() if run.execution else None})
        path = self.run_dir(run.agent_run_id) / "run.json"
        path.write_text(run.model_dump_json(indent=2))
        return path

    def runs(self) -> list[RunRecord]:
        return [RunRecord.model_validate_json(p.read_text()) for p in sorted(self._runs.glob("*/run.json"))]
