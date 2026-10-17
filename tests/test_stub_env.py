"""Hand-calculated fixtures for the development stub environment (not Abel's simulator)."""
from __future__ import annotations

import pytest

from greenstove import scenarios as sc
from greenstove.env_protocol import Environment, HouseholdRequest, load_environment
from greenstove.stub_env import CHARGE_EFF, DISCHARGE_EFF, STUB_NAME, StubEnvironment

ENV = StubEnvironment()
TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}
PHASES = ("forecast_expected", "forecast_downside")


def req(rid="r1", power=1.0, dur=2, pref=44, early=None, late=None, max_cost=100.0,
        penalty=1.0, auto=True) -> HouseholdRequest:
    early = pref if early is None else early
    late = pref + dur if late is None else late
    return HouseholdRequest(request_id=rid, household_id=f"h-{rid}", power_kw=power,
                            duration_steps=dur, preferred_start=pref, earliest_start=early,
                            latest_finish=late, max_cost=max_cost, shift_penalty=penalty,
                            auto_book=auto)


def ctx(requests, pv=0.0, battery=6.0, essential=0.0, sid="fixture"):
    base = sc.make_context(1, "clear", 1.0, scenario_id=sid)
    return base.model_copy(update={
        "requests": requests, "forecast_pv_kw": [pv] * 96, "downside_pv_kw": [pv] * 96,
        "essential_load_kw": [essential] * 96, "initial_battery_kwh": battery})


def run(context, tariff=TARIFF, phase="forecast_expected", requirements=None):
    plan = ENV.allocate(context.requests, tariff, context, "p1")
    trace = ENV.simulate(plan, context, phase)
    return plan, trace, ENV.verify(trace, requirements or sc.DEFAULT_REQUIREMENTS, context, plan)


def test_labeled_stub_implements_protocol():
    assert ENV.name == STUB_NAME and "not Abel's simulator" in ENV.name
    assert isinstance(ENV, Environment)
    assert isinstance(load_environment(prefer_stub=True), StubEnvironment)


def test_one_kw_half_hour_uses_half_kwh_and_costs_half_price():
    c = ctx([req(power=1.0, dur=2, pref=44)])                    # 11:00, sun window
    plan, trace, _ = run(c)
    (r,) = plan.reservations
    assert (r.start_step, r.end_step, r.locked_cost) == (44, 46, 2.0)  # 0.5 kWh * 4
    assert trace.extra["served_cooking_kwh"] == pytest.approx(0.5)
    assert trace.battery_kwh[-1] == pytest.approx(6.0 - 0.5 / DISCHARGE_EFF)
    assert r.reservation_id == "p1:r1" and r.tariff_version


def test_cross_tier_quote_integrates_each_interval():
    c = ctx([req(power=1.0, dur=4, pref=62)])                    # 2 steps sun, 2 steps peak
    (r,) = run(c)[0].reservations
    assert r.locked_cost == pytest.approx(0.25 * (4 + 4 + 13 + 13))   # 8.5


def test_two_kw_session_doubles_interval_load():
    t1 = run(ctx([req(power=1.0)], essential=0.12))[1]
    t2 = run(ctx([req(power=2.0)], essential=0.12))[1]
    assert t1.demand_kw[44] == pytest.approx(1.12) and t2.demand_kw[44] == pytest.approx(2.12)
    assert t2.extra["served_cooking_kwh"] == pytest.approx(2 * t1.extra["served_cooking_kwh"])
    assert run(ctx([req(power=2.0)]))[0].reservations[0].locked_cost == 4.0


def test_overlapping_high_power_requests_shift_or_hit_inverter_limit():
    flexible = [req("a", 2.0, pref=48, early=44, late=56), req("b", 2.0, pref=48, early=44, late=56)]
    plan, trace, verdict = run(ctx(flexible, essential=0.12))
    a, b = sorted(plan.reservations, key=lambda r: r.request_id)
    assert a.start_step == 48 and b.start_step in (46, 50)        # nearest non-overlapping start
    assert not trace.overload_steps and verdict.conditions[0].passed
    rigid = [req("a", 2.0, pref=48), req("b", 2.0, pref=48)]
    plan, _, verdict = run(ctx(rigid, essential=0.12))
    assert [u.reason for u in plan.unserved] == ["INVERTER_LIMIT"]
    assert any(f.code == "INVERTER_LIMIT" and f.request_id == "b" for f in verdict.feedback_codes)


def test_budget_rejection():
    plan, _, verdict = run(ctx([req(power=1.0, max_cost=1.99)]))  # only start costs 2.00
    assert plan.reservations == [] and plan.unserved[0].reason == "BUDGET_EXCEEDED"
    assert {"BUDGET_EXCEEDED", "SERVICE_SHORTFALL"} <= {f.code for f in verdict.feedback_codes}


def test_household_prefers_cheaper_start_when_shift_is_worth_it():
    r = req(power=2.0, dur=2, pref=64, early=56, late=66, penalty=1.0)  # peak 13 vs sun 4
    assert run(ctx([r]))[0].reservations[0].start_step == 62     # saves 9 for 0.5 h shift


def test_pending_offer_is_not_dispatched_or_served():
    plan, trace, verdict = run(ctx([req(auto=False)]))
    assert plan.reservations[0].accepted is False
    assert trace.reservation_outcomes == {} and trace.demand_kw[44] == 0.0
    assert verdict.metrics["completed_sessions"] == 0 and not verdict.passed


def test_full_battery_curtails_without_creating_energy():
    c = ctx([req(power=1.0)], pv=2.0, battery=6.0, essential=0.12)
    trace = run(c)[1]
    assert max(trace.battery_kwh) <= 6.0 + 1e-9
    pv_used = 0.12 * 0.25 * 96 + trace.extra["served_cooking_kwh"]
    assert trace.extra["pv_kwh"] == pytest.approx(48.0)
    assert trace.extra["curtailed_kwh"] == pytest.approx(48.0 - pv_used)


@pytest.mark.parametrize("name", ["dev-clear-full", "dev-overcast-low-battery"])
def test_energy_balance(name):
    c = sc.scenario(name)
    plan = ENV.allocate(c.requests, {"standard": 8.0, "sun": 3.0, "peak": 13.0}, c, "p1")
    for phase in PHASES:
        trace = ENV.simulate(plan, c, phase)
        x, end = trace.extra, trace.battery_kwh[-1]
        assert end == pytest.approx(c.initial_battery_kwh + x["pv_to_battery_kwh"] * CHARGE_EFF
                                    - x["battery_out_kwh"], abs=1e-5)
        assert x["pv_kwh"] >= x["pv_to_battery_kwh"] + x["curtailed_kwh"] - 1e-6


def test_low_battery_no_pv_interrupts_cooking_but_serves_essential():
    c = ctx([req(power=1.0, dur=2, pref=4, max_cost=100)], pv=0.0, battery=1.4, essential=0.04)
    plan, trace, verdict = run(c)
    rid = plan.reservations[0].reservation_id
    assert trace.reservation_outcomes[rid] == "interrupted"
    assert trace.unserved_cooking_kwh == pytest.approx(0.5)
    assert trace.unserved_essential_kwh == 0.0                    # essential may go below reserve
    assert min(trace.battery_kwh) < c.reserve_kwh
    cond = {x.name: x.passed for x in verdict.conditions}
    assert cond["battery_reserve"] is False and cond["essential"] is True
    fb = [f for f in verdict.feedback_codes if f.code == "BATTERY_RESERVE"]
    assert fb[0].request_id == "r1" and fb[0].interval == 4 and fb[0].limit == 1.2


def test_empty_battery_reports_unserved_essential():
    trace = run(ctx([], pv=0.0, battery=0.5, essential=0.12))[1]
    assert trace.unserved_essential_kwh == pytest.approx(2.88 - 0.5 * DISCHARGE_EFF)


def test_lower_bound_proof_only_for_infeasible_fixture():
    proof = ENV.lower_bound(sc.infeasible_context(), sc.DEFAULT_REQUIREMENTS)
    assert proof is not None and proof.lower_bound_kwh > proof.available_kwh + 2.0
    assert proof.assumptions
    assert ENV.lower_bound(sc.scenario("dev-clear-full"), sc.DEFAULT_REQUIREMENTS) is None


def test_deterministic_plans_and_verdicts():
    c = sc.scenario("dev-overcast-low-battery")
    p1, t1, v1 = run(c)
    p2, t2, v2 = run(sc.scenario("dev-overcast-low-battery"))
    assert p1 == p2 and t1 == t2
    assert v1.model_dump(exclude={"run_id"}) == v2.model_dump(exclude={"run_id"})
    # identical inputs give the same deterministic evaluation id (contract: Verdict.run_id)
    assert v1.run_id == v2.run_id and v1.run_id.startswith(f"{c.scenario_id}:p1:forecast_expected:")


def test_actual_differs_from_forecast_and_is_seeded():
    c = sc.scenario("dev-clear-full")
    a1, a2 = sc.actual_for("dev-clear-full"), sc.actual_for("dev-clear-full")
    assert a1 == a2 and a1.actual_pv_kw != c.forecast_pv_kw
    for f, a in zip(c.forecast_pv_kw, a1.actual_pv_kw):
        assert 0.8 * f - 1e-9 <= a <= 1.2 * f + 1e-9
    plan = ENV.allocate(c.requests, TARIFF, c, "p1")
    assert ENV.simulate(plan, c, "actual", a1).pv_kw == a1.actual_pv_kw
    with pytest.raises(ValueError):
        ENV.simulate(plan, c, "actual")


def test_config_windows_and_lattice():
    names = list(sc.DEV_SCENARIOS) + list(sc.DEMO_SCENARIOS)
    assert len({sc.scenario(n).config_hash for n in names}) == 1
    c = sc.scenario("dev-clear-full")
    steps = [t for w in c.windows for t in range(w.start_step, w.end_step)]
    assert steps == list(range(96)) and len(c.windows) == 3
    assert len(sc.lattice_tariffs(c)) == 27
    assert sum(r.auto_book for r in c.requests) == 6 and len(c.requests) == 8
    with pytest.raises(ValueError):
        ENV.allocate(c.requests, {"sun": 3.0, "peak": 10.0}, c, "bad")


def _passing(context):
    out = []
    for i, t in enumerate(sc.lattice_tariffs(context)):
        plan = ENV.allocate(context.requests, t, context, f"p{i}")
        if all(ENV.verify(ENV.simulate(plan, context, ph), sc.DEFAULT_REQUIREMENTS, context,
                          plan).passed for ph in PHASES):
            out.append((t["standard"], t["sun"], t["peak"]))
    return out


def test_lattice_brute_force_properties():
    cheapest = (6.0, 3.0, 10.0)
    assert _passing(sc.scenario("dev-clear-full"))
    low = _passing(sc.scenario("dev-overcast-low-battery"))
    assert low and cheapest not in low                           # price steering matters
    assert _passing(sc.scenario("dev-rigid-high-demand")) == []  # no verified plan (not energy)
    assert _passing(sc.infeasible_context()) == []
