"""simulate(plan, context, phase, actual=None) -> Trace.  Deterministic, no LLM.

Matches the contract signature exactly; the tariff travels inside the Plan.

C0 scope is interval power only. To keep that from being mistaken for a full simulation,
the C0 simulator refuses anything it does not model:
- only phase "forecast_expected"; "forecast_downside" and "actual" raise ContractError;
- no ActualScenario;
- essential load must be zero (the C0 fixture profile).
PV and battery fields are carried but not used; battery_kwh and unserved_cooking_kwh are None.
"""
from __future__ import annotations

from .billing import quote, step_prices
from .models import (
    SIMULATOR_VERSION,
    STEPS,
    ActualScenario,
    ContractError,
    Phase,
    Plan,
    ScenarioContext,
    Trace,
    normalize_tariff,
    short_hash,
    tariff_version,
)

EPS = 1e-9
C0_SUPPORTED_PHASES: tuple[Phase, ...] = ("forecast_expected",)


def simulation_id(context: ScenarioContext, plan: Plan, phase: str, actual: ActualScenario | None) -> str:
    return "sim-" + short_hash({
        "config_hash": context.config_hash,
        "scenario_hash": context.scenario_hash,
        "plan": {**plan.model_dump(mode="json"), "tariff": normalize_tariff(plan.tariff)},
        "phase": phase,
        "actual": actual.model_dump(mode="json") if actual else None,
        "simulator_version": SIMULATOR_VERSION,
    }, 12)


def check_plan(plan: Plan, context: ScenarioContext) -> list[float]:
    """Contract checks shared by simulator and verifier. Returns per-step prices."""
    prices = step_prices(plan.tariff, context)
    version = tariff_version(plan.tariff)
    requests = {r.request_id: r for r in context.requests}
    ids = [r.reservation_id for r in plan.reservations]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate reservation_id in plan")
    for res in plan.reservations:
        req = requests.get(res.request_id)
        if req is None:
            raise ContractError(f"reservation {res.reservation_id} cites unknown request {res.request_id}")
        if abs(res.power_kw - req.power_kw) > EPS:
            raise ContractError(f"reservation {res.reservation_id} power differs from its request")
        if res.end_step - res.start_step != req.duration_steps:
            raise ContractError(f"reservation {res.reservation_id} length differs from duration_steps")
        if res.tariff_version != version:
            raise ContractError(f"reservation {res.reservation_id} was quoted on a different tariff")
        cost = quote(res.power_kw, res.start_step, res.end_step, prices)
        if abs(cost - res.locked_cost) > EPS:
            raise ContractError(f"reservation {res.reservation_id} locked_cost {res.locked_cost} != recomputed {cost}")
    return prices


def cooking_profile(plan: Plan) -> list[float]:
    cooking = [0.0] * STEPS
    for res in plan.reservations:
        if res.accepted:
            for t in range(res.start_step, res.end_step):
                cooking[t] += res.power_kw
    return [round(c, 6) for c in cooking]


def simulate(plan: Plan, context: ScenarioContext, phase: Phase = "forecast_expected",
             actual: ActualScenario | None = None) -> Trace:
    if phase not in C0_SUPPORTED_PHASES:
        raise ContractError(f"UNSUPPORTED_PHASE: C0 simulator models only {C0_SUPPORTED_PHASES}, got {phase!r}")
    if actual is not None:
        raise ContractError("UNSUPPORTED_PHASE: C0 simulator does not model actual execution")
    if any(e != 0 for e in context.essential_load_kw):
        raise ContractError("UNSUPPORTED_PROFILE: C0 simulator requires zero essential load")
    check_plan(plan, context)

    cooking = cooking_profile(plan)
    demand = [round(c + e, 6) for c, e in zip(cooking, context.essential_load_kw)]
    return Trace(
        simulation_id=simulation_id(context, plan, phase, actual),
        scenario_id=context.scenario_id,
        config_hash=context.config_hash,
        scenario_hash=context.scenario_hash,
        phase=phase,
        proposal_id=plan.proposal_id,
        tariff_version=tariff_version(plan.tariff),
        simulator_version=SIMULATOR_VERSION,
        pv_kw=list(context.forecast_pv_kw),
        cooking_kw=cooking,
        demand_kw=demand,
        battery_kwh=None,
        overload_steps=[t for t in range(STEPS) if demand[t] > context.inverter_kw + EPS],
        unserved_essential_kwh=None,
        unserved_cooking_kwh=None,
        reservation_outcomes={r.reservation_id: "completed" for r in plan.reservations if r.accepted},
        extra={"scope": "c0-power-only", "note": "completed assumes sufficient energy; battery not modeled"},
    )
