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

    def __init__(self, requirements: Requirements | None = None) -> None:
        self.requirements = requirements or Requirements()

    def use_requirements(self, requirements: Requirements) -> None:
        self.requirements = requirements             # allocator must not pick shifts the verifier rejects (review A4)

    def household_requests(self, context: ScenarioContext):
        return households.household_requests(context)

    def allocate(self, requests, tariff: dict[str, float], context: ScenarioContext, proposal_id: str) -> Plan:
        return allocator.allocate(requests, tariff, context, proposal_id,
                                  max_shift_hours=self.requirements.max_shift_hours)

    def simulate(self, plan: Plan, context: ScenarioContext, phase: str, actual: ActualScenario | None = None) -> Trace:
        return simulator.simulate(plan, context, phase, actual)

    def verify(self, trace: Trace, requirements: Requirements, context: ScenarioContext, plan: Plan) -> Verdict:
        return verifier.verify(trace, requirements, context, plan)

    def lower_bound(self, context: ScenarioContext, requirements: Requirements) -> InfeasibilityProof | None:
        return None                      # C0 has no battery model, so no energy bound can be proved


# ---------------------------------------------------------------------------------------
# GREENSTOVE_WORLD=abel-admission: Abel's households, allocator and battery-backed admission
# ledger drive a full-day simulation for every phase (expected, downside, actual). The energy
# verdict below is partner glue over Abel's ledger results, pending Abel's joint sign-off.
# ---------------------------------------------------------------------------------------
import os

from greenstove import admission, billing
from greenstove.admission_models import BatterySpec
from greenstove.env_protocol import (Condition, ContractError, Coverage, Feedback, HouseholdRequest, Reservation,
                                     short_hash)

ADMISSION_VERSION = "admission-glue-2"
MAX_REOFFERS = 4                                  # re-offers per request after energy DEFERs, then UNSERVED
REOFFER_LABEL = "re-offer (opt-in, pending Abel's approval)"
SCOPE = "admission-energy (Abel ledger; partner verdict glue, pending sign-off)"
SCOPE_REOFFER = f"admission-energy (Abel ledger; partner verdict glue + {REOFFER_LABEL}, pending sign-off)"
_REASON_CODE = {"INSUFFICIENT_STORED_ENERGY": "BATTERY_RESERVE", "INVERTER_CAPACITY": "INVERTER_LIMIT",
                "BATTERY_POWER": "BATTERY_POWER_LIMIT", "BUDGET_EXCEEDED": "BUDGET_EXCEEDED",
                "SHIFT_LIMIT": "OUTSIDE_WINDOW", "OUTSIDE_WINDOW": "OUTSIDE_WINDOW"}
_ENERGY_REASONS = {"INSUFFICIENT_STORED_ENERGY", "INVERTER_CAPACITY", "BATTERY_POWER"}


def battery_spec(context: ScenarioContext) -> BatterySpec:
    """Capacity and floor from the scenario (as Abel's policy requires); power and efficiency synthetic."""
    return BatterySpec(capacity_kwh=context.battery_capacity_kwh, floor_kwh=context.reserve_kwh,
                       max_charge_kw=context.inverter_kw, max_discharge_kw=context.inverter_kw,
                       charge_efficiency=0.95, discharge_efficiency=0.95)


def reoffer_from_env() -> bool:
    return os.environ.get("GREENSTOVE_REOFFER") == "1"


def next_offer(deferred: Reservation, original_id: str, k: int, request: HouseholdRequest,
               tariff: dict[str, float], context: ScenarioContext, requirements: Requirements) -> Reservation | None:
    """A NEW reservation after a DEFER, within the unchanged household rules, or None.

    Earliest later start s (s > deferred start) whose session still fits the household window,
    whose shift from the preferred start is within requirements.max_shift_hours (admission's own
    comparison) and whose repriced quote (billing.quote_for on the same tariff) is within max_cost.
    Authority comes only from the household's auto_book rule; without it nothing is re-offered.
    """
    if not request.auto_book:
        return None
    for s in range(max(deferred.start_step + 1, request.earliest_start),
                   request.latest_finish - request.duration_steps + 1):
        if abs(s - request.preferred_start) * .25 > requirements.max_shift_hours:
            continue
        cost = billing.quote_for(request.power_kw, s, s + request.duration_steps, tariff, context)
        if cost > request.max_cost:
            continue
        return Reservation(reservation_id=f"{original_id}:reoffer{k}", request_id=request.request_id,
                           start_step=s, end_step=s + request.duration_steps, power_kw=request.power_kw,
                           locked_cost=cost, tariff_version=deferred.tariff_version, accepted=True)
    return None


class AdmissionWorld:
    name = ("abel-admission (Abel's households, allocator and admission ledger, all phases; "
            "energy verdict = partner glue, pending Abel sign-off)")

    def __init__(self, requirements: Requirements | None = None, reoffer: bool | None = None) -> None:
        """reoffer None reads GREENSTOVE_REOFFER=1 (default off: a DEFER is finalised UNSERVED, never retimed)."""
        self.requirements = requirements or Requirements()
        self.reoffer = reoffer_from_env() if reoffer is None else bool(reoffer)
        if self.reoffer:
            self.name = f"{AdmissionWorld.name} + {REOFFER_LABEL}"

    def use_requirements(self, requirements: Requirements) -> None:
        self.requirements = requirements             # admission's shift check uses the day's requirements

    def household_requests(self, context: ScenarioContext):
        return households.household_requests(context)

    def allocate(self, requests, tariff: dict[str, float], context: ScenarioContext, proposal_id: str) -> Plan:
        return allocator.allocate(requests, tariff, context, proposal_id,
                                  max_shift_hours=self.requirements.max_shift_hours)

    def simulate(self, plan: Plan, context: ScenarioContext, phase: str, actual: ActualScenario | None = None) -> Trace:
        pv = {"forecast_expected": context.forecast_pv_kw, "forecast_downside": context.downside_pv_kw}.get(phase)
        if phase == "actual":
            if actual is None:
                raise ContractError("actual phase needs actual PV")
            pv = actual.actual_pv_kw
        if pv is None:
            raise ContractError(f"UNSUPPORTED_PHASE: {phase!r}")
        spec = battery_spec(context)
        state = admission.initial_state(context, spec)
        req = {q.request_id: q for q in context.requests}
        starts: dict[int, list[tuple[int, str, Reservation]]] = {}     # step -> (k, original id, reservation)
        for r in plan.reservations:
            starts.setdefault(r.start_step, []).append((0, r.reservation_id, r))
        decisions, outcomes, battery, cooking = [], {}, [], [0.0] * 96
        admitted_ids: set[str] = set()
        chains: dict[str, dict] = {}                  # original reservation id -> its re-offers
        offered: list[Reservation] = []
        error = None
        for t in range(96):
            # Booked starts before re-offers: a new offer never displaces a household's booked start.
            for k, orig, r in sorted(starts.get(t, []), key=lambda x: (x[0] > 0, x[2].request_id, x[2].reservation_id)):
                try:
                    d = admission.can_start(r, plan.tariff, context, state, spec, requirements=self.requirements)
                except ContractError as exc:                 # an upstream contract break is a bug, not a phase gap
                    raise RuntimeError(f"admission contract violated at step {t}: {exc}") from exc
                rec = {"reservation_id": r.reservation_id, "request_id": r.request_id, "step": t,
                       "status": d.status, "reason": d.reason,
                       "required_kwh": d.required_storage_kwh, "available_kwh": d.available_storage_kwh}
                decisions.append(rec)
                if d.status == "ADMIT":
                    state = d.state
                    admitted_ids.add(r.reservation_id)
                    for s in range(r.start_step, r.end_step):
                        cooking[s] += r.power_kw
                    continue
                new = None
                if self.reoffer and d.status == "DEFER" and d.reason in _ENERGY_REASONS and k < MAX_REOFFERS:
                    new = next_offer(r, orig, k + 1, req[r.request_id], plan.tariff, context, self.requirements)
                if new is not None:                          # repriced, authorized by auto_book, recorded: never silent
                    outcomes[r.reservation_id] = "reoffered"
                    offered.append(new)
                    starts.setdefault(new.start_step, []).append((k + 1, orig, new))
                    chain = chains.setdefault(orig, {"request_id": r.request_id, "chain": [], "completed_by": None})
                    chain["chain"].append(new.reservation_id)
                    decisions.append({"reservation_id": new.reservation_id, "request_id": r.request_id, "step": t,
                                      "status": "REOFFER", "reason": f"REPRICED_AFTER_{d.reason}",
                                      "replaces": r.reservation_id, "original_reservation_id": orig,
                                      "start_step": new.start_step, "end_step": new.end_step,
                                      "quote": new.locked_cost, "max_cost": req[r.request_id].max_cost,
                                      "required_kwh": 0.0, "available_kwh": d.available_storage_kwh,
                                      "label": REOFFER_LABEL})
                else:                                        # no new authorized reservation: finalised, never retimed
                    outcomes[r.reservation_id] = "unserved"
                    if d.status == "DEFER":
                        rec["status"] = "DEFER→UNSERVED"
            try:
                state = admission.advance_interval(state, context, spec, pv_kw=pv[t]).state
            except ContractError as exc:                 # ledger assumption broken: an admitted session failed
                error = str(exc)[:200]
                battery += [battery[-1] if battery else state.battery_kwh] * (96 - t)
                break
            battery.append(round(state.battery_kwh, 6))
        done_requests = set(state.completed_request_ids)
        by_id = {r.reservation_id: r for r in [*plan.reservations, *offered]}
        for rid in admitted_ids:
            finished = error is None or by_id[rid].request_id in done_requests
            outcomes[rid] = "completed" if finished else "interrupted"
        for orig, chain in chains.items():
            chain["completed_by"] = next((i for i in chain["chain"] if outcomes.get(i) == "completed"), None)
            chain["final"] = outcomes.get(chain["chain"][-1], "not_started")
        demand = [round(c + e, 6) for c, e in zip(cooking, context.essential_load_kw)]
        admitted_requests = {by_id[i].request_id for i in admitted_ids}
        not_started = [r for r in plan.reservations if r.accepted and r.request_id not in admitted_requests]
        sim_id = short_hash({"plan": plan.model_dump(mode="json"), "scenario": context.scenario_hash,
                             "config": context.config_hash, "phase": phase, "pv": pv,
                             "battery": spec.model_dump(), "req": self.requirements.model_dump(),
                             "v": ADMISSION_VERSION, "reoffer": MAX_REOFFERS if self.reoffer else 0}, 16)
        return Trace(simulation_id=sim_id, scenario_id=context.scenario_id, config_hash=context.config_hash,
                     scenario_hash=context.scenario_hash, phase=phase, proposal_id=plan.proposal_id,
                     tariff_version=short_hash(sorted(plan.tariff.items()), 10), simulator_version=ADMISSION_VERSION,
                     pv_kw=list(pv), cooking_kw=[round(c, 6) for c in cooking], demand_kw=demand, battery_kwh=battery,
                     overload_steps=[t for t in range(96) if demand[t] > context.inverter_kw + 1e-9],
                     unserved_essential_kwh=round(state.unserved_essential_kwh, 6),
                     unserved_cooking_kwh=round(sum(r.power_kw * (r.end_step - r.start_step) * 0.25 for r in not_started), 6),
                     reservation_outcomes=outcomes,
                     extra={"admission": decisions, "ledger_error": error, "floor_kwh": spec.floor_kwh,
                            "battery_spec": spec.model_dump(), "admitted": len(admitted_ids),
                            "requirements": self.requirements.model_dump(),
                            "reoffer": {"enabled": self.reoffer, "label": REOFFER_LABEL if self.reoffer else
                                        "off: a DEFER is finalised as unserved, never retimed",
                                        "max_reoffers": MAX_REOFFERS if self.reoffer else 0},
                            "reoffers": chains, "reoffer_reservations": [r.model_dump() for r in offered]})

    def verify(self, trace: Trace, requirements: Requirements, context: ScenarioContext, plan: Plan) -> Verdict:
        if (trace.scenario_id, trace.proposal_id) != (context.scenario_id, plan.proposal_id):
            raise ContractError("trace does not belong to this plan/context")
        if trace.extra.get("requirements") != requirements.model_dump():   # never judge with other rules
            self.use_requirements(requirements)
            actual = ActualScenario(actual_pv_kw=list(trace.pv_kw)) if trace.phase == "actual" else None
            trace = self.simulate(plan, context, trace.phase, actual)
        req = {q.request_id: q for q in context.requests}
        offered = [Reservation(**x) for x in trace.extra.get("reoffer_reservations", [])]
        reoffer_on = bool(trace.extra.get("reoffer", {}).get("enabled"))
        booked = {r.reservation_id for r in plan.reservations}
        done = [r for r in [*plan.reservations, *offered]          # the reservation that actually ran
                if trace.reservation_outcomes.get(r.reservation_id) == "completed"]
        cooked = {r.request_id for r in done}
        rescued = [r for r in done if r.reservation_id not in booked]
        decisions = trace.extra.get("admission", [])
        offers = [d for d in decisions if d["status"] == "REOFFER"]
        attempts = [d for d in decisions if d["status"] != "REOFFER"]
        pending = [d for d in attempts if d["reason"] == "APPROVAL_REQUIRED"]     # waiting for the household
        refused = [d for d in attempts if d["status"] != "ADMIT" and d["reason"] != "APPROVAL_REQUIRED"]
        failed = [d for d in refused if d["request_id"] not in cooked]           # not rescued by a re-offer
        energy_failed = sorted({d["request_id"] for d in failed if d["reason"] in _ENERGY_REASONS})
        served = len(done) / max(1, len(context.requests))
        shifts = [abs(r.start_step - req[r.request_id].preferred_start) * 0.25 for r in done]
        quotes = {r.reservation_id: billing.quote_for(r.power_kw, r.start_step, r.end_step, plan.tariff, context) for r in done}
        over_budget = [r for r in done if quotes[r.reservation_id] > req[r.request_id].max_cost + 1e-9]
        floor = trace.extra.get("floor_kwh", context.reserve_kwh)
        min_batt = min(trace.battery_kwh) if trace.battery_kwh else context.initial_battery_kwh
        conds = [
            Condition(name="inverter", passed=not trace.overload_steps, value=float(len(trace.overload_steps)), limit=0),
            Condition(name="ledger_consistent", passed=trace.extra.get("ledger_error") is None),
            Condition(name="battery_floor", passed=min_batt >= floor - 1e-6, value=min_batt, limit=floor),
            Condition(name="essential", passed=(trace.unserved_essential_kwh or 0) <= requirements.essential_unserved_kwh + 1e-9,
                      value=trace.unserved_essential_kwh, limit=requirements.essential_unserved_kwh),
            Condition(name="booked_sessions_admitted", passed=not energy_failed, value=float(len(energy_failed)), limit=0),
            Condition(name="service", passed=served >= requirements.min_service_fraction - 1e-9, value=served,
                      limit=requirements.min_service_fraction),
            Condition(name="max_shift", passed=max(shifts, default=0) <= requirements.max_shift_hours + 1e-9,
                      value=max(shifts, default=0.0), limit=requirements.max_shift_hours),
            Condition(name="budget", passed=not over_budget, value=float(len(over_budget)), limit=0)]
        fb = [Feedback(code=_REASON_CODE.get(d["reason"], d["reason"]), interval=d["step"], request_id=d["request_id"],
                       value=d["required_kwh"], limit=d["available_kwh"],
                       message=f"admission {d['status']} ({d['reason']}): needs {d['required_kwh']:.2f} kWh "
                               f"above the floor, {d['available_kwh']:.2f} available") for d in failed[:6]]
        for u in plan.unserved:                    # price signal: no affordable start / no capacity left
            fb.append(Feedback(code={"UNSERVED_BUDGET": "BUDGET_EXCEEDED", "UNSERVED_CAPACITY": "INVERTER_LIMIT"}.get(
                u.reason, u.reason), request_id=u.request_id, message=f"allocator: {u.reason}"))
        for r in over_budget:
            fb.append(Feedback(code="BUDGET_EXCEEDED", request_id=r.request_id, value=quotes[r.reservation_id],
                               limit=req[r.request_id].max_cost))
        if max(shifts, default=0) > requirements.max_shift_hours + 1e-9:
            fb.append(Feedback(code="OUTSIDE_WINDOW", value=max(shifts), limit=requirements.max_shift_hours,
                               message="a booked start is shifted more than allowed"))
        if trace.overload_steps:
            fb.append(Feedback(code="INVERTER_LIMIT", interval=trace.overload_steps[0]))
        if (trace.unserved_essential_kwh or 0) > requirements.essential_unserved_kwh + 1e-9:
            fb.append(Feedback(code="ESSENTIAL_UNSERVED", value=trace.unserved_essential_kwh))
        if served < requirements.min_service_fraction - 1e-9:
            fb.append(Feedback(code="SERVICE_SHORTFALL", value=served, limit=requirements.min_service_fraction,
                               message=f"{len(done)} of {len(context.requests)} requests cooked"))
        for r in rescued:                          # informational, last: the household was re-offered, not failed
            first = next(d for d in refused if d["request_id"] == r.request_id)
            fb.append(Feedback(code="REOFFERED", interval=first["step"], request_id=r.request_id,
                               value=quotes[r.reservation_id], limit=req[r.request_id].max_cost,
                               message=f"informational: deferred at step {first['step']} ({first['reason']}); "
                                       f"{REOFFER_LABEL} {r.reservation_id} started at step {r.start_step}, "
                                       f"repriced {quotes[r.reservation_id]:.2f} within budget, completed"))
        metrics = {"served_fraction": round(served, 6), "completed_sessions": float(len(done)),
                   "requested_sessions": float(len(context.requests)),
                   "admitted_sessions": float(trace.extra.get("admitted", len(done))),
                   "deferred_sessions": float(sum(d["status"] in ("DEFER", "DEFER→UNSERVED") for d in refused)),
                   "reoffers": float(len(offers)), "rescued_by_reoffer": float(len(rescued)),
                   "pending_approval_sessions": float(len(pending)),
                   "unserved_cooking_kwh": trace.unserved_cooking_kwh or 0.0,
                   "unserved_essential_kwh": trace.unserved_essential_kwh or 0.0, "min_battery_kwh": round(min_batt, 6),
                   "overload_steps": float(len(trace.overload_steps)), "max_shift_hours": max(shifts, default=0.0),
                   "total_spend": round(sum(quotes.values()), 2)}
        run_id = f"{context.scenario_id}:{plan.proposal_id}:{trace.phase}:{short_hash([trace.simulation_id, requirements.model_dump()], 8)}"
        return Verdict(run_id=run_id, simulation_id=trace.simulation_id, scenario_id=context.scenario_id,
                       config_hash=context.config_hash, scenario_hash=context.scenario_hash, phase=trace.phase,
                       passed=all(c.passed for c in conds), conditions=conds, metrics=metrics, feedback_codes=fb,
                       verifier_version=ADMISSION_VERSION, requirements=requirements,
                       coverage=Coverage(scope=SCOPE_REOFFER if reoffer_on else SCOPE,
                                         complete=True, phases_checked=[trace.phase],
                                         not_modeled=["distribution losses", "voltage/phase", "household connection limits",
                                                      "manual approvals",
                                                      "re-offer after non-energy refusals" if reoffer_on else
                                                      "re-offer after DEFER (off: finalised unserved; "
                                                      "GREENSTOVE_REOFFER=1 opts in)"]))

    def lower_bound(self, context: ScenarioContext, requirements: Requirements) -> InfeasibilityProof | None:
        """Pre-dawn proof under Abel's floor policy, then the world-independent energy bound."""
        spec = battery_spec(context)
        need, t = 0.0, 0
        while t < 96 and context.downside_pv_kw[t] < context.essential_load_kw[t]:   # PV cannot cover essential yet
            need += (context.essential_load_kw[t] - context.downside_pv_kw[t]) * 0.25
            t += 1
        above_floor = max(0.0, context.initial_battery_kwh - spec.floor_kwh) * spec.discharge_efficiency
        if need - requirements.essential_unserved_kwh > above_floor + 1e-9:
            return InfeasibilityProof(lower_bound_kwh=round(need, 4), available_kwh=round(above_floor, 4), assumptions=[
                f"essential load before downside PV covers it (steps 0-{max(0, t - 1)}) must come from storage above the floor",
                f"floor {spec.floor_kwh} kWh (Abel's admission policy), discharge efficiency {spec.discharge_efficiency}",
                "optimistic: no cooking at all before sunrise"])
        from greenstove.stub_env import StubEnvironment     # world-independent energy bound (optimistic)
        return StubEnvironment().lower_bound(context, requirements)
