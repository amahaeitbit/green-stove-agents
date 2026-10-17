"""The real selector, supervisor and acceptance loop must use independent evidence."""
import json

import pytest

from greenstove import acceptance, admission_verifier, cli, comparators, supervisor
from greenstove.admission_world import AdmissionWorld
from greenstove.env_protocol import load_environment
from greenstove.evidence import EvidenceStore
from greenstove.household_messages import household_outcomes
from greenstove.llm import LLMClient, LLMSettings
from greenstove.models import ActualScenario, ContractError
from greenstove.scenarios import DEFAULT_REQUIREMENTS, actual_for, scenario
from greenstove.world_adapter import battery_spec

TARIFF = {"standard": 6.0, "sun": 3.0, "peak": 10.0}


def fixed_llm():
    return LLMClient(LLMSettings(mode="mock"), {"pricing": lambda payload: {"prices": TARIFF}})


def test_actual_loader_and_memory_identity_use_independent_versions(monkeypatch):
    monkeypatch.setenv("GREENSTOVE_WORLD", "abel-admission")
    world = load_environment()
    assert isinstance(world, AdmissionWorld)
    ctx = cli.context_for("dev-clear-full")
    base = scenario("dev-clear-full")
    assert ctx.config_hash == admission_verifier.configuration_hash(base, battery_spec(base), DEFAULT_REQUIREMENTS)
    monkeypatch.setattr(admission_verifier, "POLICY_VERSION", "new-policy")
    assert cli.context_for("dev-clear-full").config_hash != ctx.config_hash


def test_production_loop_finalizes_deferred_and_routes_private_events():
    ctx = scenario("dev-overcast-low-battery")
    world = AdmissionWorld(DEFAULT_REQUIREMENTS)
    plan = world.allocate(ctx.requests, TARIFF, ctx, "routed")
    trace = world.simulate(plan, ctx, "actual", actual_for("dev-overcast-low-battery"))
    verdict = world.verify(trace, DEFAULT_REQUIREMENTS, ctx, plan)
    assert verdict.verifier_version == admission_verifier.ADMISSION_VERIFIER_VERSION
    energy_deferred = [d for d in trace.extra["admission"] if d["status"] == "DEFER" and d["reason"] != "APPROVAL_REQUIRED"]
    assert energy_deferred
    final = {m.request_id: m for m in household_outcomes(ctx, plan, trace)}
    for d in energy_deferred:
        assert trace.reservation_outcomes[d["reservation_id"]] == "unserved"
        assert final[d["request_id"]].status == "NOT_SERVED"
    owner = {q.request_id: q.household_id for q in ctx.requests}
    booked = {r.request_id: r for r in plan.reservations}
    for event in trace.extra["household_events"]:
        for message in event["messages"]:
            assert message["request_id"] == event["request_id"]
            assert message["household_id"] == owner[event["request_id"]]
            if message["status"] == "COMPLETED":
                assert event["step"] == booked[event["request_id"]].end_step


def test_supervisor_saves_trace_and_actual_only_household_outbox(tmp_path):
    world, ctx = AdmissionWorld(), scenario("dev-clear-full")
    evidence = EvidenceStore(tmp_path)
    run = supervisor.run_agent_day(ctx, "off", DEFAULT_REQUIREMENTS, env=world, llm=fixed_llm(),
                                   evidence=evidence, actual=actual_for("dev-clear-full"), write_notes=False)
    assert run.execution and run.execution.outcome_passed
    folder = evidence.run_dir(run.agent_run_id)
    assert (folder / "trace_1_forecast_expected.json").exists()
    assert (folder / "trace_1_forecast_downside.json").exists()
    assert json.loads((folder / "trace_actual.json").read_text())["simulator_version"] == admission_verifier.SUPPORTED_SIMULATOR_VERSION
    assert json.loads((folder / "verdict_actual.json").read_text())["verifier_version"] == admission_verifier.ADMISSION_VERIFIER_VERSION
    assert len(json.loads((folder / "household_outcomes.json").read_text())) == len(ctx.requests)
    assert json.loads((folder / "household_events.json").read_text())
    assert not list(folder.glob("*household*forecast*"))


def test_acceptance_fixture_now_independently_verified_and_tampering_rejected():
    result = acceptance.run_acceptance()
    ctx, spec, fx = acceptance.acceptance_scenario()
    assert result["verdict"].verifier_version == admission_verifier.ADMISSION_VERIFIER_VERSION
    assert all(ok for _, ok in result["conditions"])
    trace = result["run"].trace.model_copy(deep=True)
    trace.cooking_kw = [0.0] * 96
    with pytest.raises(ContractError):
        admission_verifier.verify(trace, result["verdict"].requirements, ctx, result["plan"], spec,
                                  actual=ActualScenario(actual_pv_kw=[0.0]*96), start_step=fx["start_step"])


class CorruptWorld(AdmissionWorld):
    def simulate(self, *args, **kwargs):
        trace = super().simulate(*args, **kwargs)
        trace.battery_kwh = [999.0] * 96
        return trace


def test_verifier_error_is_not_downgraded_to_unmodeled_phase(tmp_path):
    ctx = scenario("dev-clear-full")
    with pytest.raises(ContractError):
        supervisor.run_agent_day(ctx, "off", DEFAULT_REQUIREMENTS, env=CorruptWorld(), llm=fixed_llm(),
                                  evidence=EvidenceStore(tmp_path), write_notes=False)
    with pytest.raises(ContractError):
        comparators.run_comparator("F3", ctx, [TARIFF], DEFAULT_REQUIREMENTS, env=CorruptWorld())


def test_outbox_reconstructed_instead_of_trusting_unverified_event_metadata(tmp_path):
    class WrongMetadataWorld(AdmissionWorld):
        def simulate(self, *args, **kwargs):
            trace = super().simulate(*args, **kwargs)
            trace.extra["household_events"] = [{"request_id": "foreign-household", "messages": []}]
            return trace
    ctx, evidence = scenario("dev-clear-full"), EvidenceStore(tmp_path)
    run = supervisor.run_agent_day(ctx, "off", DEFAULT_REQUIREMENTS, env=WrongMetadataWorld(), llm=fixed_llm(),
                                   evidence=evidence, actual=actual_for("dev-clear-full"), write_notes=False)
    messages = json.loads((evidence.run_dir(run.agent_run_id) / "household_events.json").read_text())
    known = {q.request_id for q in ctx.requests}
    assert messages and all(e["request_id"] in known for e in messages)
