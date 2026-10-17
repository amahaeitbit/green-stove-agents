"""Environment boundary between the agent layer (partner) and the world (Abel).

The world types below mirror docs/contracts.md. They are PROVISIONAL: when Abel's
greenstove/models.py lands, import his types here instead and delete these copies.
The agent layer only talks to the world through the Environment protocol.
"""
from __future__ import annotations

import importlib
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

STEPS = 96          # 15-minute intervals, 0..95
STEP_HOURS = 0.25


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class HouseholdRequest(_Strict):
    request_id: str
    household_id: str
    power_kw: float = Field(gt=0)
    duration_steps: int = Field(gt=0)
    preferred_start: int = Field(ge=0, lt=STEPS)
    earliest_start: int = Field(ge=0, lt=STEPS)
    latest_finish: int = Field(gt=0, le=STEPS)      # exclusive end step
    max_cost: float = Field(ge=0)
    shift_penalty: float = Field(ge=0)              # currency per hour of shift
    auto_book: bool = False


class TariffWindow(_Strict):
    name: str
    start_step: int = Field(ge=0, lt=STEPS)
    end_step: int = Field(gt=0, le=STEPS)           # exclusive


class ScenarioContext(_Strict):
    scenario_id: str
    config_hash: str
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


class ActualScenario(_Strict):
    actual_pv_kw: list[float] = Field(min_length=STEPS, max_length=STEPS)


class Reservation(_Strict):
    reservation_id: str
    request_id: str
    start_step: int
    end_step: int                                    # exclusive
    power_kw: float
    locked_cost: float
    tariff_version: str
    accepted: bool


class Unserved(_Strict):
    request_id: str
    reason: str


class Plan(_Strict):
    proposal_id: str
    reservations: list[Reservation]
    unserved: list[Unserved] = []


class Trace(_Strict):
    scenario_id: str
    phase: str                                       # forecast_expected | forecast_downside | actual
    proposal_id: str
    pv_kw: list[float]
    demand_kw: list[float]                           # cooking + essential
    battery_kwh: list[float]                         # end of each interval
    overload_steps: list[int] = []
    unserved_essential_kwh: float = 0.0
    unserved_cooking_kwh: float = 0.0
    reservation_outcomes: dict[str, str] = {}        # reservation_id -> completed | interrupted
    extra: dict[str, Any] = {}


class Condition(_Strict):
    name: str
    passed: bool
    value: float | None = None
    limit: float | None = None


class Feedback(_Strict):
    code: str                                        # INVERTER_LIMIT, BATTERY_RESERVE, ...
    interval: int | None = None
    request_id: str | None = None
    value: float | None = None
    limit: float | None = None


class Verdict(_Strict):
    run_id: str
    scenario_id: str
    config_hash: str
    phase: str
    passed: bool
    conditions: list[Condition]
    metrics: dict[str, float]
    feedback_codes: list[Feedback]


class Requirements(_Strict):
    """Fixed requirements from configuration; never relaxed by the agent layer."""
    min_service_fraction: float = 1.0                # accepted+completed sessions / requested
    max_shift_hours: float = 3.0
    essential_unserved_kwh: float = 0.0


class InfeasibilityProof(_Strict):
    lower_bound_kwh: float
    available_kwh: float
    assumptions: list[str]


@runtime_checkable
class Environment(Protocol):
    name: str

    def household_requests(self, context: ScenarioContext) -> list[HouseholdRequest]: ...
    def allocate(self, requests: list[HouseholdRequest], tariff: dict[str, float],
                 context: ScenarioContext, proposal_id: str) -> Plan: ...
    def simulate(self, plan: Plan, context: ScenarioContext, phase: str,
                 actual: ActualScenario | None = None) -> Trace: ...
    def verify(self, trace: Trace, requirements: Requirements,
               context: ScenarioContext, plan: Plan) -> Verdict: ...
    def lower_bound(self, context: ScenarioContext,
                    requirements: Requirements) -> InfeasibilityProof | None: ...


def load_environment(prefer_stub: bool = False) -> Environment:
    """Abel's world if his modules exist, else the labeled development stub."""
    if not prefer_stub:
        try:
            mod = importlib.import_module("greenstove.world_adapter")
            return mod.WorldEnvironment()
        except ModuleNotFoundError:
            pass
    from greenstove.stub_env import StubEnvironment
    return StubEnvironment()
