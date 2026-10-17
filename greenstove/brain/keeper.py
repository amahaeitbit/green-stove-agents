"""Code Keeper: admits Note-writer proposals only as facts reconstructed from evidence.

The model supplies evidence ids, tags and prose. Every factual field (situation, tariff,
phase, outcome, codes, metrics) is rebuilt here from the cited EvidenceRecords.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from greenstove.agent_models import (AdmissionResult, ApprovedObservation, EvidenceRecord,
                                     ObservationProposal)
from greenstove.brain.store import BrainStore, FrozenStoreError
from greenstove.evidence import EvidenceStore

# Tag thresholds on reconstructed situation descriptors.
LOW_BATTERY, HIGH_BATTERY = 0.35, 0.7
HIGH_DEMAND_RATIO = 0.6              # demand_kwh > ratio * forecast_pv_kwh => high_demand
FLEXIBLE_HOURS, RIGID_HOURS = 3.0, 1.5
# Same situation bucket: equal weather, battery_frac within 0.1, demand within 10%.
BUCKET_BATTERY, BUCKET_DEMAND = 0.1, 0.10
# A number in prose is supported if within 1% or 0.01 absolute of a reconstructed value.
REL_TOL, ABS_TOL = 0.01, 0.01

CONSTRAINT_PATTERNS = [re.compile(p) for p in (
    r"\brelax", r"\b(lower|raise|reduce|increase|drop|change) (the )?(battery )?reserve",
    r"\b(change|move|shift|extend) (the )?(tariff )?windows?\b",
    r"\b(increase|raise|exceed) (the )?inverter", r"\bignor", r"\boverrid", r"\bbypass",
    r"\bwaive", r"\bbound", r"\blattice", r"\brequirement")]
PASS_WORDS = re.compile(r"\b(pass|passed|passes|passing|succeeded|success|successful|verified)\b")
FAIL_WORDS = re.compile(r"\b(fail|failed|fails|failing|failure)\b")
ACTUAL_WORDS = re.compile(r"\b(actual|executed|execution)\b")
_ID_TOKEN = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_][A-Za-z0-9.]+)+")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(\d+(?:\.\d+)?)(%?)")


# Stage order from the team diagram; a rejection reports the first failing stage only.
STAGE_OF = {"unknown_evidence": "evidence_unknown", "expired_evidence": "evidence_expired",
            "future_evidence": "evidence_expired", "config_mismatch": "evidence_expired",
            "claimed_tariff": "result_mismatch", "claimed_phase": "result_mismatch",
            "claimed_result": "result_mismatch", "unsupported_number": "result_mismatch",
            "outcome_contradiction": "result_mismatch", "phase_contradiction": "result_mismatch",
            "incomplete_forecast_evidence": "result_mismatch", "mixed_evidence": "scope",
            "tag_out_of_scope": "scope", "constraint_edit": "constraint_edit"}
ORDER = ("evidence_unknown", "evidence_expired", "result_mismatch", "scope", "constraint_edit")


def first_stage(reasons: list[str]) -> tuple[str, list[str]]:
    """(code, details) of the earliest failing stage."""
    staged = [(ORDER.index(STAGE_OF.get(r.split(":")[0], "scope")), r) for r in reasons]
    first = min(i for i, _ in staged)
    return ORDER[first], [r for i, r in staged if i == first]


@dataclass(frozen=True)
class KeeperConfig:
    config_hash: str
    clock: int                       # simulated day of admission
    ttl_days: int = 30
    evidence_max_age_days: int = 30
    cap: int = 20


def _plain(text: str) -> str:
    return re.sub(r"[\s_\-]+", " ", text.lower()).strip()


def _num(situation: dict, key: str) -> float | None:
    v = situation.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def reconstruct(records: list[EvidenceRecord]) -> dict:
    """Factual fields of an observation, derived only from evidence records."""
    actual = [r for r in records if r.phase == "actual"]
    failing = [r for r in records if not r.passed]
    expected = [r for r in records if r.phase == "forecast_expected"]
    decisive = (actual or failing or expected or records)[0]
    return {"situation": dict(decisive.situation), "tariff": dict(decisive.tariff),
            "phase": "actual" if actual else "forecast",
            "outcome_passed": all(r.passed for r in records),
            "feedback_codes": sorted({c for r in records for c in r.feedback_codes}),
            "metrics": dict(decisive.metrics)}


def allowed_tags(facts: dict) -> set[str]:
    """Tag vocabulary that is true of the reconstructed situation and outcome."""
    sit = facts["situation"]
    tags = {facts["phase"], "pass" if facts["outcome_passed"] else "fail"}
    tags |= {c.lower() for c in facts["feedback_codes"]}
    if isinstance(sit.get("weather_class"), str):
        tags.add(sit["weather_class"].lower())
    batt, demand, pv, flex = (_num(sit, k) for k in
                              ("battery_frac", "demand_kwh", "forecast_pv_kwh", "flex_hours"))
    if batt is not None and batt < LOW_BATTERY:
        tags.add("low_battery")
    if batt is not None and batt > HIGH_BATTERY:
        tags.add("high_battery")
    if demand is not None and pv is not None:
        tags.add("high_demand" if demand > HIGH_DEMAND_RATIO * pv else "low_demand")
    if flex is not None and flex >= FLEXIBLE_HOURS:
        tags.add("flexible")
    if flex is not None and flex < RIGID_HOURS:
        tags.add("rigid")
    return tags


def same_bucket(a: dict, b: dict) -> bool:
    ba, bb, da, db = _num(a, "battery_frac"), _num(b, "battery_frac"), _num(a, "demand_kwh"), _num(b, "demand_kwh")
    if a.get("weather_class") != b.get("weather_class") or None in (ba, bb, da, db):
        return False
    return abs(ba - bb) <= BUCKET_BATTERY + 1e-9 and abs(da - db) <= BUCKET_DEMAND * max(abs(da), abs(db)) + 1e-9


def _evidence_problems(records: list[EvidenceRecord], config: KeeperConfig) -> list[str]:
    first, reasons = records[0], []
    if any(r.config_hash != config.config_hash for r in records):
        reasons.append("config_mismatch")
    key = lambda r: (r.agent_run_id, r.proposal_id, r.scenario_id, r.tariff, r.situation)
    if any(key(r) != key(first) for r in records):
        reasons.append("mixed_evidence")
    if any(r.day < config.clock - config.evidence_max_age_days for r in records):
        reasons.append("expired_evidence")
    if any(r.day > config.clock for r in records):
        reasons.append("future_evidence")
    phases = {r.phase for r in records}
    if all(r.passed for r in records) and "actual" not in phases and \
            not {"forecast_expected", "forecast_downside"} <= phases:
        reasons.append("incomplete_forecast_evidence")   # a forecast pass needs both scenarios
    return reasons


def _numbers(text: str, known_ids: set[str]) -> list[tuple[str, bool]]:
    """Numbers stated in prose, ignoring ids (cited ids and id-like tokens such as obs-003)."""
    for ident in sorted(known_ids, key=len, reverse=True):
        text = text.replace(ident, " ")
    text = _ID_TOKEN.sub(" ", text)
    return [(m.group(1), m.group(2) == "%") for m in _NUMBER.finditer(text)]


def _supported(raw: str, percent: bool, values: list[float]) -> bool:
    n = float(raw)
    cands = [n, n / 100] if percent else [n]
    return any(abs(c - abs(v)) <= max(ABS_TOL, REL_TOL * abs(v)) for c in cands for v in values)


def _scope_problems(proposal: ObservationProposal, facts: dict, known_ids: set[str],
                    tags: list[str]) -> list[str]:
    reasons = []
    if proposal.claimed_tariff is not None and proposal.claimed_tariff != facts["tariff"]:
        reasons.append("claimed_tariff")
    if proposal.claimed_phase is not None and proposal.claimed_phase != facts["phase"]:
        reasons.append("claimed_phase")
    if proposal.claimed_passed is not None and proposal.claimed_passed != facts["outcome_passed"]:
        reasons.append("claimed_result")
    values = list(facts["metrics"].values()) + list(facts["tariff"].values())
    values += [v for k in facts["situation"] if (v := _num(facts["situation"], k)) is not None]
    for raw, pct in _numbers(proposal.description, known_ids):
        if not _supported(raw, pct, values):
            reasons.append(f"unsupported_number:{raw}{'%' if pct else ''}")
    text = _plain(proposal.description)
    words = PASS_WORDS if not facts["outcome_passed"] else FAIL_WORDS
    if words.search(text):
        reasons.append("outcome_contradiction")
    if facts["phase"] == "forecast" and ACTUAL_WORDS.search(text):
        reasons.append("phase_contradiction")
    scope_text = " ".join([text] + [_plain(t) for t in tags])
    if any(p.search(scope_text) for p in CONSTRAINT_PATTERNS):
        reasons.append("constraint_edit")
    allowed = allowed_tags(facts)
    reasons += [f"tag_out_of_scope:{t}" for t in tags if t not in allowed]
    return reasons


def _make_room(store: BrainStore, config: KeeperConfig, keep: set[str]) -> list[str]:
    """Expire stale entries, then evict the soonest-expiring current entry while full."""
    cap = min(config.cap, store.cap)
    live = lambda: [e for e in store.entries() if e.status == "current"]
    if len(live()) < cap:
        return []
    store.expire(config.clock)
    notes, cur = [], live()
    while cur and len(cur) >= cap:
        pool = [e for e in cur if e.id not in keep] or cur
        victim = min(pool, key=lambda e: (e.expires_at, e.id))
        store.put(victim.model_copy(update={"status": "superseded"}))
        store.audit({"event": "evicted_for_cap", "id": victim.id, "clock": config.clock, "cap": cap})
        notes.append(f"evicted_for_cap:{victim.id}")
        cur = live()
    return notes


def _confirm(entry: ApprovedObservation, ids: list[str], run_id: str, facts: dict,
             evidence: EvidenceStore, config: KeeperConfig, store: BrainStore) -> AdmissionResult:
    new_ids = [i for i in ids if i not in entry.evidence_ids]
    if not new_ids:
        return AdmissionResult(action="rejected", reasons=[f"duplicate_evidence:{entry.id}"])
    seen_runs = {r.agent_run_id for r in map(evidence.get, entry.evidence_ids) if r is not None}
    independent = run_id not in seen_runs        # same run again is not a new confirmation
    update = {"evidence_ids": entry.evidence_ids + new_ids, "version": entry.version + 1,
              "feedback_codes": sorted(set(entry.feedback_codes) | set(facts["feedback_codes"]))}
    if independent:
        update["confirmations"] = entry.confirmations + 1
        update["expires_at"] = max(entry.expires_at, config.clock + config.ttl_days)
    store.put(entry.model_copy(update=update))
    return AdmissionResult(action="confirmed", observation_id=entry.id,
                           reasons=[] if independent else ["same_run_evidence"])


def _hint_notes(proposal: ObservationProposal, action: str, matched: str | None) -> list[str]:
    """Model duplicate/conflict hints are advisory; record where the Keeper disagreed."""
    notes = []
    if proposal.duplicate_of and not (action == "confirmed" and proposal.duplicate_of == matched):
        notes.append(f"hint_ignored:duplicate_of={proposal.duplicate_of}")
    notes += [f"hint_ignored:conflicts_with={c}" for c in proposal.conflicts_with
              if not (action == "kept_separate" and c == matched)]
    return notes


def _place(proposal, facts, tags, ids, records, evidence, config, store) -> AdmissionResult:
    same = [e for e in store.current(config.config_hash, config.clock)
            if e.tariff == facts["tariff"] and e.phase == facts["phase"]
            and same_bucket(e.situation, facts["situation"])]
    dup = next((e for e in same if e.outcome_passed == facts["outcome_passed"]), None)
    if dup is not None:
        result = _confirm(dup, ids, records[0].agent_run_id, facts, evidence, config, store)
        matched = dup.id
    else:
        rival = next((e for e in same if e.outcome_passed != facts["outcome_passed"]), None)
        group = (rival.conflict_group or f"cg-{rival.id}") if rival else None
        notes = _make_room(store, config, keep={rival.id} if rival else set())
        if rival is not None and rival.conflict_group != group:   # recency is not proof: keep both
            store.put(rival.model_copy(update={"conflict_group": group, "version": rival.version + 1}))
        obs = ApprovedObservation(
            id=store.next_id(), config_hash=config.config_hash, evidence_ids=ids, tags=tags,
            description=proposal.description.strip(), created_at=config.clock,
            expires_at=config.clock + config.ttl_days, conflict_group=group, **facts)
        store.put(obs)
        result = AdmissionResult(action="kept_separate" if rival else "added",
                                 observation_id=obs.id, reasons=notes)
        matched = rival.id if rival else None
    result.reasons += _hint_notes(proposal, result.action, matched)
    return result


def admit(proposal: ObservationProposal, evidence: EvidenceStore, config: KeeperConfig,
          store: BrainStore) -> AdmissionResult:
    """Admit one proposal; every outcome (including rejection) is written to the audit log."""
    if store.frozen:
        raise FrozenStoreError("frozen brain: quarantine evaluation-time proposals instead")
    ids = list(dict.fromkeys(proposal.evidence_ids))
    records = [r for i in ids if (r := evidence.get(i)) is not None]
    reasons = [f"unknown_evidence:{i}" for i in ids if evidence.get(i) is None]
    if not reasons:
        reasons = _evidence_problems(records, config)
    tags = sorted({_plain(t).replace(" ", "_") for t in proposal.situation_tags} - {""})
    if not reasons:
        facts = reconstruct(records)
        known = set(ids) | set(proposal.conflicts_with) | {proposal.duplicate_of or ""}
        known |= {records[0].agent_run_id, records[0].proposal_id, records[0].scenario_id}
        reasons = _scope_problems(proposal, facts, known - {""}, tags)
    if reasons:
        code, details = first_stage(reasons)
        result = AdmissionResult(action="rejected", code=code, reasons=[code] + details)
    else:
        result = _place(proposal, facts, tags, ids, records, evidence, config, store)
        result.code = {"confirmed": "duplicate", "kept_separate": "conflict"}.get(result.action)
        if result.action == "rejected":                       # re-cited evidence already held
            result.code = "duplicate"
    store.audit({"event": "admission", "action": result.action, "code": result.code,
                 "observation_id": result.observation_id,
                 "reasons": result.reasons, "clock": config.clock, "config_hash": config.config_hash,
                 "proposal": proposal.model_dump(mode="json")})
    return result


def admit_all(proposals: list[ObservationProposal], evidence: EvidenceStore, config: KeeperConfig,
              store: BrainStore) -> list[AdmissionResult]:
    return [admit(p, evidence, config, store) for p in proposals]
