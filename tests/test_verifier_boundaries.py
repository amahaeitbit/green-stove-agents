"""Regressions from the independent review of the allocator increment."""
import json
import unittest
from pathlib import Path

from greenstove.allocator import allocate
from greenstove.households import household_requests
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import ContractError, HouseholdRequest, Plan, Requirements
from greenstove.simulator import simulate
from greenstove.verifier import C0_COVERAGE, verify

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/c0/context.json"
TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}


def context(requests, **changes):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["requests"] = [r.model_dump() for r in requests]
    data.update(changes)
    return context_from_dict(with_hashes(data))


def request(rid="A", **changes):
    values = dict(request_id=rid, household_id="home-" + rid, power_kw=1.0,
                  duration_steps=2, preferred_start=72, earliest_start=68,
                  latest_finish=80, max_cost=100.0, shift_penalty=2.0, auto_book=True)
    return HouseholdRequest(**(values | changes))


def plan_for(ctx):
    return allocate(household_requests(ctx), TARIFF, ctx, "boundary", max_shift_hours=3.0)


class VerdictIsolation(unittest.TestCase):
    def test_mutating_coverage_cannot_change_other_verdicts_or_template(self):
        ctx = context([request()])
        plan = plan_for(ctx)
        trace = simulate(plan, ctx)
        first = verify(trace, Requirements(), ctx, plan)
        second = verify(trace, Requirements(), ctx, plan)
        first.coverage.complete = True
        first.coverage.phases_checked.append("actual")
        first.coverage.not_modeled.clear()
        third = verify(trace, Requirements(), ctx, plan)
        for coverage in (second.coverage, third.coverage, C0_COVERAGE):
            self.assertFalse(coverage.complete)
            self.assertEqual(coverage.phases_checked, ["forecast_expected"])
            self.assertIn("battery_energy", coverage.not_modeled)

    def test_requirements_snapshot_is_detached_from_caller(self):
        ctx = context([request()])
        plan = plan_for(ctx)
        requirements = Requirements()
        verdict = verify(simulate(plan, ctx), requirements, ctx, plan)
        requirements.min_service_fraction = 0.0
        self.assertEqual(verdict.requirements.min_service_fraction, 1.0)


class PendingCapacity(unittest.TestCase):
    def test_forged_overlapping_pending_hold_is_rejected(self):
        ctx = context([request(power_kw=2.0), request("B", power_kw=2.0, auto_book=False)])
        data = plan_for(ctx).model_dump()
        data["reservations"][1].update(start_step=72, end_step=74)
        plan = Plan.model_validate(data)
        with self.assertRaisesRegex(ContractError, "pending capacity holds"):
            verify(simulate(plan, ctx), Requirements(min_service_fraction=0.5), ctx, plan)

    def test_valid_pending_hold_does_not_count_as_consumption_or_service(self):
        ctx = context([request(), request("B", auto_book=False)])
        plan = plan_for(ctx)
        result = verify(simulate(plan, ctx), Requirements(min_service_fraction=0.5), ctx, plan)
        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["peak_load_kw"], 1.0)
        self.assertEqual(result.metrics["completed_sessions"], 1.0)
        # Hypothetical approval of an unchanged valid hold preserves power capacity.
        data = plan.model_dump()
        data["reservations"][1]["accepted"] = True
        approved = Plan.model_validate(data)
        result = verify(simulate(approved, ctx), Requirements(), ctx, approved)
        self.assertTrue(result.passed)
        self.assertEqual(result.metrics["peak_load_kw"], 2.0)


class PowerPrecision(unittest.TestCase):
    def test_sub_micro_kw_exact_capacity_is_not_rounded_into_overload(self):
        ctx = context([request(power_kw=1.0000006)], inverter_kw=1.0000006)
        plan = plan_for(ctx)
        trace = simulate(plan, ctx)
        self.assertEqual(max(trace.demand_kw), 1.0000006)
        self.assertEqual(trace.overload_steps, [])
        self.assertTrue(verify(trace, Requirements(), ctx, plan).passed)

    def test_real_small_overload_is_consistently_reported(self):
        roomy = context([request(power_kw=1.000002)], inverter_kw=2.0)
        plan = plan_for(roomy)
        constrained = context(roomy.requests, inverter_kw=1.0000006)
        trace = simulate(plan, constrained)
        result = verify(trace, Requirements(), constrained, plan)
        self.assertFalse(result.passed)
        self.assertEqual(trace.overload_steps, [72, 73])
        self.assertEqual(result.feedback_codes[0].code, "INVERTER_LIMIT")


if __name__ == "__main__":
    unittest.main()
