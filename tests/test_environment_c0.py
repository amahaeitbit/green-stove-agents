"""C0 tests: hand-calculated fixture, plan checks, validation. pytest or unittest."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from greenstove.billing import quote, quote_for
from greenstove.io import context_from_dict, load_context, load_plan, load_requirements, run_fixture, with_hashes
from greenstove.models import ContractError, HouseholdRequest, Plan, Requirements, tariff_version
from greenstove.simulator import simulate
from greenstove.tariff import validate_tariff
from greenstove.verifier import verify

C0 = Path(__file__).resolve().parent.parent / "fixtures" / "c0"
CTX, REQ = C0 / "context.json", C0 / "requirements.json"


def plan_dict(name: str) -> dict:
    return json.loads((C0 / name).read_text())


class TestHandCalculated(unittest.TestCase):
    def test_simultaneous_fails_power_check(self):
        v = run_fixture(CTX, C0 / "plan-simultaneous.json", REQ)
        self.assertFalse(v.passed)
        self.assertEqual(v.metrics["peak_load_kw"], 3.0)
        self.assertEqual(v.metrics["overload_steps"], 2.0)
        [f] = v.feedback_codes
        self.assertEqual((f.code, f.interval, f.end_interval, f.value, f.limit), ("INVERTER_LIMIT", 72, 74, 3.0, 2.0))
        self.assertEqual(f.request_ids, ["req-A", "req-B"])
        self.assertEqual({c.name: c.passed for c in v.conditions}["inverter"], False)

    def test_sequential_passes_and_uses_1_5_kwh(self):
        v = run_fixture(CTX, C0 / "plan-sequential.json", REQ)
        self.assertTrue(v.passed)
        self.assertEqual(v.metrics["peak_load_kw"], 2.0)
        self.assertEqual(v.metrics["cooking_energy_kwh"], 1.5)
        self.assertEqual(v.metrics["served_fraction"], 1.0)
        self.assertEqual(v.metrics["max_shift_hours"], 0.5)   # B starts 2 steps after its preferred 18:00
        self.assertEqual(v.feedback_codes, [])

    def test_spend(self):
        v = run_fixture(CTX, C0 / "plan-sequential.json", REQ)
        self.assertEqual(v.metrics["total_spend"], 19.5)        # 6.50 + 13.00 at peak 13/kWh

    def test_unmodeled_parts_are_not_reported_as_checked(self):
        v = run_fixture(CTX, C0 / "plan-sequential.json", REQ)
        names = {c.name for c in v.conditions}
        self.assertNotIn("battery_reserve", names)
        self.assertNotIn("essential", names)
        self.assertNotIn("min_battery_kwh", v.metrics)
        self.assertIn("battery_reserve", v.coverage.not_modeled)

    def test_deterministic(self):
        a = run_fixture(CTX, C0 / "plan-simultaneous.json", REQ)
        b = run_fixture(CTX, C0 / "plan-simultaneous.json", REQ)
        self.assertEqual(a.model_dump(), b.model_dump())


class TestPlanChecks(unittest.TestCase):
    def setUp(self):
        self.ctx, self.req = load_context(CTX), load_requirements(REQ)

    def run_plan(self, d: dict):
        plan = Plan.model_validate(d)
        return verify(simulate(plan, self.ctx), self.req, self.ctx, plan)

    def requote(self, r: dict, d: dict) -> None:
        r["locked_cost"] = quote_for(r["power_kw"], r["start_step"], r["end_step"], d["tariff"], self.ctx)

    def test_missing_household_is_service_shortfall(self):
        d = plan_dict("plan-sequential.json")
        d["reservations"] = d["reservations"][:1]
        v = self.run_plan(d)
        self.assertFalse(v.passed)
        # req-B was left out of the plan entirely: reported as not offered, and service falls short.
        self.assertEqual([f.code for f in v.feedback_codes], ["UNSERVED_NOT_OFFERED", "SERVICE_SHORTFALL"])
        self.assertEqual(v.feedback_codes[0].request_id, "req-B")
        self.assertEqual(v.feedback_codes[1].request_ids, ["req-B"])

    def test_unaccepted_offer_does_not_count_as_served(self):
        d = plan_dict("plan-sequential.json")
        d["reservations"][1]["accepted"] = False
        self.assertIn("SERVICE_SHORTFALL", [f.code for f in self.run_plan(d).feedback_codes])

    def test_outside_window(self):
        d = plan_dict("plan-sequential.json")
        r = d["reservations"][1]
        r.update(start_step=80, end_step=82, reservation_id="res-late")
        self.requote(r, d)
        self.assertIn("OUTSIDE_WINDOW", [f.code for f in self.run_plan(d).feedback_codes])

    def test_shift_over_requirement(self):
        d = plan_dict("plan-sequential.json")
        r = d["reservations"][1]
        r.update(start_step=68, end_step=70, reservation_id="res-early")   # 1 h shift, allowed 3 h
        self.requote(r, d)
        self.assertTrue(self.run_plan(d).passed)
        tight = Requirements(min_service_fraction=1.0, max_shift_hours=0.5)
        plan = Plan.model_validate(d)
        v = verify(simulate(plan, self.ctx), tight, self.ctx, plan)
        self.assertFalse(v.passed)

    def test_duplicate_reservation(self):
        d = plan_dict("plan-sequential.json")
        extra = dict(d["reservations"][0], reservation_id="res-dup", start_step=76, end_step=78)
        d["reservations"].append(extra)
        self.assertIn("DUPLICATE_RESERVATION", [f.code for f in self.run_plan(d).feedback_codes])

    def test_over_budget(self):
        d = plan_dict("plan-sequential.json")
        ctx = copy.deepcopy(json.loads(CTX.read_text()))
        ctx["requests"][1]["max_cost"] = 12.0
        self.ctx = context_from_dict(with_hashes(ctx))
        self.assertIn("BUDGET_EXCEEDED", [f.code for f in self.run_plan(d).feedback_codes])

    def test_wrong_locked_cost_is_contract_error(self):
        d = plan_dict("plan-sequential.json")
        d["reservations"][0]["locked_cost"] = 1.0
        with self.assertRaises(ContractError):
            simulate(Plan.model_validate(d), self.ctx)

    def test_quote_on_other_tariff_is_contract_error(self):
        d = plan_dict("plan-sequential.json")
        d["tariff"] = {"standard": 8.0, "sun": 4.0, "peak": 16.0}
        with self.assertRaises(ContractError):
            simulate(Plan.model_validate(d), self.ctx)


class TestValidation(unittest.TestCase):
    def test_unknown_field_rejected(self):
        d = plan_dict("plan-sequential.json")
        d["reservations"][0]["bonus"] = 1
        with self.assertRaises(ValidationError):
            Plan.model_validate(d)

    def test_nonfinite_and_negative_rejected(self):
        base = json.loads((C0 / "request.json").read_text())
        for bad in ({"power_kw": float("nan")}, {"power_kw": float("inf")}, {"power_kw": -1.0},
                    {"duration_steps": 0}, {"max_cost": -5.0}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                HouseholdRequest.model_validate({**base, **bad})

    def test_window_shorter_than_duration_rejected(self):
        base = json.loads((C0 / "request.json").read_text())
        with self.assertRaises(ValidationError):
            HouseholdRequest.model_validate({**base, "earliest_start": 79, "preferred_start": 79})

    def test_tariff_lattice_order_and_keys(self):
        ctx = load_context(CTX)
        self.assertEqual(validate_tariff(load_plan(C0 / "plan-sequential.json").tariff, ctx), [])
        bad = {"standard": 8.5, "sun": 10.0, "peak": 20.0}           # all off-lattice, and sun > standard
        msgs = [f.message for f in validate_tariff(bad, ctx)]
        self.assertEqual(len(msgs), 4)
        self.assertEqual(len(validate_tariff({"sun": 4.0}, ctx)), 1)  # missing windows

    def test_window_lattice_mismatch_rejected(self):
        d = json.loads(CTX.read_text())
        d["lattice"] = {"sun": [4.0]}
        with self.assertRaises(ValidationError):
            context_from_dict(with_hashes(d))

    def test_tampered_context_hash_rejected(self):
        d = json.loads(CTX.read_text())
        d["inverter_kw"] = 3.0
        with self.assertRaises(ContractError):
            context_from_dict(d)

    def test_tariff_version_ignores_int_vs_float(self):
        self.assertEqual(tariff_version({"a": 1, "b": 2}), tariff_version({"b": 2.0, "a": 1.0}))
        self.assertEqual(quote(1.0, 0, 1, [12.0] * 96), 3.0)


if __name__ == "__main__":
    unittest.main()
