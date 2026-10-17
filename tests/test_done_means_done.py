"""Done Means Done: definition of done from a one-line goal, drift and loop detection, decision trail."""
from greenstove import note_writer, supervisor
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import Condition, Coverage, Requirements, Verdict
from greenstove.evidence import EvidenceStore
from greenstove.llm import LLMClient, LLMSettings
from greenstove.spec_agent import (DEFAULT_GOAL, SpecCheckOut, SpecOutput, mock_spec_writer, validate,
                                   write_spec)
from tests.test_pricing import ctx
from tests.test_supervisor import FakeEnv

REQ = Requirements(min_service_fraction=0.75, max_shift_hours=4.0)


def test_spec_code_tightens_adds_drops_and_never_loosens():
    out = SpecOutput(checks=[
        SpecCheckOut(metric="served_fraction", op=">=", threshold=0.5),        # looser -> tightened to 0.75
        SpecCheckOut(metric="max_shift_hours", op=">=", threshold=2.0),        # wrong direction -> corrected
        SpecCheckOut(metric="vibes", op=">=", threshold=1.0),                  # unknown -> dropped
        SpecCheckOut(metric="unserved_cooking_kwh", op="<=", threshold=0.0)])  # extra strict check kept
    dod = validate(out, "goal", REQ, "m")
    by = {c.metric: c for c in dod.checks}
    assert by["served_fraction"].threshold == 0.75 and by["served_fraction"].source == "code_tightened"
    assert by["max_shift_hours"].op == "<=" and by["max_shift_hours"].threshold == 2.0     # stricter than 4 kept
    assert by["overload_steps"].source == "code_added" and by["unserved_essential_kwh"].source == "code_added"
    assert any("vibes" in d for d in dod.dropped) and any("direction" in d for d in dod.dropped)
    eff = dod.requirements(REQ)
    assert eff.min_service_fraction == 0.75 and eff.max_shift_hours == 2.0               # stricter of the two


def test_spec_evaluate_flags_failures_and_unchecked_metrics():
    dod = validate(SpecOutput(checks=[SpecCheckOut(metric="unserved_cooking_kwh", op="<=", threshold=0)]), "g", REQ, "m")
    fails = dod.evaluate({"served_fraction": 0.9, "max_shift_hours": 1, "overload_steps": 0,
                          "unserved_essential_kwh": 0, "unserved_cooking_kwh": 0.5})
    assert fails == ["DOD_UNSERVED_COOKING_KWH"]
    assert "DOD_UNCHECKED:unserved_cooking_kwh" in dod.evaluate({"served_fraction": 1})


def test_write_spec_from_one_line_goal_is_cached(tmp_path):
    llm = LLMClient(LLMSettings(mode="mock"), {"spec_writer": mock_spec_writer})
    dod = write_spec(DEFAULT_GOAL, REQ, llm, cache_dir=tmp_path)
    assert {c.source for c in dod.checks} >= {"agent", "code_tightened", "code_added"} and len(llm.calls) == 1
    again = write_spec(DEFAULT_GOAL, REQ, llm, cache_dir=tmp_path)
    assert again.spec_hash() == dod.spec_hash() and len(llm.calls) == 1                 # frozen: no new call


class DriftEnv(FakeEnv):
    """Served share depends on the sun price; every attempt fails min_service 1.0."""
    SERVED = {4.0: 0.9, 5.0: 0.6, 3.0: 0.3}

    def verify(self, trace, requirements, context, plan):
        self.n += 1
        served = self.SERVED[plan.tariff["sun"]]
        return Verdict(run_id=f"{trace.simulation_id}:{self.n}", simulation_id=trace.simulation_id,
                       scenario_id=context.scenario_id, config_hash=context.config_hash,
                       scenario_hash=context.scenario_hash, phase=trace.phase, passed=False,
                       conditions=[Condition(name="service", passed=False, value=served, limit=1.0)],
                       metrics={"served_fraction": served}, feedback_codes=[], verifier_version="fake",
                       requirements=requirements,
                       coverage=Coverage(scope="fake", complete=True, phases_checked=[trace.phase], not_modeled=[]))


def scripted(prices):
    it = iter(prices)
    return lambda p: {"prices": next(it), "rationale": "This tariff will certainly pass."}


def run(tmp_path, env, handler, **kw):
    llm = LLMClient(LLMSettings(mode="mock"), {"pricing": handler, "note_writer": note_writer.mock_note_writer})
    return supervisor.run_agent_day(ctx(), "off", Requirements(min_service_fraction=1.0), env=env, llm=llm,
                                    evidence=EvidenceStore(tmp_path / "ev"), brain=BrainStore(tmp_path / "b"),
                                    write_notes=False, **kw)


def test_drift_is_caught_and_stops(tmp_path):
    seq = [{"sun": s, "standard": 8, "peak": 13} for s in (4, 5, 3, 4)]
    r = run(tmp_path, DriftEnv(), scripted(seq), max_attempts=4)
    assert r.status == "NO_VERIFIED_PLAN" and r.reason.startswith("drifting")
    assert [d.decision for d in r.decisions] == ["re-plan", "re-plan", "stop", "ping_human"]
    assert len(r.attempts) == 3                                    # stopped before using the 4th attempt


def test_model_claims_do_not_set_status(tmp_path):
    r = run(tmp_path, DriftEnv(), scripted([{"sun": 4, "standard": 8, "peak": 13}] + [
        {"sun": 5, "standard": 8, "peak": 13}, {"sun": 3, "standard": 8, "peak": 13}]))
    assert r.status != "PLAN_VERIFIED" and all(not a.passed for a in r.attempts)


def test_decision_trail_continue_stop_and_ping(tmp_path):
    ok = run(tmp_path / "a", FakeEnv(), scripted([{"sun": 4, "standard": 8, "peak": 16}]))
    assert ok.status == "PLAN_VERIFIED" and [d.decision for d in ok.decisions] == ["continue"]
    inf = run(tmp_path / "b", FakeEnv(infeasible=True), scripted([]))
    assert inf.status == "INFEASIBLE" and [d.decision for d in inf.decisions] == ["stop", "ping_human"]
    loop = run(tmp_path / "c", FakeEnv(), scripted([{"sun": 4, "standard": 8, "peak": 13}] * 2))
    assert [d.decision for d in loop.decisions] == ["re-plan", "stop", "ping_human"] and "looping" in loop.decisions[1].reason
