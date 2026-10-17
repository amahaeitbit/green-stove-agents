"""Per-household routing (review A3): statuses, privacy and SMS-length rendering."""
import pytest

from greenstove.acceptance import TARIFF, acceptance_scenario, execute, private
from greenstove import allocator
from greenstove.household_messages import HouseholdMessage, household_outcomes, message_for, render, render_one
from greenstove.models import ContractError, Plan, Requirements, Unserved


@pytest.fixture(scope="module")
def day():
    context, battery, fx = acceptance_scenario()
    plan = allocator.allocate(context.requests, TARIFF, context, "p", max_shift_hours=3.0)
    run = execute(plan, context, battery, start_step=fx["start_step"], requirements=Requirements())
    return context, plan, run


def test_one_message_per_household_with_final_statuses(day):
    context, plan, run = day
    msgs = household_outcomes(context, plan, run.trace)
    assert [m.household_id for m in msgs] == ["hh-A", "hh-B", "hh-C"]
    by = {m.household_id: m for m in msgs}
    assert (by["hh-A"].status, by["hh-A"].reason) == ("COMPLETED", "RESOURCES_COMMITTED")
    assert (by["hh-B"].status, by["hh-B"].reason) == ("NOT_SERVED", "INSUFFICIENT_STORED_ENERGY")
    assert (by["hh-C"].status, by["hh-C"].reason) == ("APPROVAL_NEEDED", "APPROVAL_REQUIRED")
    a = by["hh-A"]
    assert (a.request_id, a.reservation_id, a.start, a.end) == ("req-A", "p:req-A", "23:00", "23:30")
    assert a.locked_quote == next(r.locked_cost for r in plan.reservations if r.request_id == "req-A")


def test_mid_run_statuses_start_now_and_wait(day):
    context, plan, run = day
    first = [e for e in run.events if e.kind == "admission"][-1].messages    # after all 23:00 decisions
    by = {m.household_id: m.status for m in first}
    assert by == {"hh-A": "BOOKED_START_NOW", "hh-B": "WAIT", "hh-C": "APPROVAL_NEEDED"}


def test_unserved_and_not_offered_from_the_plan(day):
    context, plan, run = day
    keep = [r for r in plan.reservations if r.request_id == "req-A"]
    sub = Plan(proposal_id=plan.proposal_id, tariff=plan.tariff, reservations=keep,
               unserved=[Unserved(request_id="req-B", reason="UNSERVED_CAPACITY")])
    trace = run.trace.model_copy(update={"extra": {**run.trace.extra, "admission": [
        d for d in run.trace.extra["admission"] if d["request_id"] == "req-A"]}})
    by = {m.household_id: m for m in household_outcomes(context, sub, trace)}
    assert (by["hh-B"].status, by["hh-B"].reason, by["hh-B"].reservation_id) == ("NOT_SERVED", "UNSERVED_CAPACITY", None)
    assert (by["hh-C"].status, by["hh-C"].reason) == ("NOT_OFFERED", "UNSERVED_NOT_OFFERED")


def test_no_message_references_another_household(day):
    context, plan, run = day
    every = [m for e in run.events for m in e.messages] + household_outcomes(context, plan, run.trace)
    assert every
    for m in every:
        assert private(m, context, plan, render_one(m))
        for other in context.requests:
            if other.household_id != m.household_id:
                blob = m.model_dump_json() + render_one(m)
                assert other.request_id not in blob and other.household_id not in blob
    assert [m.household_id for m in message_for(every, "hh-B")] == ["hh-B"] * len(message_for(every, "hh-B"))
    assert set(HouseholdMessage.model_fields) == {"household_id", "request_id", "reservation_id", "status", "reason",
                                                  "start", "end", "locked_quote"}       # no ledger, no aggregates


def test_routing_refuses_decisions_or_reservations_outside_the_map(day):
    context, plan, run = day
    bad = run.trace.model_copy(update={"extra": {**run.trace.extra, "admission": [
        {**run.trace.extra["admission"][0], "request_id": "req-B"}]}})       # decision mislabelled to another request
    with pytest.raises(ContractError):
        household_outcomes(context, plan, bad)
    forged = plan.model_copy(update={"reservations": [plan.reservations[0].model_copy(update={"request_id": "req-Z"})]})
    with pytest.raises(ContractError):
        household_outcomes(context, forged, run.trace)


def test_render_is_one_short_line_per_household(day):
    context, plan, run = day
    lines = render(household_outcomes(context, plan, run.trace))
    assert len(lines) == 3 and all(len(line) <= 160 and "\n" not in line for line in lines)
    assert lines[0].startswith("hh-A: completed") and "23:00-23:30" in lines[0]
    assert lines[1].startswith("hh-B: not served") and "insufficient stored energy" in lines[1]
    assert lines[2].startswith("hh-C: approval needed")
