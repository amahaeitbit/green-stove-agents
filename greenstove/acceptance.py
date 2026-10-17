"""Joint acceptance run from docs/integration-checkpoint-review.md ("Joint acceptance run").

Synthetic last-hour scenario, same numbers as Abel's scripts/admission_check.py and
fixtures/admission/commitment-example.json: battery 1.0 kWh measured at step 92 (23:00), floor
0.4 kWh, efficiencies 1.0, no PV, essential 0.1 kW to the end of the day, inverter 4 kW. hh-A and
hh-B auto-book 1 kW for 30 min at 23:00 (window to 24:00); hh-C is pending (not auto-book).

``execute`` is the single owner of the admission ledger for one run (review B1): it mirrors
AdmissionWorld.simulate (Abel's allocator plan, ``admission.can_start`` at each booked start in
request order, ``advance_interval`` every step, state carried between every call) but starts at
an explicit ``start_step`` with an explicit BatterySpec, and finalises an energy DEFER as
UNSERVED at the request's last valid start (no automatic retiming). This is a rehearsal of the
execution path, not a verified plan; labels say so. No network, no file writes.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

from greenstove import admission, allocator
from greenstove.admission_models import AdmissionState, BatterySpec
from greenstove.household_messages import HouseholdMessage, household_outcomes, render_one
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import (STEPS, ContractError, HouseholdRequest, Plan, Requirements, ScenarioContext, Trace,
                               short_hash, step_label)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures/admission/commitment-example.json"
C0_CONTEXT = ROOT / "fixtures/c0/context.json"
TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}
VERSION = "acceptance-loop-1"
HOUSEHOLDS = (("req-A", "hh-A", True), ("req-B", "hh-B", True), ("req-C", "hh-C", False))


# ------------------------------------------------------------------ scenario (Abel's builder)
def acceptance_scenario() -> tuple[ScenarioContext, BatterySpec, dict]:
    """Abel's last-hour builder from scripts/admission_check.py, plus pending hh-C and zero PV."""
    fx = json.loads(FIXTURE.read_text())
    data = json.loads(C0_CONTEXT.read_text())
    start = fx["start_step"]
    data.update(scenario_id="joint-acceptance-last-hour", initial_battery_kwh=fx["initial_battery_kwh"],
                battery_capacity_kwh=fx["capacity_kwh"], reserve_kwh=fx["floor_kwh"], inverter_kw=fx["inverter_kw"],
                forecast_pv_kw=[0.0] * STEPS, downside_pv_kw=[0.0] * STEPS,
                essential_load_kw=[0.0] * start + [fx["essential_kw"]] * (STEPS - start))
    data["requests"] = [HouseholdRequest(
        request_id=rid, household_id=hid, power_kw=fx["session_power_kw"], duration_steps=fx["session_duration_steps"],
        preferred_start=start, earliest_start=start, latest_finish=STEPS, max_cost=100.0, shift_penalty=1.0,
        auto_book=auto).model_dump() for rid, hid, auto in HOUSEHOLDS]
    context = context_from_dict(with_hashes(data))
    battery = BatterySpec(**{k: fx[k] for k in ("capacity_kwh", "floor_kwh", "max_charge_kw", "max_discharge_kw",
                                                 "charge_efficiency", "discharge_efficiency")})
    return context, battery, fx


# ------------------------------------------------------------------ the single ledger loop
@dataclass
class Event:
    step: int
    kind: str                      # admission | finalise | interval | completed | end
    text: str
    battery_after: float | None = None
    request_id: str | None = None
    messages: list[HouseholdMessage] = field(default_factory=list)   # full routed set at this moment


@dataclass
class Execution:
    trace: Trace
    state: AdmissionState
    events: list[Event]
    decisions: list[dict]


def execute(plan: Plan, context: ScenarioContext, battery: BatterySpec, *, start_step: int = 0,
            pv_kw: list[float] | None = None, requirements: Requirements | None = None) -> Execution:
    """Run the day from ``start_step`` to 96 with one ledger. No caller-supplied state is accepted:
    every can_start sees the state returned by the previous transition (no stale snapshot)."""
    requirements = requirements or Requirements()
    pv = list(pv_kw) if pv_kw is not None else [0.0] * STEPS
    if len(pv) != STEPS or any(not math.isfinite(p) or p < 0 for p in pv):
        raise ContractError("execution requires 96 finite non-negative PV samples")
    if any(r.start_step < start_step for r in plan.reservations):
        raise ContractError("reservation precedes authoritative run start")
    req = {r.request_id: r for r in context.requests}
    booked = [r.request_id for r in plan.reservations]
    if len(booked) != len(set(booked)):                 # one admission per request, ever
        raise ContractError("plan books a request more than once; refusing before any admission")
    state = admission.initial_state(context, battery, start_step=start_step)
    starts: dict[int, list] = {}
    for r in plan.reservations:
        starts.setdefault(r.start_step, []).append(r)
    decisions: list[dict] = []
    outcomes: dict[str, str] = {}
    events: list[Event] = []
    deferred: dict[str, tuple] = {}
    admitted: set[str] = set()
    cooking = [0.0] * STEPS
    battery_after = [context.initial_battery_kwh] * start_step     # outside the run: no load, measured at start

    def snapshot() -> list[HouseholdMessage]:
        return household_outcomes(context, plan, _trace(plan, context, battery, requirements, pv, cooking,
                                                        battery_after, state, decisions, outcomes, admitted))

    for t in range(start_step, STEPS):
        for r in sorted(starts.get(t, []), key=lambda r: r.request_id):        # fixed order
            d = admission.can_start(r, plan.tariff, context, state, battery, requirements=requirements)
            decisions.append({"reservation_id": r.reservation_id, "request_id": r.request_id, "step": t,
                              "status": d.status, "reason": d.reason,
                              "required_kwh": d.required_storage_kwh, "available_kwh": d.available_storage_kwh})
            if d.status == "ADMIT":
                state = d.state                                                 # persist before the next call
                admitted.add(r.reservation_id)
                for s in range(r.start_step, r.end_step):
                    cooking[s] += r.power_kw
            elif d.status == "DEFER":
                outcomes[r.reservation_id] = "deferred"
                if d.reason != "APPROVAL_REQUIRED":                             # pending offers wait for the household
                    deferred[r.reservation_id] = (r, d)
            else:
                outcomes[r.reservation_id] = "unserved"
            need = f" (needs {d.required_storage_kwh:.3f}, has {d.available_storage_kwh:.3f})" \
                if d.reason in ("RESOURCES_COMMITTED", "INSUFFICIENT_STORED_ENERGY") else ""
            events.append(Event(t, "admission", f"{r.request_id} {d.status} {d.reason}{need}",
                                request_id=r.request_id, messages=snapshot()))
        for rid, (r, first) in list(deferred.items()):     # B2: DEFER -> UNSERVED at the last valid start
            if t < req[r.request_id].latest_finish - req[r.request_id].duration_steps:
                continue
            final = admission.can_start(r, plan.tariff, context, state, battery, requirements=requirements)
            if final.status != "UNSERVED":
                raise ContractError(f"ledger did not finalise {rid} at its last valid start: {final.status}")
            decisions.append({"reservation_id": rid, "request_id": r.request_id, "step": t, "status": "UNSERVED",
                              "reason": first.reason, "ledger_reason": final.reason, "final": True,
                              "required_kwh": first.required_storage_kwh, "available_kwh": final.available_storage_kwh})
            outcomes[rid] = "unserved"
            del deferred[rid]
            events.append(Event(t, "finalise", f"{r.request_id} finalised UNSERVED at last valid start "
                                f"{step_label(t)} ({first.reason}; no new authorised reservation)",
                                request_id=r.request_id, messages=snapshot()))
        result = admission.advance_interval(state, context, battery, pv_kw=pv[t])
        done = [c for c in state.commitments if c.end_step == t + 1]
        running = [c.request_id + (" cooking (final interval)" if c.end_step == t + 1 else " cooking")
                   for c in state.commitments]
        state = result.state
        battery_after.append(round(state.battery_kwh, 6))
        events.append(Event(t, "interval", f"interval {step_label(t)} delivered"
                            + (f"; {', '.join(running)}" if running else ""), battery_after=state.battery_kwh))
        for c in done:
            outcomes[c.reservation_id] = "completed"
            events.append(Event(t + 1, "completed", f"{c.request_id} completed (final interval {t} delivered)",
                                request_id=c.request_id, messages=snapshot()))
    events.append(Event(STEPS, "end", f"end of day; essential shortage {state.unserved_essential_kwh:.3f} kWh",
                        battery_after=state.battery_kwh, messages=snapshot()))
    trace = _trace(plan, context, battery, requirements, pv, cooking, battery_after, state, decisions, outcomes,
                   admitted)
    return Execution(trace=trace, state=state, events=events, decisions=decisions)


def _trace(plan, context, battery, requirements, pv, cooking, battery_after, state, decisions, outcomes,
           admitted) -> Trace:
    demand = [round(c + e, 6) for c, e in zip(cooking, context.essential_load_kw)]
    not_started = [r for r in plan.reservations if r.accepted and r.reservation_id not in admitted]
    sim_id = short_hash({"plan": plan.model_dump(mode="json"), "scenario": context.scenario_hash, "pv": pv,
                         "battery": battery.model_dump(), "req": requirements.model_dump(), "v": VERSION}, 16)
    return Trace(simulation_id=sim_id, scenario_id=context.scenario_id, config_hash=context.config_hash,
                 scenario_hash=context.scenario_hash, phase="actual", proposal_id=plan.proposal_id,
                 tariff_version=short_hash(sorted(plan.tariff.items()), 10), simulator_version=VERSION,
                 pv_kw=list(pv), cooking_kw=[round(c, 6) for c in cooking], demand_kw=demand,
                 battery_kwh=list(battery_after),
                 overload_steps=[t for t in range(STEPS) if demand[t] > context.inverter_kw + 1e-9],
                 unserved_essential_kwh=round(state.unserved_essential_kwh, 6),
                 unserved_cooking_kwh=round(sum(r.power_kw * (r.end_step - r.start_step) * 0.25
                                                for r in not_started), 6),
                 reservation_outcomes=dict(outcomes),
                 extra={"admission": [dict(d) for d in decisions], "ledger_error": None,
                        "floor_kwh": battery.floor_kwh, "battery_spec": battery.model_dump(),
                        "admitted": len(admitted), "requirements": requirements.model_dump()})


# ------------------------------------------------------------------ acceptance checks
def private(m: HouseholdMessage, context: ScenarioContext, plan: Plan, line: str) -> bool:
    """The message and its rendered line reference only the household's own request/reservation."""
    owner = {r.request_id: r.household_id for r in context.requests}
    if owner.get(m.request_id) != m.household_id:
        return False
    mine = [r for r in plan.reservations if r.request_id == m.request_id]
    if m.reservation_id is not None and m.reservation_id not in {r.reservation_id for r in mine}:
        return False
    others = [r for r in context.requests if r.request_id != m.request_id]
    foreign = {x for r in others for x in (r.request_id, r.household_id)}
    foreign |= {r.reservation_id for r in plan.reservations if r.request_id != m.request_id}
    return not any(x in line or x in m.model_dump_json() for x in foreign)


def run_acceptance() -> dict:
    from greenstove.supervisor import complete_pass
    from greenstove import admission_verifier
    from greenstove.models import ActualScenario, tariff_version

    context, battery, fx = acceptance_scenario()
    requirements = Requirements()
    plan = allocator.allocate(context.requests, TARIFF, context, "acceptance",
                              max_shift_hours=requirements.max_shift_hours)
    run = execute(plan, context, battery, start_step=fx["start_step"], requirements=requirements)
    by_req = {r.request_id: r for r in plan.reservations}
    hh = {r.request_id: r.household_id for r in context.requests}
    final = {m.household_id: m for m in household_outcomes(context, plan, run.trace)}

    def decided(rid):
        return [d for d in run.decisions if d["request_id"] == rid]

    a_done = [e for e in run.events if e.kind == "completed" and e.request_id == "req-A"]
    a_msgs = [(e.step, m.status) for e in run.events for m in e.messages if m.household_id == "hh-A"]
    b_final = [d for d in decided("req-B") if d.get("final")]
    b_last = context.requests[1].latest_finish - context.requests[1].duration_steps
    # Verify the same loop independently, including its explicitly declared late start.
    trace = run.trace
    trace.simulator_version = admission_verifier.SUPPORTED_SIMULATOR_VERSION
    trace.tariff_version = tariff_version(plan.tariff)
    trace.extra["start_step"] = fx["start_step"]
    trace.simulation_id = short_hash({"plan": plan.model_dump(mode="json"), "scenario": context.scenario_hash,
                                     "config": context.config_hash, "phase": "actual", "pv": trace.pv_kw,
                                     "battery": battery.model_dump(), "req": requirements.model_dump(),
                                     "v": trace.simulator_version, "start_step": fx["start_step"]}, 16)
    verdict = admission_verifier.verify(trace, requirements, context, plan, battery,
                                         actual=ActualScenario(actual_pv_kw=[0.0]*STEPS),
                                         start_step=fx["start_step"])
    status = "PLAN_VERIFIED" if complete_pass(verdict) else "NO_VERIFIED_PLAN"
    forged = verdict.model_copy(update={"passed": True,
                                        "coverage": verdict.coverage.model_copy(update={"complete": False})})
    all_msgs = [m for e in run.events for m in e.messages] + list(final.values())
    batteries = {e.step: e.battery_after for e in run.events if e.kind == "interval"}
    a_first = decided("req-A")[0]
    conditions = [
        ("A completed only after step 93 delivered",
         len(a_done) == 1 and a_done[0].step == 94 and "final interval 93" in a_done[0].text
         and all(s == "COMPLETED" for st, s in a_msgs if st >= 94) and "COMPLETED" not in {s for st, s in a_msgs if st < 94}
         and final["hh-A"].status == "COMPLETED"),
        ("B and C never served or completed",
         not any(d["status"] == "ADMIT" for rid in ("req-B", "req-C") for d in decided(rid))
         and all(run.trace.reservation_outcomes.get(by_req[r].reservation_id) != "completed" for r in ("req-B", "req-C"))
         and list(run.state.completed_request_ids) == ["req-A"] and verdict.metrics["completed_sessions"] == 1
         and final["hh-B"].status == "NOT_SERVED" and final["hh-C"].status == "APPROVAL_NEEDED"),
        ("B finalised UNSERVED at its last valid start",
         len(b_final) == 1 and b_final[0]["step"] == b_last and b_final[0]["status"] == "UNSERVED"
         and b_final[0]["reason"] == "INSUFFICIENT_STORED_ENERGY" and final["hh-B"].reason == "INSUFFICIENT_STORED_ENERGY"),
        ("no household sees another's data",
         bool(all_msgs) and all(private(m, context, plan, render_one(m)) for m in all_msgs)
         and all(hh[m.request_id] == m.household_id for m in all_msgs)),
        ("battery 0.40 at 24:00, essential shortage 0.000",
         run.state.step == STEPS and math.isclose(run.state.battery_kwh, 0.4, abs_tol=1e-9)
         and math.isclose(run.state.unserved_essential_kwh, 0.0, abs_tol=1e-12) and min(run.trace.battery_kwh) >= 0.4 - 1e-9),
        ("PLAN_VERIFIED only with coverage.complete=True",
         (status != "PLAN_VERIFIED" or verdict.coverage.complete) and not complete_pass(forged)),
        ("numbers match Abel's hand-calculated fixture",
         a_first["status"] == fx["expected_first_status"] and decided("req-B")[0]["status"] == fx["expected_second_status"]
         and math.isclose(a_first["required_kwh"], fx["expected_first_required_storage_kwh"])
         and math.isclose(batteries[92], fx["expected_after_first_interval_battery_kwh"])
         and math.isclose(batteries[93], fx["expected_after_session_battery_kwh"])
         and math.isclose(run.state.battery_kwh, fx["expected_end_battery_kwh"])),
    ]
    return {"context": context, "plan": plan, "run": run, "verdict": verdict, "status": status,
            "final": final, "conditions": conditions}


def table_rows(result: dict) -> list[tuple[str, str, str, str]]:
    """(step, event, battery after, message for that household only)."""
    rows = []
    for e in result["run"].events:
        when = f"{e.step} ({step_label(e.step)})"
        batt = "" if e.battery_after is None else f"{e.battery_after:.3f}"
        hid = next((r.household_id for r in result["context"].requests if r.request_id == e.request_id), None)
        msg = next((render_one(m) for m in e.messages if m.household_id == hid), "-") if hid else "-"
        rows.append((when, e.text, batt, msg))
    return rows


def report(result: dict) -> tuple[list[str], bool]:
    lines = ["Joint acceptance run | synthetic last-hour fixture (Abel) | REHEARSAL on the admission ledger, "
             "not a verified plan | no PV, efficiencies 1.0, floor 0.4 kWh", ""]
    rows = [("step", "event", "battery", "message (that household only)")] + table_rows(result)
    w = [max(len(r[i]) for r in rows) for i in range(3)]
    lines += [f"{r[0]:<{w[0]}} | {r[1]:<{w[1]}} | {r[2]:>{w[2]}} | {r[3]}" for r in rows]
    v = result["verdict"]
    lines += ["", f"plan status: {result['status']} (verdict passed={v.passed}, coverage.complete={v.coverage.complete}; "
                  f"served {v.metrics['completed_sessions']:.0f} of {v.metrics['requested_sessions']:.0f})", ""]
    ok = True
    for name, passed in result["conditions"]:
        ok = ok and bool(passed)
        lines.append(f"{'PASS' if passed else 'FAIL'}  {name}")
    lines.append("ACCEPTANCE PASS" if ok else "ACCEPTANCE FAIL")
    return lines, ok


def cmd_acceptance(_a=None) -> int:
    lines, ok = report(run_acceptance())
    print("\n".join(lines))
    return 0 if ok else 1


def register(sub) -> None:
    """Add `acceptance` to cli.main()'s argparse subparsers object."""
    s = sub.add_parser("acceptance", help="joint acceptance run (review): last-hour admission, routed household "
                                          "messages, PASS/FAIL per condition; exit 0 only if all pass")
    s.set_defaults(fn=cmd_acceptance)


if __name__ == "__main__":
    sys.exit(cmd_acceptance())
