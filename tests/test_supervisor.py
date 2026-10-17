"""Supervisor tests against a tiny fake world (passes only when peak price >= 16)."""
import pytest

from greenstove import note_writer, pricing_agent, supervisor
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import (Condition, Coverage, Feedback, InfeasibilityProof, Plan, Requirements,
                                     Reservation, Trace, Verdict)
from greenstove.evidence import EvidenceStore
from greenstove.llm import LLMClient, LLMSettings
from tests.test_pricing import ctx

REQ = Requirements(min_service_fraction=0.75)


class FakeEnv:
    name = "fake-env (tests)"

    def __init__(self, infeasible=False, complete=True):
        self.infeasible = infeasible
        self.complete, self.scope = complete, "fake-full" if complete else "fake-power-only"
        self.n = 0

    def household_requests(self, context):
        return context.requests

    def allocate(self, requests, tariff, context, proposal_id):
        r = requests[0]
        return Plan(proposal_id=proposal_id, tariff=dict(tariff), reservations=[Reservation(
            reservation_id=f"{proposal_id}:{r.request_id}", request_id=r.request_id, start_step=60, end_step=62,
            power_kw=r.power_kw, locked_cost=round(0.5 * tariff["sun"], 2), tariff_version=str(tariff["peak"]),
            accepted=True)])

    def simulate(self, plan, context, phase, actual=None):
        peak = float(plan.reservations[0].tariff_version)
        return Trace(simulation_id=f"{plan.proposal_id}:{phase}", scenario_id=context.scenario_id,
                     config_hash=context.config_hash, scenario_hash=context.scenario_hash, phase=phase,
                     proposal_id=plan.proposal_id, tariff_version="t", simulator_version="fake",
                     pv_kw=[1.0] * 96, cooking_kw=[0.5] * 96, demand_kw=[0.5] * 96,
                     battery_kwh=[2.0 if peak >= 16 else 1.0] * 96)

    def verify(self, trace, requirements, context, plan):
        self.n += 1
        ok = min(trace.battery_kwh) >= context.reserve_kwh
        fb = [] if ok else [Feedback(code="BATTERY_RESERVE", interval=76, value=1.0, limit=context.reserve_kwh)]
        return Verdict(run_id=f"{context.scenario_id}:{trace.proposal_id}:{trace.phase}:{self.n}",
                       simulation_id=trace.simulation_id, scenario_id=context.scenario_id,
                       config_hash=context.config_hash, scenario_hash=context.scenario_hash, phase=trace.phase,
                       passed=ok, conditions=[Condition(name="battery_reserve", passed=ok)],
                       metrics={"min_battery_kwh": min(trace.battery_kwh), "served_fraction": 1.0},
                       feedback_codes=fb, verifier_version="fake", requirements=requirements,
                       coverage=Coverage(scope=self.scope, complete=self.complete, phases_checked=[trace.phase],
                                         not_modeled=[]))

    def lower_bound(self, context, requirements):
        return InfeasibilityProof(lower_bound_kwh=9.0, available_kwh=5.0, assumptions=["test"]) if self.infeasible else None


def llm(pricing=None, **kw):
    return LLMClient(LLMSettings(mode="mock", **kw), {"pricing": pricing or pricing_agent.mock_pricing,
                                                      "note_writer": note_writer.mock_note_writer})


@pytest.fixture
def stores(tmp_path):
    return EvidenceStore(tmp_path / "ev"), BrainStore(tmp_path / "brain")


def run(c, mode, stores, env=None, client=None, **kw):
    ev, brain = stores
    return supervisor.run_agent_day(c, mode, REQ, env=env or FakeEnv(), llm=client or llm(), evidence=ev,
                                    brain=brain, **kw)


def test_feedback_leads_to_verified_plan_with_evidence(stores):
    r = run(ctx(), "off", stores)
    assert r.status == "PLAN_VERIFIED" and len(r.attempts) == 2
    assert r.attempts[0].feedback == ["BATTERY_RESERVE"] and r.selected_tariff["peak"] == 16
    assert r.candidate_evaluations == 2 and r.simulator_runs == 4
    assert len(stores[0].all()) == 4 and all(c.outcome == "ok" for c in r.calls if c.role == "pricing")


def test_repeated_proposal_stops_honestly(stores):
    same = lambda p: {"prices": {"sun": 4, "standard": 8, "peak": 13}}  # noqa: E731
    r = run(ctx(), "off", stores, client=llm(same), write_notes=False)
    assert r.status == "NO_VERIFIED_PLAN" and r.reason.startswith("repeated_proposal")


def test_invalid_proposals_consume_attempts(stores):
    bad = lambda p: {"prices": {"sun": 4.5, "standard": 8, "peak": 13}}  # noqa: E731
    r = run(ctx(), "off", stores, client=llm(bad), write_notes=False)
    assert r.status == "NO_VERIFIED_PLAN" and len(r.attempts) == 3 and r.candidate_evaluations == 0
    assert all(a.feedback == ["INVALID_TARIFF"] for a in r.attempts)


def test_infeasible_is_proved_not_guessed_and_costs_no_calls(stores):
    r = run(ctx(), "curated", stores, env=FakeEnv(infeasible=True))
    assert r.status == "INFEASIBLE" and r.calls == [] and r.infeasibility["lower_bound_kwh"] == 9.0


def test_budget_exhaustion_is_an_error_not_infeasibility(stores):
    r = run(ctx(), "off", stores, client=llm(max_calls=1), write_notes=False)
    assert r.status == "ERROR" and "model_error" in r.reason


def test_requirements_are_never_relaxed(stores):
    before = REQ.model_dump()
    run(ctx(), "off", stores)
    assert REQ.model_dump() == before


def test_development_notes_reach_a_fresh_agent(stores):
    ev, brain = stores
    first = run(ctx(), "off", stores)
    assert any(n.action == "added" for n in first.notes)
    fresh = run(ctx(), "curated", stores, write_notes=False)
    assert fresh.brief.entry_ids and fresh.status == "PLAN_VERIFIED" and len(fresh.attempts) == 1
    assert fresh.attempts[0].proposal.cited_observation_ids


def test_evaluation_quarantines_notes(stores):
    ev, brain = stores
    run(ctx(), "off", stores)
    n_before = len(brain.entries())
    r = run(ctx(), "curated", stores, phase="evaluation", write_notes=True)
    assert r.notes == [] and len(brain.entries()) == n_before and r.quarantined_notes


def test_decision_record_and_run_folder(stores):
    from greenstove.decision import brief_lines, render_decision
    ev, _ = stores
    c = ctx()
    r = run(c, "off", stores)
    files = {p.name for p in ev.run_dir(r.agent_run_id).iterdir()}
    assert {"requests.json", "tariff_1.json", "reservations_1.json", "verdict_1_forecast_expected.json",
            "status.json", "run.json"} <= files
    text = render_decision(c, r, FakeEnv(), REQ, brief_lines(r.brief.text))
    assert "Status: PLAN_VERIFIED" in text and "Today's tariff" in text and "Alternatives rejected" in text
    assert "Not decided by the model" in text


def test_incomplete_coverage_pass_is_never_plan_verified(stores):
    r = run(ctx(), "off", stores, env=FakeEnv(complete=False), write_notes=False)
    assert r.status == "NO_VERIFIED_PLAN" and "coverage is incomplete" in r.reason
    assert "INCOMPLETE_COVERAGE" in r.attempts[-1].feedback
