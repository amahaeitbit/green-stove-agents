"""Shared Brain insight: is the memory current, lean and conflict-free, and is it used?

`stats` answers the Shared Brain questions with numbers from the store, the audit log and
the run records; `explain` shows the full provenance of one entry, from evidence to the
fresh agents that later read it.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from greenstove.agent_models import RunRecord
from greenstove.brain.store import BrainStore
from greenstove.evidence import EvidenceStore


def stats(brain: BrainStore, evidence: EvidenceStore) -> dict[str, Any]:
    entries = brain.entries()
    status = Counter(e.status for e in entries)
    current = [e for e in entries if e.status == "current"]
    audit = brain.audit_log()
    admissions = [a for a in audit if a.get("event") == "admission"]
    actions = Counter(a.get("action") for a in admissions)
    rejected = Counter((a.get("code") or (a.get("reasons") or ["?"])[0]).split(":")[0]
                       for a in admissions if a.get("action") == "rejected")
    runs = evidence.runs()
    with_brief = [r for r in runs if r.brief.mode == "curated" and r.brief.entry_ids]
    proposals = [a.proposal for r in with_brief for a in r.attempts if a.proposal]
    cited = Counter(i for p in proposals for i in p.cited_observation_ids)
    return {
        "entries": {"current": len(current), **{k: v for k, v in status.items() if k != "current"},
                    "cap": brain.cap},
        "current_outcomes": {"pass": sum(e.outcome_passed for e in current),
                             "fail": sum(not e.outcome_passed for e in current)},
        "conflict_groups": len({e.conflict_group for e in current if e.conflict_group}),
        "confirmations": sum(e.confirmations for e in current),
        "admissions": dict(actions),
        "rejections_by_code": dict(rejected),
        "evictions": sum(1 for a in audit if a.get("event") == "evicted_for_cap"),
        "expired": sum(1 for a in audit if a.get("event") == "expired"),
        "runs_with_curated_brief": len(with_brief),
        "mean_brief_tokens": round(sum(r.brief.tokens for r in with_brief) / len(with_brief), 1) if with_brief else 0,
        "mean_entries_supplied": round(sum(len(r.brief.entry_ids) for r in with_brief) / len(with_brief), 2) if with_brief else 0,
        "proposals_citing_memory": f"{sum(bool(p.cited_observation_ids) for p in proposals)}/{len(proposals)}",
        "most_cited": cited.most_common(5),
        "snapshot": brain.snapshot_hash()[:8],
    }


def explain(obs_id: str, brain: BrainStore, evidence: EvidenceStore) -> dict[str, Any] | None:
    e = brain.get(obs_id)
    if e is None:
        return None
    records = [evidence.get(i) for i in e.evidence_ids]
    source_runs = sorted({r.agent_run_id for r in records if r})
    audit = [a for a in brain.audit_log() if a.get("observation_id") == obs_id or a.get("id") == obs_id]
    later: list[dict[str, Any]] = []
    for run in evidence.runs():
        if obs_id in run.brief.entry_ids:
            cited = any(obs_id in a.proposal.cited_observation_ids for a in run.attempts if a.proposal)
            later.append({"run": run.agent_run_id, "scenario": run.scenario_id, "mode": run.memory_mode,
                          "cited": cited, "status": run.status, "attempts": len(run.attempts)})
    return {
        "entry": e.model_dump(mode="json"),
        "evidence": [{"id": r.run_id, "phase": r.phase, "passed": r.passed, "codes": r.feedback_codes,
                      "served": r.metrics.get("served_fraction"), "min_battery_kwh": r.metrics.get("min_battery_kwh"),
                      "scenario": r.scenario_id, "day": r.day} if r else {"id": i, "missing": True}
                     for i, r in zip(e.evidence_ids, records)],
        "source_runs": [{"run": rid, "decision": str(evidence.run_dir(rid) / "decision.md")
                         if (evidence.run_dir(rid) / "decision.md").exists() else None} for rid in source_runs],
        "keeper_events": [{k: a.get(k) for k in ("seq", "event", "action", "code", "reasons", "clock") if k in a}
                          for a in audit],
        "read_by_later_agents": later,
    }


def run_summary(run: RunRecord) -> str:
    return f"{run.agent_run_id} {run.memory_mode} {run.status} attempts={len(run.attempts)}"
