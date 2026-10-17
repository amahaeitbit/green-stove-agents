"""Abel's world behind the agent layer's Environment protocol (GREENSTOVE_WORLD=abel).

Households, allocation, simulation and verification are all Abel's (greenstove/households.py,
allocator.py, simulator.py, verifier.py; C0: power only, zero essential load, forecast phases
only, coverage.complete=False). The energy bound is not available (C0 does not model the
battery). The Supervisor never turns an incomplete-coverage pass into PLAN_VERIFIED.
"""
from __future__ import annotations

from greenstove import allocator, households, simulator, verifier
from greenstove.env_protocol import (ActualScenario, InfeasibilityProof, Plan, Requirements, ScenarioContext, Trace,
                                     Verdict)


class AbelC0World:
    name = "abel-c0 (Abel's households, allocator, simulator and verifier; power-only)"

    def household_requests(self, context: ScenarioContext):
        return households.household_requests(context)

    def allocate(self, requests, tariff: dict[str, float], context: ScenarioContext, proposal_id: str) -> Plan:
        return allocator.allocate(requests, tariff, context, proposal_id)

    def simulate(self, plan: Plan, context: ScenarioContext, phase: str, actual: ActualScenario | None = None) -> Trace:
        return simulator.simulate(plan, context, phase, actual)

    def verify(self, trace: Trace, requirements: Requirements, context: ScenarioContext, plan: Plan) -> Verdict:
        return verifier.verify(trace, requirements, context, plan)

    def lower_bound(self, context: ScenarioContext, requirements: Requirements) -> InfeasibilityProof | None:
        return None                      # C0 has no battery model, so no energy bound can be proved
