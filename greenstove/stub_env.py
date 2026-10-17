"""DEVELOPMENT STUB of the Environment protocol -- NOT Abel's simulator.

A small deterministic stand-in so the agent layer can be built and demoed before the
real world model lands. Deliberately simple rules:

* Allocation: requests in (preferred_start, request_id) order; each household ranks
  starts by quoted cost (integrated per interval across tiers) + shift_penalty * shift
  hours, ties to the earliest start, skipping starts over max_cost. The best start that
  keeps essential + cooking <= inverter_kw in every interval is reserved. Pending offers
  (auto_book False) hold capacity but are never dispatched or counted as served.
* Simulation: PV serves essential load, then cooking; surplus charges the battery (95%)
  up to capacity, the rest is curtailed and counted. Deficits discharge it (95%). Cooking
  may not take the battery below reserve: the latest-started active session is
  interrupted and its remaining energy counted unserved. Essential load may empty the
  battery; any shortfall is unserved essential energy. No separate battery power limit.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid

from greenstove.env_protocol import (
    STEP_HOURS, STEPS, ActualScenario, Condition, Feedback, HouseholdRequest,
    InfeasibilityProof, Plan, Requirements, Reservation, ScenarioContext, Trace, Unserved,
    Verdict,
)

STUB_NAME = "stub-env (development stand-in, not Abel's simulator)"
CHARGE_EFF = DISCHARGE_EFF = 0.95
PHASES = ("forecast_expected", "forecast_downside", "actual")
EPS = 1e-9


def tariff_version(tariff: dict[str, float]) -> str:
    return hashlib.sha256(json.dumps(tariff, sort_keys=True).encode()).hexdigest()[:10]


def step_prices(tariff: dict[str, float], context: ScenarioContext) -> list[float]:
    """Price per kWh for each interval; ValueError (INVALID_TARIFF) on bad keys/windows."""
    if set(tariff) != {w.name for w in context.windows}:
        raise ValueError(f"INVALID_TARIFF: keys {sorted(tariff)} do not match the tariff windows")
    owner = {t: w.name for w in context.windows for t in range(w.start_step, w.end_step)}
    if sum(w.end_step - w.start_step for w in context.windows) != STEPS or len(owner) != STEPS:
        raise ValueError("INVALID_TARIFF: windows do not partition the day")
    return [float(tariff[owner[t]]) for t in range(STEPS)]


def session_cost(req: HouseholdRequest, start: int, prices: list[float]) -> float:
    return sum(req.power_kw * STEP_HOURS * prices[t] for t in range(start, start + req.duration_steps))


class StubEnvironment:
    name = STUB_NAME

    def household_requests(self, context: ScenarioContext) -> list[HouseholdRequest]:
        return list(context.requests)

    # ---- allocation ---------------------------------------------------------------
    def allocate(self, requests: list[HouseholdRequest], tariff: dict[str, float],
                 context: ScenarioContext, proposal_id: str) -> Plan:
        prices, version = step_prices(tariff, context), tariff_version(tariff)
        load = list(context.essential_load_kw)            # forecast interval load (kW)
        plan = Plan(proposal_id=proposal_id, reservations=[], unserved=[])
        seen: set[str] = set()
        for req in sorted(requests, key=lambda r: (r.preferred_start, r.request_id)):
            reason, chosen = None, None
            last = min(STEPS, req.latest_finish) - req.duration_steps
            starts = range(max(0, req.earliest_start), last + 1)
            ranked = []                                   # (score, start, cost), within budget
            for s in starts:
                cost = session_cost(req, s, prices)
                if cost <= req.max_cost + EPS:
                    shift_h = abs(s - req.preferred_start) * STEP_HOURS
                    ranked.append((round(cost + req.shift_penalty * shift_h, 9), s, cost))
            ranked.sort()
            if req.request_id in seen:
                reason = "DUPLICATE_RESERVATION"
            elif not starts:
                reason = "OUTSIDE_WINDOW"
            elif not ranked:
                reason = "BUDGET_EXCEEDED"
            else:
                fits = (x for x in ranked if all(load[t] + req.power_kw <= context.inverter_kw + EPS
                                                 for t in range(x[1], x[1] + req.duration_steps)))
                chosen = next(fits, None)
                reason = None if chosen else "INVERTER_LIMIT"
            seen.add(req.request_id)
            if reason:
                plan.unserved.append(Unserved(request_id=req.request_id, reason=reason))
                continue
            _, s, cost = chosen
            for t in range(s, s + req.duration_steps):
                load[t] += req.power_kw
            plan.reservations.append(Reservation(
                reservation_id=f"{proposal_id}:{req.request_id}", request_id=req.request_id,
                start_step=s, end_step=s + req.duration_steps, power_kw=req.power_kw,
                locked_cost=round(cost, 2), tariff_version=version, accepted=req.auto_book))
        return plan

    # ---- simulation ---------------------------------------------------------------
    def simulate(self, plan: Plan, context: ScenarioContext, phase: str,
                 actual: ActualScenario | None = None) -> Trace:
        pv = {"forecast_expected": context.forecast_pv_kw, "forecast_downside": context.downside_pv_kw,
              "actual": actual.actual_pv_kw if actual else None}.get(phase)
        if pv is None:
            raise ValueError(f"phase must be one of {PHASES}; 'actual' needs an ActualScenario")
        running = sorted((r for r in plan.reservations if r.accepted),
                         key=lambda r: (r.start_step, r.reservation_id))
        battery = context.initial_battery_kwh
        interrupted: dict[str, int] = {}
        demand, levels, overload = [], [], []
        unserved_ess = unserved_cook = served_cook = charged = discharged = curtailed = 0.0
        for t in range(STEPS):
            active = [r for r in running if r.start_step <= t < r.end_step
                      and r.reservation_id not in interrupted]
            ess_kw = context.essential_load_kw[t]
            demand.append(round(ess_kw + sum(r.power_kw for r in active), 6))
            if demand[-1] > context.inverter_kw + EPS:
                overload.append(t)
            pv_left = pv[t] * STEP_HOURS
            ess_e = ess_kw * STEP_HOURS                  # essential: PV, then battery to empty
            from_pv = min(ess_e, pv_left)
            pv_left -= from_pv
            draw = min(battery, (ess_e - from_pv) / DISCHARGE_EFF)
            battery -= draw
            discharged += draw
            unserved_ess += ess_e - from_pv - draw * DISCHARGE_EFF
            while active:                                # cooking: PV, then battery to reserve
                cook_e = sum(r.power_kw for r in active) * STEP_HOURS
                cook_pv = min(cook_e, pv_left)
                need = (cook_e - cook_pv) / DISCHARGE_EFF
                if need <= EPS or battery - need >= context.reserve_kwh - EPS:
                    pv_left -= cook_pv
                    battery -= need
                    discharged += need
                    served_cook += cook_e
                    break
                victim = active.pop()                    # latest-started session stops
                interrupted[victim.reservation_id] = t
                unserved_cook += victim.power_kw * STEP_HOURS * (victim.end_step - t)
            if pv_left > 0:                              # surplus: charge, then curtail
                stored = min(pv_left * CHARGE_EFF, context.battery_capacity_kwh - battery)
                battery += stored
                charged += stored / CHARGE_EFF
                curtailed += pv_left - stored / CHARGE_EFF
            levels.append(round(battery, 6))
        outcomes = {r.reservation_id: "interrupted" if r.reservation_id in interrupted
                    else "completed" for r in running}
        extra = {"env": STUB_NAME, "pv_kwh": round(sum(pv) * STEP_HOURS, 6),
                 "pv_to_battery_kwh": round(charged, 6), "battery_out_kwh": round(discharged, 6),
                 "curtailed_kwh": round(curtailed, 6), "served_cooking_kwh": round(served_cook, 6),
                 "interrupted_at": interrupted}            # reservation_id -> interval stopped
        return Trace(scenario_id=context.scenario_id, phase=phase, proposal_id=plan.proposal_id,
                     pv_kw=list(pv), demand_kw=demand, battery_kwh=levels, overload_steps=overload,
                     unserved_essential_kwh=round(unserved_ess, 6),
                     unserved_cooking_kwh=round(unserved_cook, 6),
                     reservation_outcomes=outcomes, extra=extra)

    # ---- verification -------------------------------------------------------------
    def verify(self, trace: Trace, requirements: Requirements, context: ScenarioContext,
               plan: Plan) -> Verdict:
        if trace.proposal_id != plan.proposal_id or trace.scenario_id != context.scenario_id:
            raise ValueError("trace does not belong to this plan/context")
        req, rq = {r.request_id: r for r in context.requests}, requirements
        accepted = [r for r in plan.reservations if r.accepted]
        done = [r for r in accepted if trace.reservation_outcomes.get(r.reservation_id) == "completed"]
        broken = [r for r in accepted if r not in done]
        overload = sorted({t for t, d in enumerate(trace.demand_kw) if d > context.inverter_kw + EPS}
                          | set(trace.overload_steps))
        n_req = len(context.requests)
        served = len(done) / n_req if n_req else 1.0
        shifts = {r.request_id: abs(r.start_step - req[r.request_id].preferred_start) * STEP_HOURS
                  for r in accepted}
        max_shift = max(shifts.values(), default=0.0)
        over_budget = [r for r in plan.reservations if r.locked_cost > req[r.request_id].max_cost + EPS]
        ids = [r.request_id for r in plan.reservations]
        bad_booking = [r for r in plan.reservations if ids.count(r.request_id) > 1
                       or r.start_step < req[r.request_id].earliest_start
                       or r.end_step > req[r.request_id].latest_finish]
        ess_ok = trace.unserved_essential_kwh <= rq.essential_unserved_kwh + EPS
        service_ok = served >= rq.min_service_fraction - EPS
        fb: list[Feedback] = []
        if overload:
            fb.append(Feedback(code="INVERTER_LIMIT", interval=overload[0],
                               value=trace.demand_kw[overload[0]], limit=context.inverter_kw))
        stops = trace.extra.get("interrupted_at", {})
        for r in broken:
            t = stops.get(r.reservation_id)            # value = battery before that interval
            before = trace.battery_kwh[t - 1] if t else context.initial_battery_kwh
            fb.append(Feedback(code="BATTERY_RESERVE", request_id=r.request_id, interval=t,
                               value=round(before, 4), limit=context.reserve_kwh))
        if not ess_ok:                                 # interval = first time battery is empty
            empty = next((i for i, b in enumerate(trace.battery_kwh) if b <= EPS), None)
            fb.append(Feedback(code="ESSENTIAL_UNSERVED", interval=empty,
                               value=trace.unserved_essential_kwh, limit=rq.essential_unserved_kwh))
        fb += [Feedback(code=u.reason, request_id=u.request_id) for u in plan.unserved]
        fb += [Feedback(code="BUDGET_EXCEEDED", request_id=r.request_id, value=r.locked_cost,
                        limit=req[r.request_id].max_cost) for r in over_budget]
        fb += [Feedback(code="DUPLICATE_RESERVATION" if ids.count(r.request_id) > 1
                        else "OUTSIDE_WINDOW", request_id=r.request_id, interval=r.start_step)
               for r in bad_booking]
        fb += [Feedback(code="OUTSIDE_WINDOW", request_id=rid, value=h, limit=rq.max_shift_hours)
               for rid, h in shifts.items() if h > rq.max_shift_hours + EPS]
        if not service_ok:   # value = sessions not completed, limit = misses the floor allows
            allowed = n_req - math.ceil(rq.min_service_fraction * n_req - EPS)
            fb.append(Feedback(code="SERVICE_SHORTFALL", value=float(n_req - len(done)),
                               limit=float(allowed)))
        conds = [
            Condition(name="inverter", passed=not overload, value=len(overload), limit=0),
            Condition(name="battery_reserve", passed=not broken and trace.unserved_cooking_kwh <= EPS,
                      value=trace.unserved_cooking_kwh, limit=0),
            Condition(name="essential", passed=ess_ok, value=trace.unserved_essential_kwh,
                      limit=rq.essential_unserved_kwh),
            Condition(name="service", passed=service_ok, value=served, limit=rq.min_service_fraction),
            Condition(name="max_shift", passed=max_shift <= rq.max_shift_hours + EPS,
                      value=max_shift, limit=rq.max_shift_hours),
            Condition(name="budget", passed=not over_budget, value=len(over_budget), limit=0),
            Condition(name="bookings", passed=not bad_booking, value=len(bad_booking), limit=0),
        ]
        spend = round(sum(r.locked_cost for r in done), 2)   # interrupted sessions are not billed
        metrics = {"served_fraction": round(served, 6), "completed_sessions": float(len(done)),
                   "requested_sessions": float(n_req),
                   "unserved_cooking_kwh": trace.unserved_cooking_kwh,
                   "unserved_essential_kwh": trace.unserved_essential_kwh,
                   "min_battery_kwh": min(trace.battery_kwh, default=context.initial_battery_kwh),
                   "overload_steps": float(len(overload)), "max_shift_hours": max_shift,
                   "total_spend": spend, "revenue": spend}
        run_id = f"{context.scenario_id}:{trace.proposal_id}:{trace.phase}:{uuid.uuid4().hex[:8]}"
        return Verdict(run_id=run_id, scenario_id=context.scenario_id, config_hash=context.config_hash,
                       phase=trace.phase, passed=all(c.passed for c in conds), conditions=conds,
                       metrics=metrics, feedback_codes=fb)

    # ---- deterministic infeasibility bound ------------------------------------------
    def lower_bound(self, context: ScenarioContext,
                    requirements: Requirements) -> InfeasibilityProof | None:
        available = context.initial_battery_kwh + sum(context.downside_pv_kw) * STEP_HOURS
        essential = max(0.0, sum(context.essential_load_kw) * STEP_HOURS
                        - requirements.essential_unserved_kwh)
        n_needed = math.ceil(requirements.min_service_fraction * len(context.requests) - EPS)
        bookable = sorted(r.power_kw * r.duration_steps * STEP_HOURS
                          for r in context.requests if r.auto_book)
        cooking = sum(bookable[:n_needed])
        required = essential + cooking
        assumptions = [
            "optimistic: 100% charge/discharge efficiency, no curtailment, no reserve floor",
            "PV = downside forecast (a verified plan must pass the downside phase)",
            f"cooking = the {n_needed} smallest auto-bookable sessions "
            f"(min_service_fraction {requirements.min_service_fraction} of {len(context.requests)})",
            f"essential = {essential:.3f} kWh after allowed unserved {requirements.essential_unserved_kwh}",
        ]
        over = [t for t, e in enumerate(context.essential_load_kw) if e > context.inverter_kw + EPS]
        short = len(bookable) < n_needed
        assumptions += [f"essential load exceeds inverter_kw at interval {over[0]}"] if over else []
        assumptions += [f"only {len(bookable)} requests may auto-book; {n_needed} needed"] if short else []
        if not (over or short or required > available + EPS):
            return None
        return InfeasibilityProof(lower_bound_kwh=round(required, 4),
                                  available_kwh=round(available, 4), assumptions=assumptions)
