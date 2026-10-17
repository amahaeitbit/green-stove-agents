"""Shared world types (docs/contracts.md), aligned with the agent layer's env_protocol.py.

Field names, phases, windows/lattice/tier_order, Condition, Feedback and Requirements follow
Isaac's provisional greenstove/env_protocol.py so the agent layer can import these types
instead of its copies. Additions on top of his layout (all from the C0 review):

- ScenarioContext.config_hash means STATION compatibility (as in his scenarios.config_hash):
  hardware, windows, lattice, tier order, household profiles. Daily inputs never change it.
  ScenarioContext.scenario_hash identifies the day.
- Plan.tariff: the plan carries the tariff it was quoted on, so the verifier can recompute
  every quote instead of trusting locked_cost.
- Trace.simulation_id: deterministic identity of one simulation (full tariff, plan, phase,
  actual inputs, simulator version).
- Verdict.run_id: deterministic identity of one evaluation (simulation_id + requirements +
  verifier version). The evidence store keys on it; identical inputs give the same id.
- Verdict.coverage: what was actually checked. complete=False can never be PLAN_VERIFIED.

Units: 96 steps of 15 min, half-open [start, end); kW, kWh; currency per kWh as float,
quotes rounded half-up to 0.01 currency; shift_penalty is currency per hour of shift.

Abel owns changes to this file after both developers agree (contracts.md).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STEPS = 96          # 15-minute intervals, 0..95
STEPS_PER_DAY = STEPS
STEP_HOURS = 0.25
SIMULATOR_VERSION = "c0-power-only-4"
VERIFIER_VERSION = "c0-power-allocator-7"
POWER_TOLERANCE_KW = 1e-9  # shared simulator/verifier boundary; never round loads first

# Informational reasons for requests the allocator did not book. These are never failures by
# themselves: the service condition decides. INVERTER_LIMIT and BUDGET_EXCEEDED are reserved for
# real violations (an overloaded interval; an accepted booking above its household budget).
UNSERVED_CAPACITY = "UNSERVED_CAPACITY"   # affordable, but no start fits the capacity left
UNSERVED_BUDGET = "UNSERVED_BUDGET"       # no allowed start is affordable for the household
UNSERVED_REASONS = (UNSERVED_CAPACITY, UNSERVED_BUDGET)   # the only reasons allowed in plan.unserved
# Emitted by the verifier (never the allocator) for a scenario request that appears in neither
# plan.reservations nor plan.unserved, e.g. after allocating a subset. Also informational.
UNSERVED_NOT_OFFERED = "UNSERVED_NOT_OFFERED"

Phase = Literal["forecast_expected", "forecast_downside", "actual"]
FORECAST_PHASES: tuple[Phase, ...] = ("forecast_expected", "forecast_downside")

# Context fields that define station compatibility (config_hash). Everything else is the day.
STATION_FIELDS = ("inverter_kw", "battery_capacity_kwh", "reserve_kwh", "essential_load_kw",
                  "windows", "lattice", "tier_order")
HASH_FIELDS = ("config_hash", "scenario_hash")


class ContractError(ValueError):
    """An input breaks the contract in a way that is an upstream bug, not a plan failure."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# ---------------------------------------------------------------- helpers
def step_label(step: int) -> str:
    minutes = step * 15
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def short_hash(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()[:n]


def normalize_tariff(tariff: dict[str, float]) -> dict[str, float]:
    return {k: float(v) for k, v in sorted(tariff.items())}


def tariff_version(tariff: dict[str, float]) -> str:
    """Hash of the tariff's contents (same recipe as stub_env.tariff_version, on floats)."""
    return hashlib.sha256(json.dumps(normalize_tariff(tariff), sort_keys=True).encode()).hexdigest()[:10]


def compute_station_hash(context_data: dict) -> str:
    """Station-scope hash for contexts built from fixture files (scenarios.py has its own)."""
    return short_hash({k: context_data[k] for k in STATION_FIELDS}, 12)


def compute_scenario_hash(context_data: dict) -> str:
    return short_hash({k: v for k, v in context_data.items() if k not in STATION_FIELDS + HASH_FIELDS}, 12)


# ---------------------------------------------------------------- inputs
class HouseholdRequest(_Strict):
    request_id: str = Field(min_length=1)
    household_id: str = Field(min_length=1)
    power_kw: float = Field(gt=0)
    duration_steps: int = Field(gt=0, le=STEPS)
    preferred_start: int = Field(ge=0, lt=STEPS)
    earliest_start: int = Field(ge=0, lt=STEPS)
    latest_finish: int = Field(gt=0, le=STEPS)      # exclusive end step
    max_cost: float = Field(ge=0)
    shift_penalty: float = Field(ge=0)              # currency per hour of shift
    auto_book: bool = False

    @model_validator(mode="after")
    def _window_fits(self) -> "HouseholdRequest":
        if self.latest_finish - self.earliest_start < self.duration_steps:
            raise ValueError("window [earliest_start, latest_finish) is shorter than duration_steps")
        if self.preferred_start < self.earliest_start or self.preferred_start + self.duration_steps > self.latest_finish:
            raise ValueError("preferred_start does not fit inside the request window")
        return self


class TariffWindow(_Strict):
    name: str = Field(min_length=1)
    start_step: int = Field(ge=0, lt=STEPS)
    end_step: int = Field(gt=0, le=STEPS)           # exclusive

    @model_validator(mode="after")
    def _ordered(self) -> "TariffWindow":
        if self.end_step <= self.start_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class ScenarioContext(_Strict):
    scenario_id: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)          # station compatibility, not the day
    scenario_hash: str = Field(min_length=1)        # the day
    day: int = 0                                     # explicit simulated clock (days)
    weather_class: str | None = None
    initial_battery_kwh: float = Field(ge=0)
    battery_capacity_kwh: float = Field(gt=0)
    reserve_kwh: float = Field(ge=0)
    inverter_kw: float = Field(gt=0)
    forecast_pv_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)
    downside_pv_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)
    essential_load_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)
    requests: list[HouseholdRequest]
    windows: list[TariffWindow]                      # fixed, partition the day
    lattice: dict[str, list[float]]                  # allowed prices per window name
    tier_order: list[str]                            # prices non-decreasing in this order
    assumptions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid(self) -> "ScenarioContext":
        for name in ("forecast_pv_kw", "downside_pv_kw", "essential_load_kw"):
            if any(v < 0 for v in getattr(self, name)):
                raise ValueError(f"{name} has negative values")
        if any(d > f for d, f in zip(self.downside_pv_kw, self.forecast_pv_kw)):
            raise ValueError("downside_pv_kw must not exceed forecast_pv_kw")
        if self.initial_battery_kwh > self.battery_capacity_kwh or self.reserve_kwh > self.battery_capacity_kwh:
            raise ValueError("battery levels exceed battery_capacity_kwh")
        cursor = 0
        for w in sorted(self.windows, key=lambda w: w.start_step):
            if w.start_step != cursor:
                raise ValueError("windows must partition steps 0..96 with no gap or overlap")
            cursor = w.end_step
        if cursor != STEPS:
            raise ValueError("windows must partition steps 0..96 with no gap or overlap")
        names = [w.name for w in self.windows]
        if len(names) != len(set(names)) or set(self.lattice) != set(names) or sorted(self.tier_order) != sorted(names):
            raise ValueError("window names, lattice keys and tier_order must match exactly")
        if any(not prices or any(p < 0 for p in prices) for prices in self.lattice.values()):
            raise ValueError("every lattice entry needs at least one non-negative price")
        ids = [r.request_id for r in self.requests]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate request_id in requests")
        return self


class ActualScenario(_Strict):
    """Visible only to execution and the evaluator. Rejected by the C0 simulator."""

    actual_pv_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)


class Reservation(_Strict):
    reservation_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    start_step: int = Field(ge=0, lt=STEPS)
    end_step: int = Field(gt=0, le=STEPS)           # exclusive
    power_kw: float = Field(gt=0)
    locked_cost: float = Field(ge=0)
    tariff_version: str = Field(min_length=1)
    accepted: bool

    @model_validator(mode="after")
    def _ordered(self) -> "Reservation":
        if self.end_step <= self.start_step:
            raise ValueError("end_step must be greater than start_step")
        return self


class Unserved(_Strict):
    request_id: str
    reason: str


class Plan(_Strict):
    proposal_id: str = Field(min_length=1)
    tariff: dict[str, float]                         # the tariff every quote was locked on
    reservations: list[Reservation]
    unserved: list[Unserved] = []


class Requirements(_Strict):
    """Fixed requirements from configuration; never relaxed by the agent layer."""
    min_service_fraction: float = Field(default=1.0, ge=0, le=1)
    max_shift_hours: float = Field(default=3.0, ge=0)
    essential_unserved_kwh: float = Field(default=0.0, ge=0)


# ---------------------------------------------------------------- outputs
class Trace(_Strict):
    simulation_id: str
    scenario_id: str
    config_hash: str
    scenario_hash: str
    phase: Phase
    proposal_id: str
    tariff_version: str
    simulator_version: str
    pv_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)
    cooking_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)
    demand_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)    # cooking + essential
    battery_kwh: list[float] | None = None           # None: battery not modeled (C0)
    overload_steps: list[int] = []
    unserved_essential_kwh: float | None = None      # None: not modeled
    unserved_cooking_kwh: float | None = None        # None: not modeled
    reservation_outcomes: dict[str, str] = {}        # reservation_id -> completed | interrupted
    extra: dict[str, Any] = {}


class Condition(_Strict):
    name: str
    passed: bool
    value: float | None = None
    limit: float | None = None


class Feedback(_Strict):
    code: str                                        # INVERTER_LIMIT, BATTERY_RESERVE, ...
    interval: int | None = None                      # first interval affected
    end_interval: int | None = None                  # exclusive end of the affected range
    request_id: str | None = None
    request_ids: list[str] = []                      # all households involved (e.g. an overload)
    value: float | None = None
    limit: float | None = None
    message: str = ""


class Coverage(_Strict):
    scope: str                                       # "c0-power-only"
    complete: bool
    phases_checked: list[Phase]
    not_modeled: list[str]


class Verdict(_Strict):
    run_id: str                                      # deterministic evaluation identity
    simulation_id: str
    scenario_id: str
    config_hash: str
    scenario_hash: str
    phase: Phase
    passed: bool                                     # every CHECKED condition passed; see coverage
    conditions: list[Condition]
    metrics: dict[str, float]
    feedback_codes: list[Feedback]
    verifier_version: str
    requirements: Requirements
    coverage: Coverage


class InfeasibilityProof(_Strict):
    lower_bound_kwh: float
    available_kwh: float
    assumptions: list[str]
