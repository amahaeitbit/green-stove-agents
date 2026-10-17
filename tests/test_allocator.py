"""Allocator acceptance and boundary tests; existing C0 verification remains intact."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from greenstove.allocator import allocate
from greenstove.households import household_requests
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import ContractError, HouseholdRequest, Requirements, tariff_version
from greenstove.simulator import simulate
from greenstove.verifier import verify

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/c0/context.json"
TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}


def request(rid="req-A", **changes):
    fields = dict(request_id=rid, household_id="hh-" + rid, power_kw=1.0,
                  duration_steps=2, preferred_start=72, earliest_start=68,
                  latest_finish=80, max_cost=20.0, shift_penalty=2.0, auto_book=True)
    return HouseholdRequest(**(fields | changes))


def context(requests=None, **changes):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if requests is not None:
        data["requests"] = [r.model_dump() for r in requests]
    data.update(changes)
    return context_from_dict(with_hashes(data))


def make_plan(ctx, **kwargs):
    return allocate(household_requests(ctx), kwargs.get("tariff", TARIFF), ctx,
                    kwargs.get("proposal_id", "allocator-test"))


class AllocatorAcceptance(unittest.TestCase):
    def test_generates_non_overlapping_two_household_plan(self):
        ctx = context([request(), request("req-B", power_kw=2.0)])
        plan = make_plan(ctx)
        slots = {r.request_id: (r.start_step, r.end_step) for r in plan.reservations}
        # A has first priority; B's equally good alternatives are 17:30 and 18:30.
        self.assertEqual(slots, {"req-A": (72, 74), "req-B": (70, 72)})
        verdict = verify(simulate(plan, ctx), Requirements(), ctx, plan)
        self.assertTrue(verdict.passed)
        self.assertFalse(verdict.coverage.complete)  # still only the C0 power model
        self.assertEqual(verdict.metrics["peak_load_kw"], 2.0)
        self.assertEqual(verdict.metrics["cooking_energy_kwh"], 1.5)
        self.assertEqual(verdict.metrics["total_spend"], 19.5)

    def test_simultaneous_homes_allowed_when_capacity_fits(self):
        plan = make_plan(context([request(), request("req-B")]))
        self.assertEqual([r.start_step for r in plan.reservations], [72, 72])
        self.assertTrue(all(r.accepted for r in plan.reservations))

    def test_flexible_household_chooses_cheaper_sun_window(self):
        plan = make_plan(context([request(earliest_start=60, shift_penalty=0.0)]))
        reservation, = plan.reservations
        self.assertEqual((reservation.start_step, reservation.locked_cost), (60, 2.0))

    def test_inconvenience_can_outweigh_cheaper_energy(self):
        plan = make_plan(context([request(earliest_start=60, shift_penalty=20.0)]))
        self.assertEqual(plan.reservations[0].start_step, 72)

    def test_rigid_window_does_not_move_for_low_price(self):
        plan = make_plan(context([request(earliest_start=72, latest_finish=74)]))
        self.assertEqual(plan.reservations[0].start_step, 72)

    def test_unaffordable_request_is_explicitly_unserved(self):
        plan = make_plan(context([request(max_cost=2.0)]))
        self.assertEqual(plan.reservations, [])
        self.assertEqual([(u.request_id, u.reason) for u in plan.unserved],
                         [("req-A", "UNSERVED_BUDGET")])

    def test_budget_equal_to_rounded_quote_is_allowed(self):
        plan = make_plan(context([request(max_cost=6.5)]))
        self.assertEqual(plan.reservations[0].locked_cost, 6.5)

    def test_capacity_rejection_not_confused_with_budget(self):
        plan = make_plan(context([request(power_kw=3.0, max_cost=100.0)]))
        self.assertEqual(plan.reservations, [])
        self.assertEqual(plan.unserved[0].reason, "UNSERVED_CAPACITY")

    def test_pending_offer_is_not_accepted_or_served(self):
        ctx = context([request(auto_book=False)])
        plan = make_plan(ctx)
        self.assertFalse(plan.reservations[0].accepted)
        verdict = verify(simulate(plan, ctx), Requirements(), ctx, plan)
        self.assertEqual(verdict.metrics["completed_sessions"], 0)
        self.assertFalse(verdict.passed)

    def test_allowed_service_shortfall_keeps_reasons_without_forcing_failure(self):
        ctx = context([request(max_cost=2.0), request("req-B")])
        plan = make_plan(ctx)
        trace = simulate(plan, ctx)
        allowed = verify(trace, Requirements(min_service_fraction=0.5), ctx, plan)
        required = verify(trace, Requirements(min_service_fraction=1.0), ctx, plan)
        self.assertTrue(allowed.passed)
        self.assertEqual(allowed.metrics["served_fraction"], 0.5)
        self.assertEqual([f.code for f in allowed.feedback_codes], ["UNSERVED_BUDGET"])
        self.assertFalse(required.passed)
        self.assertIn("SERVICE_SHORTFALL", [f.code for f in required.feedback_codes])

    def test_pending_offer_holds_capacity_in_candidate_only(self):
        # Two pending households compete; the earlier one holds the slot (D1: holds still count).
        ctx = context([request(power_kw=2.0, auto_book=False, earliest_start=72, latest_finish=74),
                       request("req-B", auto_book=False, earliest_start=72, latest_finish=74)])
        plan = make_plan(ctx)
        self.assertEqual([(r.request_id, r.accepted) for r in plan.reservations], [("req-A", False)])
        self.assertEqual([(u.request_id, u.reason) for u in plan.unserved],
                         [("req-B", "UNSERVED_CAPACITY")])
        self.assertEqual(make_plan(ctx).model_dump(), plan.model_dump())

    def test_essential_load_uses_capacity_without_claiming_energy_coverage(self):
        plan = make_plan(context([request(power_kw=2.0)], essential_load_kw=[0.1] * 96))
        self.assertEqual(plan.unserved[0].reason, "UNSERVED_CAPACITY")

    def test_cross_tier_quote_is_verifier_compatible(self):
        ctx = context([request(preferred_start=63, earliest_start=63, latest_finish=65)])
        plan = make_plan(ctx)
        self.assertEqual(plan.reservations[0].locked_cost, 4.25)
        self.assertEqual(plan.reservations[0].tariff_version, tariff_version(TARIFF))
        self.assertTrue(verify(simulate(plan, ctx), Requirements(), ctx, plan).passed)

    def test_day_end_exclusive_96_is_valid(self):
        plan = make_plan(context([request(preferred_start=94, earliest_start=94, latest_finish=96)]))
        self.assertEqual(plan.reservations[0].end_step, 96)

    def test_decimal_capacity_handles_exact_fractional_sum(self):
        ctx = context([request(power_kw=0.1), request("req-B", power_kw=0.2)], inverter_kw=0.3)
        plan = make_plan(ctx)
        self.assertEqual([r.start_step for r in plan.reservations], [72, 72])

    def test_input_order_does_not_change_priority_or_result(self):
        ctx = context([request(), request("req-B", power_kw=2.0)])
        forward = allocate(household_requests(ctx), TARIFF, ctx, "same")
        reverse = allocate(list(reversed(household_requests(ctx))), TARIFF, ctx, "same")
        self.assertEqual(forward.model_dump(), reverse.model_dump())

    def test_preferred_start_is_primary_priority(self):
        ctx = context([request(), request("req-Z", preferred_start=70)])
        self.assertEqual([r.request_id for r in make_plan(ctx).reservations], ["req-Z", "req-A"])

    def test_inputs_and_quotes_are_not_mutated(self):
        ctx = context([request()])
        requests = household_requests(ctx)
        prices = dict(TARIFF)
        before = ctx.model_dump()
        plan = allocate(requests, prices, ctx, "no-mutation")
        self.assertEqual(ctx.model_dump(), before)
        self.assertEqual(prices, TARIFF)
        prices["peak"] = 16.0
        self.assertEqual(plan.tariff["peak"], 13.0)
        requests[0].max_cost = 0.0
        self.assertEqual(ctx.requests[0].max_cost, 20.0)


class AllocatorContractChecks(unittest.TestCase):
    def test_duplicate_unknown_and_altered_requests_rejected(self):
        ctx = context([request()])
        for inputs in ([request(), request()], [request("unknown")], [request(auto_book=False)]):
            with self.subTest(inputs=inputs), self.assertRaises(ContractError):
                allocate(inputs, TARIFF, ctx, "invalid-request")

    def test_invalid_tariffs_rejected_before_allocation(self):
        ctx = context([request()])
        for prices in ({}, {**TARIFF, "peak": 99.0}, {**TARIFF, "peak": float("nan")},
                       {**TARIFF, "sun": True}, {**TARIFF, "extra": 1.0}):
            with self.subTest(prices=prices), self.assertRaises(ContractError):
                make_plan(ctx, tariff=prices)

    def test_empty_proposal_id_rejected(self):
        with self.assertRaises(ContractError):
            make_plan(context([request()]), proposal_id=" ")

    def test_empty_input_does_not_hide_full_scenario_service_shortfall(self):
        ctx = context([request()])
        plan = allocate([], TARIFF, ctx, "empty")
        verdict = verify(simulate(plan, ctx), Requirements(), ctx, plan)
        self.assertFalse(verdict.passed)
        self.assertIn("SERVICE_SHORTFALL", [f.code for f in verdict.feedback_codes])


if __name__ == "__main__":
    unittest.main()
