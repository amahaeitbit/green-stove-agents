"""verify(trace, requirements, context, plan) -> Verdict.  The only place pass/fail is decided.

Signature matches the agent layer's Environment protocol. Trust boundary:
- Authoritative inputs: context (fixed station config incl. inverter_kw), requirements, and
  the plan's reservations and tariff.
- The trace is checked, not trusted: its simulation_id must match the plan/context/phase, and
  its load arrays and overload list must equal what the verifier recomputes from the plan.
  Any disagreement is a ContractError, so a tampered or stale trace cannot pass.
- Quotes are recomputed from plan.tariff; window, budget, duplicate, shift and service
  results are derived here, never read from flags.
"""
from __future__ import annotations

import math

from .models import (
    STEP_HOURS,
    STEPS,
    POWER_TOLERANCE_KW,
    UNSERVED_NOT_OFFERED,
    UNSERVED_REASONS,
    VERIFIER_VERSION,
    Condition,
    ContractError,
    Coverage,
    Feedback,
    Plan,
    Requirements,
    ScenarioContext,
    Trace,
    Verdict,
    short_hash,
    step_label,
)
from .simulator import check_plan, cooking_profile, simulation_id

TOL = 1e-6
C0_COVERAGE = Coverage(
    scope="c0-power-only",
    complete=False,
    phases_checked=["forecast_expected"],
    not_modeled=["pv_supply", "battery_energy", "battery_reserve", "battery_power",
                 "essential_load", "forecast_downside", "actual_execution"],
)


def verdict_run_id(simulation: str, requirements: Requirements) -> str:
    return "run-" + short_hash({"simulation_id": simulation, "requirements": requirements.model_dump(mode="json"),
                                "verifier_version": VERIFIER_VERSION}, 12)


def _check_trace(trace: Trace, context: ScenarioContext, plan: Plan) -> tuple[list[float], list[list[str]]]:
    if trace.phase != "forecast_expected":
        raise ContractError(f"UNSUPPORTED_PHASE: C0 verifier checks only forecast_expected, got {trace.phase!r}")
    if (trace.scenario_id, trace.config_hash, trace.scenario_hash, trace.proposal_id) != (
            context.scenario_id, context.config_hash, context.scenario_hash, plan.proposal_id):
        raise ContractError("trace does not belong to this plan/context")
    if trace.simulation_id != simulation_id(context, plan, trace.phase, None):
        raise ContractError("trace simulation_id does not match plan, context and phase")
    check_plan(plan, context)
    cooking = cooking_profile(plan)
    who: list[list[str]] = [[] for _ in range(STEPS)]
    for r in plan.reservations:
        if r.accepted:
            for t in range(r.start_step, r.end_step):
                who[t].append(r.request_id)
    demand = [math.fsum((c, e)) for c, e in zip(cooking, context.essential_load_kw)]
    for t in range(STEPS):
        if abs(cooking[t] - trace.cooking_kw[t]) > TOL or abs(demand[t] - trace.demand_kw[t]) > TOL:
            raise ContractError(f"trace load at interval {t} disagrees with the plan's reservations")
    overload = [t for t in range(STEPS) if demand[t] > context.inverter_kw + POWER_TOLERANCE_KW]
    if sorted(trace.overload_steps) != overload:
        raise ContractError("trace overload_steps disagree with recomputed loads")
    _check_unserved(plan, context)
    # Pending offers consume no energy yet, but promise capacity on approval.
    # Recompute holds from the plan rather than trusting allocator provenance.
    held = cooking_profile(plan, include_pending=True)
    for t, (all_cooking, accepted_cooking, essential) in enumerate(
        zip(held, cooking, context.essential_load_kw)
    ):
        if (all_cooking > accepted_cooking
                and math.fsum((all_cooking, essential)) > context.inverter_kw + POWER_TOLERANCE_KW):
            raise ContractError(f"pending capacity holds overbook the inverter at interval {t}")
    return demand, who


def _check_unserved(plan: Plan, context: ScenarioContext) -> None:
    """plan.unserved is informational, so it must also be truthful: known scenario requests,
    each listed once, never also reserved, with an informational reason only."""
    known = {r.request_id for r in context.requests}
    reserved = {r.request_id for r in plan.reservations}
    seen: set[str] = set()
    for u in plan.unserved:
        if u.request_id not in known:
            raise ContractError(f"unserved entry cites unknown request {u.request_id}")
        if u.request_id in seen:
            raise ContractError(f"request {u.request_id} is listed as unserved more than once")
        if u.request_id in reserved:
            raise ContractError(f"request {u.request_id} is both reserved and listed as unserved")
        if u.reason not in UNSERVED_REASONS:
            raise ContractError(f"unserved reason {u.reason!r} is not one of {UNSERVED_REASONS}")
        seen.add(u.request_id)


def verify(trace: Trace, requirements: Requirements, context: ScenarioContext, plan: Plan) -> Verdict:
    demand, who = _check_trace(trace, context, plan)
    limit = context.inverter_kw
    reqs = {r.request_id: r for r in context.requests}
    fb: list[Feedback] = []

    # Inverter: one feedback per contiguous overloaded range with the same households.
    over = [t for t in range(STEPS) if demand[t] > limit + POWER_TOLERANCE_KW]
    groups: list[list[int]] = []
    for t in over:
        if groups and groups[-1][-1] == t - 1 and sorted(who[t - 1]) == sorted(who[t]):
            groups[-1].append(t)
        else:
            groups.append([t])
    for g in groups:
        start, end = g[0], g[-1] + 1
        peak = max(demand[t] for t in g)
        fb.append(Feedback(code="INVERTER_LIMIT", interval=start, end_interval=end, request_ids=sorted(who[start]),
                           value=peak, limit=limit,
                           message=f"Load {peak:g} kW exceeds the {limit:g} kW limit from "
                                   f"{step_label(start)} to {step_label(end)}."))

    accepted = [r for r in plan.reservations if r.accepted]
    counts: dict[str, int] = {}
    for r in plan.reservations:
        counts[r.request_id] = counts.get(r.request_id, 0) + 1
    bad_booking: list[str] = []
    for rid in sorted(k for k, n in counts.items() if n > 1):
        bad_booking.append(rid)
        fb.append(Feedback(code="DUPLICATE_RESERVATION", request_id=rid, value=float(counts[rid]), limit=1.0,
                           message=f"Request {rid} has {counts[rid]} reservations."))
    for r in plan.reservations:
        q = reqs[r.request_id]
        if r.start_step < q.earliest_start or r.end_step > q.latest_finish:
            bad_booking.append(r.request_id)
            fb.append(Feedback(code="OUTSIDE_WINDOW", request_id=r.request_id, interval=r.start_step,
                               end_interval=r.end_step,
                               message=f"Reservation {r.reservation_id} is outside the household window."))
    over_budget = [r for r in plan.reservations if r.locked_cost > reqs[r.request_id].max_cost + TOL]
    for r in over_budget:
        fb.append(Feedback(code="BUDGET_EXCEEDED", request_id=r.request_id, value=r.locked_cost,
                           limit=reqs[r.request_id].max_cost,
                           message=f"Reservation {r.reservation_id} costs more than the household maximum."))
    # Informational only (UNSERVED_* codes); the service condition decides pass/fail.
    fb += [Feedback(code=u.reason, request_id=u.request_id, message="Not booked by the allocator.")
           for u in plan.unserved]
    # Every scenario request is accounted for: one left out of the allocation entirely gets a code too.
    listed = {r.request_id for r in plan.reservations} | {u.request_id for u in plan.unserved}
    fb += [Feedback(code=UNSERVED_NOT_OFFERED, request_id=rid,
                    message="Not included in this allocation; no reservation or offer was made.")
           for rid in sorted(set(reqs) - listed)]

    shifts = {r.request_id: abs(r.start_step - reqs[r.request_id].preferred_start) * STEP_HOURS for r in accepted}
    max_shift = max(shifts.values(), default=0.0)
    for rid, h in sorted(shifts.items()):
        if h > requirements.max_shift_hours + TOL:
            fb.append(Feedback(code="OUTSIDE_WINDOW", request_id=rid, value=h, limit=requirements.max_shift_hours,
                               message=f"Request {rid} is shifted {h:g} h, above the allowed maximum."))

    # Power-only C0: an accepted session completes (energy is assumed sufficient; see coverage).
    completed = {r.request_id for r in accepted}
    n_req = len(context.requests)
    served = len(completed) / n_req if n_req else 1.0
    service_ok = served >= requirements.min_service_fraction - TOL
    if not service_ok:
        allowed = n_req - math.ceil(requirements.min_service_fraction * n_req - TOL)
        fb.append(Feedback(code="SERVICE_SHORTFALL", request_ids=sorted(set(reqs) - completed),
                           value=float(n_req - len(completed)), limit=float(allowed),
                           message="Too few requests have an accepted reservation."))

    conditions = [
        Condition(name="inverter", passed=not over, value=float(len(over)), limit=0.0),
        Condition(name="service", passed=service_ok, value=served, limit=requirements.min_service_fraction),
        Condition(name="max_shift", passed=max_shift <= requirements.max_shift_hours + TOL,
                  value=max_shift, limit=requirements.max_shift_hours),
        Condition(name="budget", passed=not over_budget, value=float(len(over_budget)), limit=0.0),
        Condition(name="bookings", passed=not bad_booking, value=float(len(bad_booking)), limit=0.0),
    ]
    spend = round(sum(r.locked_cost for r in accepted), 2)
    sim = trace.simulation_id
    return Verdict(
        run_id=verdict_run_id(sim, requirements),
        simulation_id=sim,
        scenario_id=context.scenario_id,
        config_hash=context.config_hash,
        scenario_hash=context.scenario_hash,
        phase=trace.phase,
        # Unserved reasons explain allocation outcomes, not necessarily failed
        # requirements: a configured service floor may legitimately permit them.
        # Every enforced requirement above has its own authoritative condition.
        passed=all(c.passed for c in conditions),
        conditions=conditions,
        metrics={
            "served_fraction": round(served, 6),
            "completed_sessions": float(len(completed)),
            "requested_sessions": float(n_req),
            "overload_steps": float(len(over)),
            "max_shift_hours": max_shift,
            "total_spend": spend,
            "revenue": spend,
            "peak_load_kw": max(demand),
            "cooking_energy_kwh": round(sum(trace.cooking_kw) * STEP_HOURS, 6),
        },
        feedback_codes=fb,
        verifier_version=VERIFIER_VERSION,
        requirements=requirements.model_copy(deep=True),
        coverage=C0_COVERAGE.model_copy(deep=True),
    )
