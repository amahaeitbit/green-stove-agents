"""Battery-backed start checks and energy ledger accounting, without agent calls."""
import json
import random
import unittest
from pathlib import Path

from pydantic import ValidationError

from greenstove.admission import advance_interval, can_start, initial_state, outstanding_storage_kwh
from greenstove.admission_models import BatterySpec
from greenstove.billing import quote_for
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import ContractError, HouseholdRequest, Reservation, tariff_version

TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/c0/context.json"


def setup(*, initial=1.0, capacity=1.0, floor=.4, essential=.1, efficiency=1.0,
          discharge=4.0, charge=4.0, power=1.0, auto=True):
    data = json.loads(FIXTURE.read_text())
    data.update(scenario_id="admission-test", initial_battery_kwh=initial,
                battery_capacity_kwh=capacity, reserve_kwh=floor, inverter_kw=4.0,
                essential_load_kw=[0.0]*92 + [essential]*4)
    data["requests"] = [HouseholdRequest(request_id=rid, household_id="home-"+rid,
        power_kw=power, duration_steps=2, preferred_start=92, earliest_start=92,
        latest_finish=96, max_cost=100.0, shift_penalty=1.0, auto_book=auto).model_dump()
        for rid in ("A", "B")]
    context = context_from_dict(with_hashes(data))
    battery = BatterySpec(capacity_kwh=capacity, floor_kwh=floor, max_charge_kw=charge,
                          max_discharge_kw=discharge, charge_efficiency=efficiency,
                          discharge_efficiency=efficiency)
    return context, battery, initial_state(context, battery, start_step=92)


def offer(context, rid="A", start=92, accepted=True):
    req = next(r for r in context.requests if r.request_id == rid)
    return Reservation(reservation_id="reservation-"+rid, request_id=rid,
                       start_step=start, end_step=start+2, power_kw=req.power_kw,
                       locked_cost=quote_for(req.power_kw, start, start+2, TARIFF, context),
                       tariff_version=tariff_version(TARIFF), accepted=accepted)


class AdmissionChecks(unittest.TestCase):
    def test_hand_calculated_commitment_cannot_be_spent_twice(self):
        ctx, battery, state = setup()
        a = can_start(offer(ctx), TARIFF, ctx, state, battery)
        self.assertEqual(a.status, "ADMIT")
        self.assertAlmostEqual(a.required_storage_kwh, .6)
        self.assertAlmostEqual(outstanding_storage_kwh(a.state, battery), .5)
        b = can_start(offer(ctx, "B"), TARIFF, ctx, a.state, battery)
        self.assertEqual((b.status, b.reason), ("DEFER", "INSUFFICIENT_STORED_ENERGY"))
        self.assertAlmostEqual(b.required_storage_kwh, 1.1)
        first = advance_interval(a.state, ctx, battery)
        self.assertAlmostEqual(first.state.battery_kwh, .725)
        self.assertAlmostEqual(outstanding_storage_kwh(first.state, battery), .25)
        second = advance_interval(first.state, ctx, battery)
        self.assertAlmostEqual(second.state.battery_kwh, .45)
        self.assertEqual(second.state.completed_request_ids, ("A",))
        self.assertAlmostEqual(second.state.delivered_cooking_kwh, .5)
        expired = can_start(offer(ctx, "B", start=94), TARIFF, ctx, second.state, battery)
        self.assertEqual(expired.status, "UNSERVED")
        final = advance_interval(advance_interval(second.state, ctx, battery).state, ctx, battery)
        self.assertAlmostEqual(final.state.battery_kwh, .4)
        self.assertEqual(final.state.completed_request_ids, ("A",))
        self.assertEqual(final.state.unserved_essential_kwh, 0)

    def test_admission_does_not_withdraw_battery_or_mutate_input(self):
        ctx, spec, state = setup()
        original = state.model_dump()
        result = can_start(offer(ctx), TARIFF, ctx, state, spec)
        self.assertEqual(state.model_dump(), original)
        self.assertEqual(result.state.battery_kwh, state.battery_kwh)
        self.assertEqual(len(result.state.commitments), 1)
        self.assertEqual(result.model_dump(), can_start(offer(ctx), TARIFF, ctx, state, spec).model_dump())

    def test_pending_offer_cannot_start(self):
        ctx, spec, state = setup(auto=False)
        result = can_start(offer(ctx, accepted=False), TARIFF, ctx, state, spec)
        self.assertEqual((result.status, result.reason), ("DEFER", "APPROVAL_REQUIRED"))
        self.assertEqual(result.state.commitments, ())

    def test_accepted_flag_cannot_override_missing_authority(self):
        ctx, spec, state = setup(auto=False)
        self.assertEqual(can_start(offer(ctx), TARIFF, ctx, state, spec).reason, "APPROVAL_REQUIRED")

    def test_duplicate_dispatch_rejected_even_after_completion(self):
        ctx, spec, state = setup()
        state = can_start(offer(ctx), TARIFF, ctx, state, spec).state
        for _ in range(2):
            state = advance_interval(state, ctx, spec).state
        with self.assertRaises(ContractError):
            can_start(offer(ctx, start=94), TARIFF, ctx, state, spec)

    def test_future_solar_has_no_admission_credit(self):
        ctx, spec, state = setup(initial=.4)
        data = ctx.model_dump()
        data.update(forecast_pv_kw=[10.0]*96, downside_pv_kw=[8.0]*96)
        ctx = context_from_dict(with_hashes(data))
        state = initial_state(ctx, spec, start_step=92)
        self.assertEqual(can_start(offer(ctx), TARIFF, ctx, state, spec).status, "DEFER")

    def test_discharge_power_and_inverter_both_gate_starts(self):
        ctx, spec, state = setup(discharge=.5)
        self.assertEqual(can_start(offer(ctx), TARIFF, ctx, state, spec).reason, "BATTERY_POWER")
        ctx, spec, state = setup(capacity=20, initial=20, power=4.0)
        self.assertEqual(can_start(offer(ctx), TARIFF, ctx, state, spec).reason, "INVERTER_CAPACITY")

    def test_discharge_efficiency_affects_required_storage(self):
        ctx, spec, state = setup(initial=1.2, capacity=2, efficiency=.5)
        result = can_start(offer(ctx), TARIFF, ctx, state, spec)
        self.assertEqual(result.status, "DEFER")
        self.assertAlmostEqual(result.required_storage_kwh, 1.2)

    def test_waiting_requires_a_new_authorized_quote_not_silent_retiming(self):
        ctx, spec, state = setup()
        result = can_start(offer(ctx, start=93), TARIFF, ctx, state, spec)
        self.assertEqual(result.reason, "NOT_START_TIME")
        state = advance_interval(state, ctx, spec).state
        self.assertEqual(can_start(offer(ctx), TARIFF, ctx, state, spec).reason, "NEW_RESERVATION_REQUIRED")

    def test_mismatched_context_or_battery_cannot_reuse_ledger(self):
        ctx, spec, state = setup()
        other = spec.model_copy(update={"max_discharge_kw": 3.0})
        with self.assertRaises(ContractError):
            can_start(offer(ctx), TARIFF, ctx, state, other)


class EnergyAccounting(unittest.TestCase):
    def test_charge_efficiency_power_limit_and_curtailment(self):
        ctx, spec, state = setup(initial=.4, efficiency=.8, charge=1)
        result = advance_interval(state, ctx, spec, pv_kw=3)
        self.assertAlmostEqual(result.pv_to_load_kwh, .025)
        self.assertAlmostEqual(result.battery_charge_kwh, .2)
        self.assertAlmostEqual(result.state.battery_kwh, .6)
        self.assertAlmostEqual(result.curtailed_pv_kwh, .475)
        self.assertEqual(result.battery_withdrawal_kwh, 0)

    def test_observed_charge_can_enable_a_later_start(self):
        ctx, spec, state = setup(initial=.4)
        state = advance_interval(state, ctx, spec, pv_kw=4).state
        self.assertEqual(can_start(offer(ctx, start=93), TARIFF, ctx, state, spec).status, "ADMIT")

    def test_capacity_caps_charge(self):
        ctx, spec, state = setup(initial=1)
        result = advance_interval(state, ctx, spec, pv_kw=10)
        self.assertEqual(result.state.battery_kwh, 1)
        self.assertEqual(result.battery_charge_kwh, 0)

    def test_essential_shortage_is_recorded_without_crossing_floor(self):
        ctx, spec, state = setup(initial=.4)
        result = advance_interval(state, ctx, spec)
        self.assertEqual(result.state.battery_kwh, .4)
        self.assertAlmostEqual(result.unserved_essential_kwh, .025)
        self.assertAlmostEqual(result.state.unserved_essential_kwh, .025)

    def test_no_admission_when_essential_energy_alone_is_unfunded(self):
        ctx, spec, state = setup(initial=.45)
        self.assertEqual(can_start(offer(ctx), TARIFF, ctx, state, spec).status, "DEFER")

    def test_invalid_spec_and_observed_inputs(self):
        ctx, spec, state = setup()
        with self.assertRaises(ValidationError):
            BatterySpec(**(spec.model_dump() | {"discharge_efficiency": 0}))
        for invalid in (-1, float("nan"), float("inf"), True):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError):
                advance_interval(state, ctx, spec, pv_kw=invalid)

    def test_cannot_advance_past_day_end(self):
        ctx, spec, _ = setup()
        state = initial_state(ctx, spec, start_step=96)
        with self.assertRaises(ContractError):
            advance_interval(state, ctx, spec)

    def test_random_admitted_sessions_complete_without_double_spending(self):
        rng = random.Random(31)
        for _ in range(100):
            ctx, spec, state = setup(initial=2, capacity=2, floor=.2, essential=.05,
                                     efficiency=rng.choice([.8, .95, 1.0]), power=rng.choice([.3, .8, 1.0]))
            for rid in ("A", "B"):
                state = can_start(offer(ctx, rid), TARIFF, ctx, state, spec).state
            initial = state.battery_kwh
            withdrawal = charging = 0
            while state.step < 96:
                before = state.model_dump()
                result = advance_interval(state, ctx, spec, pv_kw=rng.choice([0., .1, 1., 4.]))
                self.assertEqual(before, state.model_dump())
                withdrawal += result.battery_withdrawal_kwh
                charging += result.battery_charge_kwh
                state = result.state
                self.assertGreaterEqual(state.battery_kwh + 1e-12, spec.floor_kwh)
                self.assertLessEqual(state.battery_kwh, spec.capacity_kwh + 1e-12)
            self.assertAlmostEqual(state.battery_kwh, initial - withdrawal + charging)
            self.assertEqual(set(state.completed_request_ids), {"A", "B"})


if __name__ == "__main__":
    unittest.main()
