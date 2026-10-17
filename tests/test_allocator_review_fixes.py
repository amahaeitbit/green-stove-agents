"""Regression tests for the allocator review: M1 (max shift), M2 (unserved codes), L1 (unserved checks)."""
from __future__ import annotations

import json
import random
import unittest
from decimal import Decimal
from pathlib import Path

from greenstove.allocator import allocate
from greenstove.billing import quote, step_prices
from greenstove.households import household_requests
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import UNSERVED_REASONS, ContractError, HouseholdRequest, Plan, Requirements
from greenstove.simulator import simulate
from greenstove.verifier import verify

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/c0/context.json"
TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}
FATAL_CODES = {"INVERTER_LIMIT", "BUDGET_EXCEEDED", "OUTSIDE_WINDOW", "DUPLICATE_RESERVATION", "SERVICE_SHORTFALL"}


def request(rid="req-A", **changes):
    fields = dict(request_id=rid, household_id="hh-" + rid, power_kw=1.0, duration_steps=2, preferred_start=72,
                  earliest_start=68, latest_finish=80, max_cost=20.0, shift_penalty=2.0, auto_book=True)
    return HouseholdRequest(**(fields | changes))


def context(requests, **changes):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    data["requests"] = [r.model_dump() for r in requests]
    data.update(changes)
    return context_from_dict(with_hashes(data))


def plan_for(ctx, **kw):
    return allocate(household_requests(ctx), TARIFF, ctx, "fix-test", **kw)


class M1MaxShift(unittest.TestCase):
    def setUp(self):
        # Wide window, no penalty: the cheapest start is 10:00, an 8 h shift.
        self.ctx = context([request(earliest_start=40, shift_penalty=0.0)])
        self.req = Requirements(max_shift_hours=3.0)

    def test_without_limit_the_old_behaviour_is_unchanged(self):
        plan = plan_for(self.ctx)
        self.assertEqual(plan.reservations[0].start_step, 40)
        self.assertFalse(verify(simulate(plan, self.ctx), self.req, self.ctx, plan).passed)

    def test_with_limit_the_allocator_stays_within_the_verifiers_shift_rule(self):
        plan = plan_for(self.ctx, max_shift_hours=self.req.max_shift_hours)
        start = plan.reservations[0].start_step
        self.assertLessEqual(abs(72 - start) * 0.25, 3.0)
        self.assertEqual(start, 60)   # cheapest start within 3 h: 15:00 in the sun window
        self.assertTrue(verify(simulate(plan, self.ctx), self.req, self.ctx, plan).passed)

    def test_zero_limit_books_the_preferred_start(self):
        self.assertEqual(plan_for(self.ctx, max_shift_hours=0).reservations[0].start_step, 72)

    def test_limit_can_make_a_household_unaffordable(self):
        ctx = context([request(earliest_start=40, shift_penalty=0.0, max_cost=3.0)])
        self.assertEqual(plan_for(ctx).reservations[0].start_step, 40)          # 2.00 in the sun window
        plan = plan_for(ctx, max_shift_hours=0.5)                               # only peak starts allowed
        self.assertEqual([(u.request_id, u.reason) for u in plan.unserved], [("req-A", "UNSERVED_BUDGET")])

    def test_invalid_limits_rejected(self):
        for bad in (-1.0, float("nan"), float("inf"), True, "3"):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                plan_for(self.ctx, max_shift_hours=bad)


class M2UnservedCodes(unittest.TestCase):
    def test_passing_verdict_never_carries_a_fatal_code(self):
        ctx = context([request(power_kw=2.0, earliest_start=72, latest_finish=74),
                       request("req-B", earliest_start=72, latest_finish=74),
                       request("req-C", max_cost=1.0)])
        plan = plan_for(ctx)
        self.assertEqual(sorted((u.request_id, u.reason) for u in plan.unserved),
                         [("req-B", "UNSERVED_CAPACITY"), ("req-C", "UNSERVED_BUDGET")])
        v = verify(simulate(plan, ctx), Requirements(min_service_fraction=1 / 3), ctx, plan)
        self.assertTrue(v.passed)
        codes = {f.code for f in v.feedback_codes}
        self.assertEqual(codes, {"UNSERVED_CAPACITY", "UNSERVED_BUDGET"})
        self.assertFalse(codes & FATAL_CODES)

    def test_real_overload_still_reports_inverter_limit(self):
        # The hand-built C0 plan puts 3 kW on a 2 kW inverter: a real violation.
        ctx = context_from_dict(json.loads(FIXTURE.read_text()))
        plan = Plan.model_validate(json.loads((FIXTURE.parent / "plan-simultaneous.json").read_text()))
        v = verify(simulate(plan, ctx), Requirements(), ctx, plan)
        self.assertFalse(v.passed)
        self.assertEqual([f.code for f in v.feedback_codes], ["INVERTER_LIMIT"])


class L1UnservedValidation(unittest.TestCase):
    def setUp(self):
        self.ctx = context([request(), request("req-B", max_cost=1.0)])
        self.plan = plan_for(self.ctx)

    def forged(self, unserved):
        return Plan.model_validate({**self.plan.model_dump(), "unserved": unserved})

    def test_valid_plan_passes_checks(self):
        verify(simulate(self.plan, self.ctx), Requirements(min_service_fraction=0.5), self.ctx, self.plan)

    def test_forged_unserved_entries_rejected(self):
        cases = {
            "unknown request": [{"request_id": "ghost", "reason": "UNSERVED_CAPACITY"},
                                {"request_id": "req-B", "reason": "UNSERVED_BUDGET"}],
            "listed twice": [{"request_id": "req-B", "reason": "UNSERVED_BUDGET"}] * 2,
            "also reserved": [{"request_id": "req-A", "reason": "UNSERVED_BUDGET"},
                              {"request_id": "req-B", "reason": "UNSERVED_BUDGET"}],
            "fatal code as reason": [{"request_id": "req-B", "reason": "INVERTER_LIMIT"}],
        }
        for name, unserved in cases.items():
            with self.subTest(name), self.assertRaises(ContractError):
                plan = self.forged(unserved)
                verify(simulate(plan, self.ctx), Requirements(), self.ctx, plan)


class AllocatorProperties(unittest.TestCase):
    """Randomized invariants (the review's fuzz check, shortened)."""

    def test_random_plans_respect_capacity_authority_windows_budgets_quotes_and_shift(self):
        rng = random.Random(11)
        tariffs = [{"standard": s, "sun": u, "peak": p} for s in (6.0, 8.0, 10.0) for u in (3.0, 4.0, 5.0)
                   for p in (10.0, 13.0, 16.0)]
        for trial in range(300):
            reqs = []
            for i in range(rng.randint(1, 8)):
                dur = rng.randint(1, 6)
                early = rng.randint(0, 90 - dur)
                late = rng.randint(early + dur, min(96, early + dur + 30))
                reqs.append(request(f"r{i}", power_kw=rng.choice([0.3, 0.8, 1.0, 1.5, 2.0]), duration_steps=dur,
                                    preferred_start=rng.randint(early, late - dur), earliest_start=early,
                                    latest_finish=late, max_cost=rng.choice([1.0, 5.0, 30.0]),
                                    shift_penalty=rng.choice([0.0, 1.0, 4.0]), auto_book=rng.random() < 0.7))
            ctx = context(reqs, inverter_kw=rng.choice([1.5, 2.0, 3.0]))
            tariff, limit = rng.choice(tariffs), rng.choice([None, 0.5, 2.0])
            plan = allocate(list(ctx.requests), tariff, ctx, f"p{trial}", max_shift_hours=limit)
            prices, held = step_prices(tariff, ctx), [Decimal(0)] * 96
            by_id = {r.request_id: r for r in reqs}
            for r in plan.reservations:
                q = by_id[r.request_id]
                for t in range(r.start_step, r.end_step):
                    held[t] += Decimal(str(r.power_kw))
                self.assertEqual(r.accepted, q.auto_book)
                self.assertTrue(q.earliest_start <= r.start_step and r.end_step <= q.latest_finish)
                self.assertLessEqual(r.locked_cost, q.max_cost)
                self.assertEqual(r.locked_cost, quote(q.power_kw, r.start_step, r.end_step, prices))
                if limit is not None:
                    self.assertLessEqual(abs(r.start_step - q.preferred_start) * 0.25, limit)
            self.assertTrue(all(h <= Decimal(str(ctx.inverter_kw)) for h in held))
            self.assertEqual(sorted([r.request_id for r in plan.reservations] + [u.request_id for u in plan.unserved]),
                             sorted(by_id))
            self.assertTrue(all(u.reason in UNSERVED_REASONS for u in plan.unserved))
            v = verify(simulate(plan, ctx), Requirements(min_service_fraction=0.0,
                                                        max_shift_hours=24.0 if limit is None else limit), ctx, plan)
            self.assertTrue(v.passed, msg=f"trial {trial}")


class D1AutoBookersFirst(unittest.TestCase):
    """Decision D1, option 3: pending offers use only capacity left after auto-bookers."""

    def test_pending_offer_cannot_displace_an_auto_booker(self):
        # Same rigid slot, 2 kW limit. Pending A comes first in time order but must not block B.
        ctx = context([request(power_kw=2.0, auto_book=False, earliest_start=72, latest_finish=74),
                       request("req-B", power_kw=2.0, preferred_start=73, earliest_start=72, latest_finish=75)])
        plan = plan_for(ctx)
        self.assertEqual([(r.request_id, r.accepted) for r in plan.reservations], [("req-B", True)])
        self.assertEqual([(u.request_id, u.reason) for u in plan.unserved], [("req-A", "UNSERVED_CAPACITY")])
        v = verify(simulate(plan, ctx), Requirements(min_service_fraction=0.5), ctx, plan)
        self.assertTrue(v.passed)

    def test_pending_offer_uses_leftover_capacity_and_still_holds_it(self):
        ctx = context([request(power_kw=1.0, auto_book=False), request("req-B", power_kw=1.0),
                       request("req-C", power_kw=1.0, preferred_start=73)])
        plan = plan_for(ctx)
        slots = {r.request_id: (r.start_step, r.accepted) for r in plan.reservations}
        self.assertTrue(slots["req-B"][1] and slots["req-C"][1])
        self.assertFalse(slots["req-A"][1])
        held = [0.0] * 96
        for r in plan.reservations:
            for t in range(r.start_step, r.end_step):
                held[t] += r.power_kw
        self.assertLessEqual(max(held), ctx.inverter_kw)   # approving A later could not overbook

    def test_order_within_each_pass_is_still_preferred_start_then_id(self):
        ctx = context([request("req-Z", auto_book=False, preferred_start=70),
                       request("req-Y", auto_book=False),
                       request("req-B"), request("req-A", preferred_start=74)])
        ids = [r.request_id for r in plan_for(ctx).reservations]
        self.assertEqual(ids, ["req-B", "req-A", "req-Z", "req-Y"])


class L2EveryRequestAccountedFor(unittest.TestCase):
    def setUp(self):
        self.ctx = context([request(), request("req-B")])

    def test_subset_allocation_reports_the_missing_request(self):
        plan = allocate([self.ctx.requests[0]], TARIFF, self.ctx, "subset")
        v = verify(simulate(plan, self.ctx), Requirements(min_service_fraction=0.5), self.ctx, plan)
        self.assertTrue(v.passed)                                   # service and pass/fail unchanged
        self.assertEqual(v.metrics["served_fraction"], 0.5)
        self.assertEqual([(f.code, f.request_id) for f in v.feedback_codes], [("UNSERVED_NOT_OFFERED", "req-B")])

    def test_strict_floor_still_fails_with_both_codes(self):
        plan = allocate([], TARIFF, self.ctx, "empty")
        v = verify(simulate(plan, self.ctx), Requirements(), self.ctx, plan)
        self.assertFalse(v.passed)
        codes = [f.code for f in v.feedback_codes]
        self.assertEqual(codes.count("UNSERVED_NOT_OFFERED"), 2)
        self.assertIn("SERVICE_SHORTFALL", codes)

    def test_full_allocation_never_emits_it(self):
        plan = plan_for(self.ctx)
        v = verify(simulate(plan, self.ctx), Requirements(), self.ctx, plan)
        self.assertNotIn("UNSERVED_NOT_OFFERED", [f.code for f in v.feedback_codes])

    def test_allocator_may_not_use_it_as_a_reason(self):
        plan = Plan.model_validate({**plan_for(self.ctx, max_shift_hours=0).model_dump(), "reservations": [],
                                    "unserved": [{"request_id": "req-A", "reason": "UNSERVED_NOT_OFFERED"},
                                                 {"request_id": "req-B", "reason": "UNSERVED_NOT_OFFERED"}]})
        with self.assertRaises(ContractError):
            verify(simulate(plan, self.ctx), Requirements(), self.ctx, plan)


if __name__ == "__main__":
    unittest.main()
