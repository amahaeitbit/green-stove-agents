"""Environment boundary between the agent layer (partner) and the world (Abel).

World types now come from Abel's greenstove/models.py (the shared contract); this module
re-exports them and defines the one protocol the agent layer talks to.
GREENSTOVE_WORLD selects the world: "stub" (default, labeled development stand-in with
battery, actual weather and energy bound), "abel" (Abel's households, allocator, C0 power-only
simulator and verifier) or "abel-admission" (Abel's households, allocator and admission ledger
for every phase; independent energy verification and shared execution/acceptance loop).
"""
from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from greenstove.models import (STEP_HOURS, STEPS, ActualScenario, Condition, ContractError, Coverage,  # noqa: F401
                               Feedback, HouseholdRequest, InfeasibilityProof, Plan, Requirements, Reservation,
                               ScenarioContext, TariffWindow, Trace, Unserved, Verdict, compute_scenario_hash,
                               short_hash)


def with_scenario_hash(context: ScenarioContext) -> ScenarioContext:
    """Recompute the day hash after the day's inputs change (e.g. a new forecast)."""
    data = context.model_dump(mode="json")
    data["scenario_hash"] = compute_scenario_hash(data)
    return ScenarioContext.model_validate(data)


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
    """GREENSTOVE_WORLD: stub (default) | abel | abel-admission. Unknown names raise."""
    world = os.environ.get("GREENSTOVE_WORLD", "stub")
    if world not in ("stub", "abel", "abel-admission"):
        raise ValueError(f"unknown GREENSTOVE_WORLD={world!r}; use stub, abel or abel-admission")
    if not prefer_stub and world == "abel":
        from greenstove.world_adapter import AbelC0World
        return AbelC0World()
    if not prefer_stub and world == "abel-admission":
        from greenstove.admission_world import AdmissionWorld
        return AdmissionWorld()
    from greenstove.stub_env import StubEnvironment
    return StubEnvironment()
