"""Shared Brain store: capped approved observations plus an append-only audit log.

Only the Keeper writes here. A frozen snapshot (evaluation) refuses every write.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Iterable

from greenstove.agent_models import ApprovedObservation, stable_hash

BRAIN_FILE = "brain.json"
AUDIT_FILE = "audit.jsonl"
_OBS_ID = re.compile(r"^obs-(\d+)$")


class FrozenStoreError(RuntimeError):
    """Raised on any write to a frozen (evaluation) brain snapshot."""


def is_current(obs: ApprovedObservation, config_hash: str, clock: int) -> bool:
    """Servable: status current, same configuration, and clock inside [created_at, expires_at)."""
    return (obs.status == "current" and obs.config_hash == config_hash
            and obs.created_at <= clock < obs.expires_at)


def snapshot_hash_of(entries: Iterable[ApprovedObservation]) -> str:
    """Stable hash over current-status entries, independent of input order."""
    cur = sorted((e for e in entries if e.status == "current"), key=lambda e: e.id)
    return stable_hash([e.model_dump(mode="json") for e in cur])


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


class BrainStore:
    def __init__(self, path: Path, cap: int = 20, *, frozen: bool = False):
        self.root = Path(path)
        self.cap = cap
        self.frozen = frozen
        if not frozen:
            self.root.mkdir(parents=True, exist_ok=True)
        self._brain = self.root / BRAIN_FILE
        self._audit = self.root / AUDIT_FILE
        self._entries: dict[str, ApprovedObservation] = {}
        self._recorded_hash: str | None = None
        if self._brain.exists():
            data = json.loads(self._brain.read_text())
            self.frozen = self.frozen or bool(data.get("frozen", False))
            self.cap = int(data.get("cap", cap))
            self._recorded_hash = data.get("snapshot_hash")
            for raw in data.get("entries", []):
                obs = ApprovedObservation.model_validate(raw)
                self._entries[obs.id] = obs
        self._seq = len(self.audit_log())

    # ---- loading / freezing ------------------------------------------------------

    @classmethod
    def load_frozen(cls, path: Path) -> "BrainStore":
        """Open a snapshot read-only; refuse it if its recorded hash does not match."""
        path = Path(path)
        if not (path / BRAIN_FILE).exists():
            raise FileNotFoundError(path / BRAIN_FILE)
        store = cls(path, frozen=True)
        if store._recorded_hash and store._recorded_hash != store.snapshot_hash():
            raise ValueError(f"snapshot hash mismatch in {path}")
        return store

    def freeze(self, dest: Path) -> str:
        """Write a frozen copy (brain + audit) to dest and return its snapshot hash."""
        dest = Path(dest)
        dest.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest / BRAIN_FILE, self._dump(frozen=True))
        audit = self._audit.read_text() if self._audit.exists() else ""
        _atomic_write(dest / AUDIT_FILE, audit)
        return self.snapshot_hash()

    # ---- reads -------------------------------------------------------------------

    def entries(self) -> list[ApprovedObservation]:
        return [self._entries[k] for k in sorted(self._entries)]

    def get(self, obs_id: str) -> ApprovedObservation | None:
        return self._entries.get(obs_id)

    def current(self, config_hash: str, clock: int) -> list[ApprovedObservation]:
        return [e for e in self.entries() if is_current(e, config_hash, clock)]

    def snapshot_hash(self) -> str:
        return snapshot_hash_of(self._entries.values())

    def next_id(self) -> str:
        nums = [int(m.group(1)) for k in self._entries if (m := _OBS_ID.match(k))]
        return f"obs-{max(nums, default=0) + 1:03d}"

    def audit_log(self) -> list[dict]:
        if not self._audit.exists():
            return []
        return [json.loads(line) for line in self._audit.read_text().splitlines() if line.strip()]

    # ---- writes ------------------------------------------------------------------

    def _check_writable(self) -> None:
        if self.frozen:
            raise FrozenStoreError(f"brain store at {self.root} is frozen")

    def put(self, obs: ApprovedObservation) -> None:
        """Insert or replace by id (re-validated, persisted immediately)."""
        self._check_writable()
        obs = ApprovedObservation.model_validate(obs.model_dump())
        self._entries[obs.id] = obs
        _atomic_write(self._brain, self._dump(frozen=False))

    def audit(self, event: dict) -> int:
        """Append one event with a sequence number; returns that number."""
        self._check_writable()
        self._seq += 1
        row = {"seq": self._seq, **event}
        with self._audit.open("a") as f:
            f.write(json.dumps(row, sort_keys=True, default=str) + "\n")
        return self._seq

    def expire(self, clock: int) -> list[str]:
        """Mark current entries with expires_at <= clock as expired (audited)."""
        self._check_writable()
        done = []
        for e in self.entries():
            if e.status == "current" and e.expires_at <= clock:
                self.put(e.model_copy(update={"status": "expired"}))
                self.audit({"event": "expired", "id": e.id, "clock": clock,
                            "expires_at": e.expires_at})
                done.append(e.id)
        return done

    def _dump(self, frozen: bool) -> str:
        data = {"frozen": frozen, "cap": self.cap, "snapshot_hash": self.snapshot_hash(),
                "entries": [e.model_dump(mode="json") for e in self.entries()]}
        return json.dumps(data, indent=1, sort_keys=True)
