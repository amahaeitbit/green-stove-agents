"""Joint acceptance run (integration review) and B1: no stale-snapshot double admit on the app path."""
import argparse

import pytest

from greenstove import acceptance, admission, allocator
from greenstove.acceptance import TARIFF, acceptance_scenario, execute
from greenstove.models import ContractError, Requirements


def test_every_acceptance_condition_passes():
    result = acceptance.run_acceptance()
    assert [name for name, ok in result["conditions"] if not ok] == []
    assert len(result["conditions"]) == 7
    lines, ok = acceptance.report(result)
    assert ok and lines[-1] == "ACCEPTANCE PASS"
    assert result["status"] == "NO_VERIFIED_PLAN"            # a rehearsal with B unserved is never PLAN_VERIFIED


def test_table_matches_the_review():
    rows = acceptance.table_rows(acceptance.run_acceptance())
    events = [r[1] for r in rows]
    assert events[0].startswith("req-A ADMIT RESOURCES_COMMITTED (needs 0.600, has 0.600)")
    assert events[1].startswith("req-B DEFER INSUFFICIENT_STORED_ENERGY (needs 1.100, has 0.600)")
    assert events[2] == "req-C DEFER APPROVAL_REQUIRED"
    assert [r[2] for r in rows if r[2]] == ["0.725", "0.450", "0.425", "0.400", "0.400"]
    assert rows[0][3].startswith("hh-A: start now") and rows[1][3].startswith("hh-B: wait")
    assert rows[2][3].startswith("hh-C: approval needed")


def test_register_adds_the_acceptance_command(capsys):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    acceptance.register(sub)
    a = p.parse_args(["acceptance"])
    assert a.fn(a) == 0
    out = capsys.readouterr().out
    assert "ACCEPTANCE PASS" in out and "FAIL " not in out and "REHEARSAL" in out


@pytest.fixture()
def setup():
    context, battery, fx = acceptance_scenario()
    plan = allocator.allocate(context.requests, TARIFF, context, "b1", max_shift_hours=3.0)
    return context, battery, fx, plan


def test_b1_single_loop_carries_state_so_no_stale_double_admit(setup):
    context, battery, fx, plan = setup
    a, b = (next(r for r in plan.reservations if r.request_id == rid) for rid in ("req-A", "req-B"))
    stale = admission.initial_state(context, battery, start_step=92)
    # The hazard the review describes: two calls on the same old snapshot both admit.
    assert admission.can_start(a, plan.tariff, context, stale, battery).status == "ADMIT"
    assert admission.can_start(b, plan.tariff, context, stale, battery).status == "ADMIT"
    # Through the app path the loop owns the ledger: B is judged on the state that includes A.
    run = execute(plan, context, battery, start_step=92, requirements=Requirements())
    admits = [d for d in run.decisions if d["status"] == "ADMIT"]
    assert [d["request_id"] for d in admits] == ["req-A"]
    first_b = next(d for d in run.decisions if d["request_id"] == "req-B")
    assert first_b["status"] == "DEFER" and abs(first_b["required_kwh"] - 1.1) < 1e-9
    assert run.state.admitted_reservation_ids == (a.reservation_id,)
    assert run.trace.extra["admitted"] == 1


def test_b1_second_admission_of_the_same_request_raises(setup):
    context, battery, fx, plan = setup
    a = next(r for r in plan.reservations if r.request_id == "req-A")
    twice = plan.model_copy(update={"reservations": [a, a.model_copy(update={"reservation_id": "b1:req-A-again"})]})
    with pytest.raises(ContractError):                # refused before any second ADMIT can be recorded
        execute(twice, context, battery, start_step=92, requirements=Requirements())
    run = execute(plan, context, battery, start_step=92, requirements=Requirements())
    with pytest.raises(ContractError, match="already admitted"):     # the carried ledger remembers the admit
        admission.can_start(a, plan.tariff, context, run.state, battery)


def test_execute_accepts_no_caller_state():
    import inspect
    params = inspect.signature(execute).parameters
    assert "state" not in params and not any("state" in p for p in params)
