"""Independent replay of admission traces; never calls the admission transition code.

Authoritative inputs are the context, plan, requirements, BatterySpec and (for actual
execution) separately supplied observed PV. Reported completion flags/totals are checked.
"""
from decimal import Decimal
import math

from .admission_models import BatterySpec
from .models import (ActualScenario, Condition, ContractError, Coverage, Feedback, Plan,
                     Requirements, ScenarioContext, Trace, Verdict, short_hash, tariff_version)
from .simulator import check_plan, cooking_profile
from .tariff import validate_tariff
from .verifier import _check_unserved

POLICY_VERSION = "stored-energy-to-day-end-1"
ADMISSION_VERIFIER_VERSION = "independent-admission-2"
SUPPORTED_SIMULATOR_VERSION = "admission-execution-2"
LEGACY_SIMULATOR_VERSION = "admission-glue-1"
DT = Decimal("0.25")
TOL = 1e-6                         # serialized trace arrays are rounded to six decimals
ENERGY_TOL = Decimal("1e-12")


def D(value):
    return Decimal(str(value))


def configuration(context: ScenarioContext, battery: BatterySpec,
                  requirements: Requirements | None = None) -> dict:
    """Explicit identity payload for manifests and memory compatibility.

    Requirements are an additional compatibility dimension when supplied. The caller
    must use this payload/hash, rather than a literal world-name salt, for new memory.
    """
    if (battery.capacity_kwh, battery.floor_kwh) != (context.battery_capacity_kwh, context.reserve_kwh):
        raise ContractError("battery capacity/floor must match context")
    return {"station_config_hash": context.config_hash, "battery": battery.model_dump(mode="json"),
            "policy_version": POLICY_VERSION, "simulator_version": SUPPORTED_SIMULATOR_VERSION,
            "verifier_version": ADMISSION_VERIFIER_VERSION,
            "requirements": requirements.model_dump(mode="json") if requirements is not None else None}


def configuration_hash(context: ScenarioContext, battery: BatterySpec,
                       requirements: Requirements | None = None) -> str:
    return short_hash(configuration(context, battery, requirements), 12)


def _same_number(reported, expected, label):
    if (isinstance(reported, bool) or not isinstance(reported, (int, float))
            or not math.isfinite(reported) or abs(reported - float(expected)) > TOL):
        raise ContractError(f"admission trace {label} disagrees with independent replay")


def verify(trace: Trace, requirements: Requirements, context: ScenarioContext, plan: Plan,
           battery: BatterySpec, *, actual: ActualScenario | None = None, start_step: int = 0) -> Verdict:
    """Verify the declared start_step..96 simulation. Inconsistent traces raise ContractError.

    A different requirements snapshot is rejected, never silently re-simulated. For
    actual phase the evaluator must pass ActualScenario separately from the trace.
    """
    identity = configuration(context, battery, requirements)
    if isinstance(start_step, bool) or not isinstance(start_step, int) or not 0 <= start_step < 96:
        raise ContractError("start_step must be an integer from 0 to 95")
    if trace.simulator_version not in (SUPPORTED_SIMULATOR_VERSION, LEGACY_SIMULATOR_VERSION):
        raise ContractError("unsupported admission simulator version")
    current = trace.simulator_version == SUPPORTED_SIMULATOR_VERSION
    if not current and start_step != 0:
        raise ContractError("legacy admission trace must start at step zero")
    if current and trace.extra.get("start_step") != start_step:
        raise ContractError("trace start step differs from authoritative run start")
    if (trace.scenario_id, trace.config_hash, trace.scenario_hash, trace.proposal_id) != (
            context.scenario_id, context.config_hash, context.scenario_hash, plan.proposal_id):
        raise ContractError("admission trace belongs to another plan/context")
    if trace.extra.get("requirements") != requirements.model_dump():
        raise ContractError("admission trace was simulated under different requirements")
    if trace.extra.get("battery_spec") != battery.model_dump():
        raise ContractError("admission trace battery specification differs from configuration")
    _same_number(trace.extra.get("floor_kwh"), battery.floor_kwh, "floor")
    if trace.phase == "actual":
        if actual is None:
            raise ContractError("actual verification requires independently supplied actual PV")
        pv = actual.actual_pv_kw
    elif trace.phase in ("forecast_expected", "forecast_downside"):
        if actual is not None:
            raise ContractError("forecast verification cannot take actual inputs")
        pv = context.forecast_pv_kw if trace.phase == "forecast_expected" else context.downside_pv_kw
    else:
        raise ContractError("unsupported admission phase")
    if any(not math.isfinite(p) or p < 0 for p in pv):
        raise ContractError("PV must be finite and non-negative")
    if trace.pv_kw != list(pv):
        raise ContractError("trace PV differs from authoritative phase inputs")
    if validate_tariff(plan.tariff, context):
        raise ContractError("INVALID_TARIFF")
    check_plan(plan, context)
    _check_unserved(plan, context)
    request_ids = [r.request_id for r in plan.reservations]
    if len(set(request_ids)) != len(request_ids):
        raise ContractError("duplicate request reservation")
    req = {r.request_id: r for r in context.requests}
    for r in plan.reservations:
        if r.accepted and not req[r.request_id].auto_book:
            raise ContractError("reservation exceeds household authority")
        q = req[r.request_id]
        if r.start_step < q.earliest_start or r.end_step > q.latest_finish:
            raise ContractError("reservation/offer outside household window")
        if r.locked_cost > q.max_cost:
            raise ContractError("reservation/offer exceeds household budget")
    held = cooking_profile(plan, include_pending=True)
    if any(h + e > context.inverter_kw + 1e-9 for h, e in zip(held, context.essential_load_kw)):
        raise ContractError("reservation capacity holds overbook inverter")
    sim_identity = {"plan": plan.model_dump(mode="json"), "scenario": context.scenario_hash,
                                 "config": context.config_hash, "phase": trace.phase, "pv": list(pv),
                                 "battery": battery.model_dump(), "req": requirements.model_dump(),
                                 "v": trace.simulator_version}
    if current:
        sim_identity["start_step"] = start_step
    expected_sim_id = short_hash(sim_identity, 16)
    if trace.simulation_id != expected_sim_id:
        raise ContractError("admission simulation identity mismatch")
    # Version 1 glue used a different tariff hash; accept only these two explicit recipes.
    # New adapters should always emit the shared canonical recipe.
    if trace.tariff_version not in (tariff_version(plan.tariff), short_hash(sorted(plan.tariff.items()), 10)):
        raise ContractError("admission trace tariff identity mismatch")
    if trace.battery_kwh is None or len(trace.battery_kwh) != 96:
        raise ContractError("admission trace must contain 96 battery samples")

    stored, floor = D(context.initial_battery_kwh), D(battery.floor_kwh)
    eta = D(battery.discharge_efficiency)
    active, admitted, decisions, outcomes = [], [], [], {}
    deferred = {}
    total_shortage = Decimal(0)
    if any(r.start_step < start_step for r in plan.reservations):
        raise ContractError("reservation precedes authoritative run start")
    for t in range(start_step):
        _same_number(trace.cooking_kw[t], 0, f"pre-run cooking[{t}]")
        _same_number(trace.demand_kw[t], context.essential_load_kw[t], f"pre-run demand[{t}]")
        _same_number(trace.battery_kwh[t], stored, f"pre-run battery[{t}]")
    for t in range(start_step, 96):
        for r in sorted((r for r in plan.reservations if r.start_step == t), key=lambda r: r.request_id):
            q = req[r.request_id]
            available = max(Decimal(0), stored - floor)
            required = Decimal(0)
            reason = None
            if not r.accepted or not q.auto_book:
                reason = "APPROVAL_REQUIRED"
            elif r.start_step < q.earliest_start or r.end_step > q.latest_finish:
                reason = "OUTSIDE_WINDOW"
            elif r.locked_cost > q.max_cost:
                reason = "BUDGET_EXCEEDED"
            elif abs(r.start_step - q.preferred_start) * .25 > requirements.max_shift_hours:
                reason = "SHIFT_LIMIT"
            else:
                future = [sum((D(a.power_kw) for a in [*active, r] if s < a.end_step), Decimal(0))
                          + D(context.essential_load_kw[s]) for s in range(t, 96)]
                for demand in future:
                    if demand > D(context.inverter_kw):
                        reason = "INVERTER_CAPACITY"
                        break
                    if demand > D(battery.max_discharge_kw):
                        reason = "BATTERY_POWER"
                        break
                if reason is None:
                    required = sum(future, Decimal(0)) * DT / eta
                    if required > available + ENERGY_TOL:
                        reason = "INSUFFICIENT_STORED_ENERGY"
            status = "ADMIT" if reason is None else (
                "UNSERVED" if t >= q.latest_finish - q.duration_steps else "DEFER")
            decisions.append({"reservation_id": r.reservation_id, "request_id": r.request_id, "step": t,
                              "status": status, "reason": reason or "RESOURCES_COMMITTED",
                              "required_kwh": float(required), "available_kwh": float(available)})
            if status == "ADMIT":
                active.append(r)
                admitted.append(r)
            else:
                outcomes[r.reservation_id] = "deferred" if status == "DEFER" else "unserved"
                if current and status == "DEFER" and reason != "APPROVAL_REQUIRED":
                    deferred[r.reservation_id] = (r, decisions[-1])
        for rid, (r, first) in list(deferred.items()):
            if t >= req[r.request_id].latest_finish - req[r.request_id].duration_steps:
                decisions.append({"reservation_id": rid, "request_id": r.request_id, "step": t,
                                  "status": "UNSERVED", "reason": first["reason"],
                                  "ledger_reason": "NEW_RESERVATION_REQUIRED", "final": True,
                                  "required_kwh": first["required_kwh"],
                                  "available_kwh": float(max(Decimal(0), stored-floor))})
                outcomes[rid] = "unserved"
                del deferred[rid]
        cooking = sum((D(r.power_kw) for r in active), Decimal(0))
        essential, solar = D(context.essential_load_kw[t]), D(pv[t])
        discharge_kw = min(D(battery.max_discharge_kw), max(Decimal(0), stored - floor) * eta / DT)
        supply_kw = min(D(context.inverter_kw), solar + discharge_kw)
        served_essential = min(essential, supply_kw)
        if cooking > supply_kw - served_essential + ENERGY_TOL:
            raise ContractError("independent replay cannot supply an admitted session")
        used_pv = min(solar, served_essential + cooking)
        stored -= (served_essential + cooking - used_pv) * DT / eta
        if stored < floor and floor - stored <= ENERGY_TOL:
            stored = floor
        charge_kw = min(max(Decimal(0), solar - used_pv), D(battery.max_charge_kw),
                        max(Decimal(0), D(battery.capacity_kwh) - stored) / DT / D(battery.charge_efficiency))
        stored += charge_kw * DT * D(battery.charge_efficiency)
        # The ledger persists a float at each transition; keep that serialization boundary.
        stored = D(float(stored))
        total_shortage += (essential - served_essential) * DT
        _same_number(trace.cooking_kw[t], cooking, f"cooking[{t}]")
        _same_number(trace.demand_kw[t], cooking + essential, f"demand[{t}]")
        _same_number(trace.battery_kwh[t], stored, f"battery[{t}]")
        for r in active:
            if r.end_step == t + 1:
                outcomes[r.reservation_id] = "completed"
        active = [r for r in active if r.end_step > t + 1]

    reported = trace.extra.get("admission")
    if not isinstance(reported, list) or len(reported) != len(decisions):
        raise ContractError("admission decision count mismatch")
    for got, expected in zip(reported, decisions):
        if not isinstance(got, dict) or set(got) != set(expected):
            raise ContractError("admission decision fields mismatch")
        for key in expected:
            if key in ("required_kwh", "available_kwh"):
                _same_number(got[key], expected[key], f"decision {key}")
            elif got[key] != expected[key]:
                raise ContractError(f"admission decision {key} mismatch")
    if trace.extra.get("ledger_error") is not None or trace.reservation_outcomes != outcomes:
        raise ContractError("admission completion/ledger outcome mismatch")
    _same_number(trace.extra.get("admitted"), len(admitted), "admitted count")
    _same_number(trace.unserved_essential_kwh, total_shortage, "essential shortage")
    missed = [r for r in plan.reservations if r.accepted and r not in admitted]
    unserved_kwh = sum(r.power_kw * (r.end_step-r.start_step) * .25 for r in missed)
    _same_number(trace.unserved_cooking_kwh, unserved_kwh, "unserved cooking")
    if trace.overload_steps:
        raise ContractError("admission trace claims overload despite independently feasible delivery")

    served = len(admitted) / len(context.requests) if context.requests else 1.0
    shifts = [abs(r.start_step-req[r.request_id].preferred_start)*.25 for r in admitted]
    conditions = [Condition(name="independent_energy_replay", passed=True),
                  Condition(name="booked_sessions_admitted", passed=not missed, value=float(len(missed)), limit=0),
                  Condition(name="essential", passed=float(total_shortage) <= requirements.essential_unserved_kwh + 1e-9,
                            value=float(total_shortage), limit=requirements.essential_unserved_kwh),
                  Condition(name="service", passed=served >= requirements.min_service_fraction-1e-9,
                            value=served, limit=requirements.min_service_fraction)]
    feedback = [Feedback(code=u.reason, request_id=u.request_id) for u in plan.unserved]
    listed = set(request_ids) | {u.request_id for u in plan.unserved}
    feedback += [Feedback(code="UNSERVED_NOT_OFFERED", request_id=r) for r in sorted(set(req)-listed)]
    feedback += [Feedback(code="ADMISSION_REFUSED", request_id=r.request_id,
                          message=next(d["reason"] for d in decisions if d["reservation_id"] == r.reservation_id))
                 for r in missed]
    if not conditions[2].passed:
        feedback.append(Feedback(code="ESSENTIAL_UNSERVED", value=float(total_shortage)))
    if not conditions[3].passed:
        feedback.append(Feedback(code="SERVICE_SHORTFALL", value=served, limit=requirements.min_service_fraction))
    return Verdict(run_id="run-" + short_hash({"simulation": trace.simulation_id, "configuration": identity}, 16),
                   simulation_id=trace.simulation_id, scenario_id=context.scenario_id,
                   config_hash=context.config_hash, scenario_hash=context.scenario_hash, phase=trace.phase,
                   passed=all(c.passed for c in conditions), conditions=conditions, feedback_codes=feedback,
                   metrics={"served_fraction": served, "completed_sessions": float(len(admitted)),
                            "requested_sessions": float(len(context.requests)), "admitted_sessions": float(len(admitted)),
                            "deferred_sessions": float(sum(d["status"] == "DEFER" and d["reason"] != "APPROVAL_REQUIRED" for d in decisions)),
                            "pending_approval_sessions": float(sum(d["reason"] == "APPROVAL_REQUIRED" for d in decisions)),
                            "unserved_cooking_kwh": unserved_kwh, "unserved_essential_kwh": float(total_shortage),
                            "min_battery_kwh": min([context.initial_battery_kwh, *trace.battery_kwh]),
                            "overload_steps": 0.0, "max_shift_hours": max(shifts, default=0.0),
                            "total_spend": round(sum(r.locked_cost for r in admitted), 2)},
                   requirements=requirements.model_copy(deep=True), verifier_version=ADMISSION_VERIFIER_VERSION,
                   coverage=Coverage(scope="independent-admission-energy", complete=True, phases_checked=[trace.phase],
                                     not_modeled=["distribution losses", "voltage/phase", "household connection limits",
                                                  "manual approvals", "re-offer after DEFER"]))
