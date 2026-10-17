"""Types owned by the agent layer (pricing, note-writer, supervisor, Shared Brain)."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from greenstove.env_protocol import STEP_HOURS, ScenarioContext

MemoryMode = Literal["off", "history", "curated"]
RunPhase = Literal["development", "evaluation"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


def stable_hash(obj: Any) -> str:
    data = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ---- model outputs (what the LLM may return; nothing else) -------------------------

class PricingOutput(_Strict):
    prices: dict[str, float]                         # window name -> price per kWh
    cited_observation_ids: list[str] = Field(default_factory=list, max_length=10)
    rationale: str = Field(default="", max_length=400)


class ObservationProposal(_Strict):
    evidence_ids: list[str] = Field(min_length=1, max_length=5)
    situation_tags: list[str] = Field(default_factory=list, max_length=8)
    description: str = Field(max_length=300)
    duplicate_of: str | None = None
    conflicts_with: list[str] = Field(default_factory=list, max_length=5)
    # structured claim, checked by the Keeper against what it recomputes from evidence
    claimed_tariff: dict[str, float] | None = None
    claimed_phase: Literal["forecast", "actual"] | None = None
    claimed_passed: bool | None = None


NoteProposal = ObservationProposal          # name used in the team diagram


class NoteWriterOutput(_Strict):
    observations: list[ObservationProposal] = Field(default_factory=list, max_length=3)


# ---- agent-layer records ------------------------------------------------------------

class TariffProposal(_Strict):
    proposal_id: str
    prices: dict[str, float]
    cited_observation_ids: list[str] = []
    rationale: str = ""

    def tariff_hash(self) -> str:
        return stable_hash(dict(sorted(self.prices.items())))


class EvidenceRecord(_Strict):
    """Immutable record of one verdict, written by the application (never by a model)."""
    run_id: str                                      # = Verdict.run_id
    agent_run_id: str
    scenario_id: str
    config_hash: str
    phase: str                                       # forecast_expected | forecast_downside | actual
    day: int                                         # simulated clock
    situation: dict[str, float | str]
    tariff: dict[str, float]
    proposal_id: str
    passed: bool
    metrics: dict[str, float]
    feedback_codes: list[str]
    outcomes: dict[str, str] = {}                    # request_id -> completed | deferred | unserved | pending | ...


class ApprovedObservation(_Strict):
    id: str
    config_hash: str
    situation: dict[str, float | str]
    tariff: dict[str, float]
    phase: str
    outcome_passed: bool
    feedback_codes: list[str]
    metrics: dict[str, float]
    evidence_ids: list[str]
    tags: list[str] = []
    description: str = ""
    created_at: int                                  # simulated day
    expires_at: int                                  # simulated day (exclusive)
    status: Literal["current", "superseded", "expired"] = "current"
    version: int = 1
    confirmations: int = 0
    conflict_group: str | None = None


KEEPER_CODES = ("evidence_unknown", "evidence_expired", "result_mismatch", "scope", "constraint_edit",
                "duplicate", "conflict")


class AdmissionResult(_Strict):
    action: Literal["added", "confirmed", "kept_separate", "rejected"]
    code: str | None = None                          # first failing Keeper stage (KEEPER_CODES)
    observation_id: str | None = None
    reasons: list[str] = []


class Brief(_Strict):
    role: str
    mode: MemoryMode
    entry_ids: list[str] = []
    versions: dict[str, int] = {}
    text: str = ""
    tokens: int = 0
    budget: int = 0
    snapshot_hash: str = ""


class CallRecord(_Strict):
    role: str
    llm_mode: str                                    # mock | crusoe
    model: str
    prompt_hash: str
    memory_hash: str
    settings: dict[str, Any]
    attempt: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    request_id: str | None = None
    outcome: Literal["ok", "repaired", "invalid", "error", "budget"]
    error: str | None = None
    cached_tokens: int = 0                           # Crusoe prompt cache hits (usage.prompt_tokens_details)
    reasoning_tokens: int = 0                        # thinking tokens when enabled for the role
    cost_usd: float | None = None                    # from /v1/models pricing or env override
    response_format: str | None = None               # json_schema | json_object | None
    ratelimit_remaining_requests: int | None = None
    ratelimit_remaining_tokens: int | None = None


class AttemptRecord(_Strict):
    n: int
    proposal: TariffProposal | None = None
    validation_errors: list[str] = []
    evidence_ids: list[str] = []
    passed: bool = False
    feedback: list[str] = []


class ExecutionRecord(_Strict):
    status: Literal["EXECUTION_COMPLETE"] = "EXECUTION_COMPLETE"
    outcome_passed: bool
    evidence_id: str
    metrics: dict[str, float]


class Decision(_Strict):
    attempt: int
    decision: Literal["continue", "re-plan", "stop", "ping_human"]
    reason: str


class RunRecord(_Strict):
    agent_run_id: str
    scenario_id: str
    memory_mode: MemoryMode
    phase: RunPhase
    env_name: str
    llm_mode: str
    status: Literal["PLAN_VERIFIED", "NO_VERIFIED_PLAN", "INFEASIBLE", "ERROR"]
    reason: str
    brief: Brief
    attempts: list[AttemptRecord] = []
    selected_proposal_id: str | None = None
    selected_tariff: dict[str, float] | None = None
    execution: ExecutionRecord | None = None
    infeasibility: dict[str, Any] | None = None
    calls: list[CallRecord] = []
    candidate_evaluations: int = 0
    simulator_runs: int = 0
    notes: list[AdmissionResult] = []
    quarantined_notes: list[ObservationProposal] = []
    decisions: list[Decision] = []                   # continue | re-plan | stop | ping_human, with reasons
    definition_of_done: dict[str, Any] | None = None  # spec hash and checks used for this run


def situation_of(context: ScenarioContext) -> dict[str, float | str]:
    """Deterministic situation descriptors used for brief matching (agent layer's own)."""
    reqs = context.requests
    demand = sum(r.power_kw * r.duration_steps * STEP_HOURS for r in reqs)
    slack = [max(0, r.latest_finish - r.earliest_start - r.duration_steps) * STEP_HOURS for r in reqs]
    return {
        "weather_class": context.weather_class or "unknown",
        "forecast_pv_kwh": round(sum(context.forecast_pv_kw) * STEP_HOURS, 2),
        "downside_pv_kwh": round(sum(context.downside_pv_kw) * STEP_HOURS, 2),
        "battery_frac": round(context.initial_battery_kwh / context.battery_capacity_kwh, 3),
        "demand_kwh": round(demand, 2),
        "peak_request_kw": round(max((r.power_kw for r in reqs), default=0.0), 2),
        "flex_hours": round(sum(slack) / len(slack), 2) if slack else 0.0,
    }
