"""Cross-implementation checks of Isaac's simulator against Abel's verifier."""
import copy
from unittest.mock import patch

import pytest

from greenstove import admission_verifier as independent
from greenstove.env_protocol import ActualScenario, ContractError, Requirements, Unserved, with_scenario_hash
from greenstove.scenarios import DEFAULT_REQUIREMENTS, actual_for, scenario
from greenstove.world_adapter import AdmissionWorld, battery_spec

TARIFF = {"standard": 6.0, "sun": 3.0, "peak": 10.0}


def case(name="dev-clear-full", phase="forecast_expected"):
    ctx = scenario(name)
    world = AdmissionWorld(DEFAULT_REQUIREMENTS)
    plan = world.allocate(ctx.requests, TARIFF, ctx, "independent-test")
    actual = actual_for(name) if phase == "actual" else None
    trace = world.simulate(plan, ctx, phase, actual)
    return ctx, plan, trace, actual


def check(ctx, plan, trace, actual=None, requirements=DEFAULT_REQUIREMENTS):
    return independent.verify(trace, requirements, ctx, plan, battery_spec(ctx), actual=actual)


@pytest.mark.parametrize("name", ["dev-clear-full", "dev-partly-cloudy-mid", "dev-overcast-low-battery",
                                  "dev-rainy-afternoon", "dev-rigid-high-demand"])
@pytest.mark.parametrize("phase", ["forecast_expected", "forecast_downside", "actual"])
def test_independent_replay_accepts_consistent_traces(name, phase):
    ctx, plan, trace, actual = case(name, phase)
    before = copy.deepcopy((ctx, plan, trace))
    # Verification must not delegate to the same transitions it is checking.
    with patch("greenstove.admission.can_start", side_effect=AssertionError("shared admission")), \
         patch("greenstove.admission.advance_interval", side_effect=AssertionError("shared simulation")):
        verdict = check(ctx, plan, trace, actual)
    assert verdict.passed == all(c.passed for c in verdict.conditions)
    assert verdict.coverage.complete
    assert before == (ctx, plan, trace)


@pytest.mark.parametrize("field", ["cooking", "battery", "outcome", "admission", "pv", "simulation_id",
                                   "scope", "requirements", "floor", "shortage", "admitted", "tariff"])
def test_tampered_trace_rejected(field):
    ctx, plan, trace, _ = case()
    if field == "cooking":
        trace.cooking_kw = [0.0] * 96
    elif field == "battery":
        trace.battery_kwh = [ctx.battery_capacity_kwh] * 96
    elif field == "outcome":
        trace.reservation_outcomes = {}
    elif field == "admission":
        trace.extra["admission"] = []
    elif field == "pv":
        trace.pv_kw = [0.0] * 96
    elif field == "simulation_id":
        trace.simulation_id = "forged"
    elif field == "scope":
        trace.config_hash = "other"
    elif field == "requirements":
        trace.extra["requirements"] = {}
    elif field == "floor":
        trace.extra["floor_kwh"] = 0
    elif field == "shortage":
        trace.unserved_essential_kwh = 4
    elif field == "admitted":
        trace.extra["admitted"] = 0
    else:
        trace.tariff_version = "forged"
    with pytest.raises(ContractError):
        check(ctx, plan, trace)


def test_original_six_meals_zero_energy_attack_rejected():
    ctx, plan, trace, _ = case()
    assert check(ctx, plan, trace).passed
    trace.cooking_kw = [0.0] * 96
    trace.demand_kw = list(ctx.essential_load_kw)
    trace.pv_kw = [0.0] * 96
    trace.battery_kwh = [ctx.battery_capacity_kwh] * 96
    trace.extra["admission"] = []
    trace.extra["admitted"] = 0
    with pytest.raises(ContractError):
        check(ctx, plan, trace)


def test_actual_requires_separate_authoritative_inputs():
    ctx, plan, trace, actual = case(phase="actual")
    with pytest.raises(ContractError):
        check(ctx, plan, trace)
    assert check(ctx, plan, trace, actual).phase == "actual"
    with pytest.raises(ContractError):
        check(ctx, plan, trace, ActualScenario(actual_pv_kw=[0.0] * 96))


@pytest.mark.parametrize("kind", ["unknown", "reserved", "duplicate", "bad_reason"])
def test_forged_unserved_rejected(kind):
    ctx, plan, trace, _ = case()
    reserved = plan.reservations[0].request_id
    if kind == "unknown":
        plan.unserved = [Unserved(request_id="unknown", reason="UNSERVED_BUDGET")]
    elif kind == "reserved":
        plan.unserved = [Unserved(request_id=reserved, reason="UNSERVED_BUDGET")]
    else:
        # Remove an existing pending offer to create a valid unreserved reference.
        rid = next(r.request_id for r in plan.reservations if not r.accepted)
        plan.reservations = [r for r in plan.reservations if r.request_id != rid]
        plan.unserved = [Unserved(request_id=rid, reason="FAKE" if kind == "bad_reason" else "UNSERVED_BUDGET")]
        if kind == "duplicate":
            plan.unserved *= 2
    with pytest.raises(ContractError):
        check(ctx, plan, trace)


def test_budget_unserved_is_informational_on_passing_day():
    ctx = scenario("dev-clear-full").model_copy(deep=True)
    for q in ctx.requests:
        if not q.auto_book:
            q.max_cost = 0
    ctx = with_scenario_hash(ctx)
    world = AdmissionWorld(DEFAULT_REQUIREMENTS)
    plan = world.allocate(ctx.requests, TARIFF, ctx, "budget")
    verdict = check(ctx, plan, world.simulate(plan, ctx, "forecast_expected"))
    assert verdict.passed
    assert [f.code for f in verdict.feedback_codes] == ["UNSERVED_BUDGET", "UNSERVED_BUDGET"]


def test_configuration_changes_for_battery_policy_verifier_and_requirements(monkeypatch):
    ctx = scenario("dev-clear-full")
    spec = battery_spec(ctx)
    base = independent.configuration_hash(ctx, spec, DEFAULT_REQUIREMENTS)
    assert independent.configuration_hash(ctx, spec.model_copy(update={"charge_efficiency": .9}), DEFAULT_REQUIREMENTS) != base
    assert independent.configuration_hash(ctx, spec, Requirements()) != base
    with monkeypatch.context() as m:
        m.setattr(independent, "POLICY_VERSION", "changed")
        assert independent.configuration_hash(ctx, spec, DEFAULT_REQUIREMENTS) != base
    with monkeypatch.context() as m:
        m.setattr(independent, "ADMISSION_VERIFIER_VERSION", "changed")
        assert independent.configuration_hash(ctx, spec, DEFAULT_REQUIREMENTS) != base


def test_requirements_are_detached_and_affect_run_identity():
    ctx, plan, trace, _ = case()
    req = DEFAULT_REQUIREMENTS.model_copy(deep=True)
    verdict = check(ctx, plan, trace, requirements=req)
    verdict.requirements.max_shift_hours = 0
    assert req.max_shift_hours == DEFAULT_REQUIREMENTS.max_shift_hours
    strict = req.model_copy(update={"max_shift_hours": 1.0})
    with pytest.raises(ContractError):
        check(ctx, plan, trace, requirements=strict)


def test_verified_world_composes_with_supervisor_protocol():
    from greenstove.admission_world import AdmissionWorld as VerifiedWorld
    ctx = scenario("dev-clear-full")
    world = VerifiedWorld(DEFAULT_REQUIREMENTS)
    plan = world.allocate(ctx.requests, TARIFF, ctx, "verified-world")
    for phase in ("forecast_expected", "forecast_downside", "actual"):
        actual = actual_for("dev-clear-full") if phase == "actual" else None
        trace = world.simulate(plan, ctx, phase, actual)
        verdict = world.verify(trace, DEFAULT_REQUIREMENTS, ctx, plan)
        assert verdict.passed and verdict.verifier_version == independent.ADMISSION_VERIFIER_VERSION
        trace.cooking_kw = [0.0] * 96
        with pytest.raises(ContractError):
            world.verify(trace, DEFAULT_REQUIREMENTS, ctx, plan)


def test_actual_authority_not_shared_between_world_instances():
    from greenstove.admission_world import AdmissionWorld as VerifiedWorld
    ctx = scenario("dev-clear-full")
    world = VerifiedWorld(DEFAULT_REQUIREMENTS)
    plan = world.allocate(ctx.requests, TARIFF, ctx, "actual-world")
    trace = world.simulate(plan, ctx, "actual", actual_for("dev-clear-full"))
    with pytest.raises(ContractError):
        VerifiedWorld(DEFAULT_REQUIREMENTS).verify(trace, DEFAULT_REQUIREMENTS, ctx, plan)


def test_independent_essential_shortage_with_no_requests():
    ctx = scenario("dev-clear-full").model_copy(update={"requests": [], "initial_battery_kwh": 1.2,
                                                     "forecast_pv_kw": [0.0] * 96,
                                                     "downside_pv_kw": [0.0] * 96})
    ctx = with_scenario_hash(ctx)
    world = AdmissionWorld(DEFAULT_REQUIREMENTS)
    plan = world.allocate([], TARIFF, ctx, "essential")
    verdict = check(ctx, plan, world.simulate(plan, ctx, "forecast_expected"))
    assert not verdict.passed
    assert verdict.metrics["served_fraction"] == 1.0
    assert verdict.metrics["unserved_essential_kwh"] == pytest.approx(sum(ctx.essential_load_kw)*.25)
    assert {f.code for f in verdict.feedback_codes} == {"ESSENTIAL_UNSERVED"}


def test_supervisor_reaches_actual_with_independent_world(tmp_path):
    from greenstove import supervisor
    from greenstove.admission_world import AdmissionWorld as VerifiedWorld
    from greenstove.evidence import EvidenceStore
    from greenstove.llm import LLMClient, LLMSettings
    ctx = scenario("dev-clear-full")
    client = LLMClient(LLMSettings(mode="mock"), {"pricing": lambda payload: {"prices": TARIFF}})
    run = supervisor.run_agent_day(ctx, "off", DEFAULT_REQUIREMENTS, env=VerifiedWorld(),
                                   llm=client, evidence=EvidenceStore(tmp_path),
                                   actual=actual_for("dev-clear-full"), write_notes=False)
    assert run.status == "PLAN_VERIFIED"
    assert run.execution and run.execution.outcome_passed
    assert run.execution.metrics["completed_sessions"] == 6
