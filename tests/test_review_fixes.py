"""Regression tests for the C0 review (findings 1-4) plus billing boundaries."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from pydantic import ValidationError

from greenstove.billing import quote, quote_for
from greenstove.io import context_from_dict, load_context, load_plan, load_requirements, run_fixture_full, with_hashes
from greenstove.models import (
    ActualScenario,
    ContractError,
    Plan,
    Requirements,
    Trace,
    compute_scenario_hash,
    compute_station_hash,
)
from greenstove.simulator import simulate
from greenstove.verifier import verify

C0 = Path(__file__).resolve().parent.parent / "fixtures" / "c0"
CTX, REQ = C0 / "context.json", C0 / "requirements.json"


def ctx_dict() -> dict:
    return json.loads(CTX.read_text())


class Finding1VerifierAuthority(unittest.TestCase):
    def setUp(self):
        self.ctx, self.req = load_context(CTX), load_requirements(REQ)
        self.plan = load_plan(C0 / "plan-simultaneous.json")
        self.trace = simulate(self.plan, self.ctx)

    def test_lower_station_limit_fails_sequential(self):
        d = ctx_dict()
        d["inverter_kw"] = 1.0
        ctx = context_from_dict(with_hashes(d))
        plan = load_plan(C0 / "plan-sequential.json")
        v = verify(simulate(plan, ctx), self.req, ctx, plan)
        [f] = v.feedback_codes   # A's 1 kW equals the limit; B's 2 kW at 18:30 exceeds it
        self.assertEqual((f.code, f.interval, f.end_interval, f.request_ids), ("INVERTER_LIMIT", 74, 76, ["req-B"]))
        self.assertEqual(f.limit, 1.0)

    def test_trace_from_other_context_rejected(self):
        d = ctx_dict()
        d["inverter_kw"] = 5.0
        roomy = context_from_dict(with_hashes(d))
        with self.assertRaises(ContractError):
            verify(simulate(self.plan, roomy), self.req, self.ctx, self.plan)   # trace made under 5 kW

    def test_erased_overloads_rejected(self):
        with self.assertRaises(ContractError):
            verify(self.trace.model_copy(update={"overload_steps": []}), self.req, self.ctx, self.plan)

    def test_tampered_load_array_rejected(self):
        cooking = list(self.trace.cooking_kw)
        cooking[72] = cooking[73] = 1.0
        bad = self.trace.model_copy(update={"cooking_kw": cooking, "demand_kw": cooking, "overload_steps": []})
        with self.assertRaises(ContractError):
            verify(bad, self.req, self.ctx, self.plan)

    def test_trace_for_different_plan_rejected(self):
        other = load_plan(C0 / "plan-sequential.json")
        with self.assertRaises(ContractError):
            verify(self.trace, self.req, self.ctx, other)

    def test_malformed_or_nonfinite_trace_rejected(self):
        d = self.trace.model_dump(mode="json")
        short, nan = copy.deepcopy(d), copy.deepcopy(d)
        short["demand_kw"] = short["demand_kw"][:95]
        nan["cooking_kw"][0] = float("nan")
        for bad in (short, nan):
            with self.assertRaises(ValidationError):
                Trace.model_validate(bad)


class Finding2StationVsScenarioHash(unittest.TestCase):
    def test_daily_changes_keep_config_hash(self):
        base = ctx_dict()
        for change in ({"scenario_id": "another-day"}, {"day": 3}, {"weather_class": "overcast"},
                       {"forecast_pv_kw": [1.0] * 96, "downside_pv_kw": [0.5] * 96},
                       {"initial_battery_kwh": 3.0}, {"requests": base["requests"][:1]}):
            d = {**base, **change}
            self.assertEqual(compute_station_hash(d), base["config_hash"], change)
            self.assertNotEqual(compute_scenario_hash(d), base["scenario_hash"], change)

    def test_station_changes_change_config_hash(self):
        base = ctx_dict()
        for key, value in (("inverter_kw", 3.0), ("reserve_kwh", 2.0), ("battery_capacity_kwh", 8.0),
                           ("lattice", {**base["lattice"], "peak": [10.0, 16.0]}),
                           ("tier_order", ["standard", "sun", "peak"])):
            d = {**base, key: value}
            self.assertNotEqual(compute_station_hash(d), base["config_hash"], key)
            self.assertEqual(compute_scenario_hash(d), base["scenario_hash"], key)

    def test_hashes_checked_on_load(self):
        d = ctx_dict()
        d["scenario_id"] = "edited-without-rehash"
        with self.assertRaises(ContractError):
            context_from_dict(d)
        context_from_dict(with_hashes(d))


class Finding3EvidenceIdentity(unittest.TestCase):
    def setUp(self):
        self.ctx, self.req = load_context(CTX), load_requirements(REQ)
        self.plan = load_plan(C0 / "plan-sequential.json")

    def test_same_inputs_same_ids(self):
        a, b = simulate(self.plan, self.ctx), simulate(self.plan, self.ctx)
        self.assertEqual(a.simulation_id, b.simulation_id)
        self.assertEqual(verify(a, self.req, self.ctx, self.plan).run_id, verify(b, self.req, self.ctx, self.plan).run_id)

    def test_tariff_contents_change_ids_even_with_same_proposal_id(self):
        # Change only the sun price; the evening quotes are unchanged but the tariff is different evidence.
        d = self.plan.model_dump(mode="json")
        d["tariff"] = {"standard": 8.0, "sun": 5.0, "peak": 13.0}
        for r in d["reservations"]:
            from greenstove.models import tariff_version
            r["tariff_version"] = tariff_version(d["tariff"])
        p2 = Plan.model_validate(d)
        self.assertEqual(p2.proposal_id, self.plan.proposal_id)
        self.assertNotEqual(simulate(self.plan, self.ctx).simulation_id, simulate(p2, self.ctx).simulation_id)

    def test_different_requirements_different_run_id(self):
        t = simulate(self.plan, self.ctx)
        loose, tight = Requirements(max_shift_hours=3.0), Requirements(max_shift_hours=0.25)
        v1, v2 = verify(t, loose, self.ctx, self.plan), verify(t, tight, self.ctx, self.plan)
        self.assertEqual(v1.simulation_id, v2.simulation_id)
        self.assertNotEqual(v1.run_id, v2.run_id)
        self.assertNotEqual(v1.passed, v2.passed)
        self.assertEqual(v2.requirements, tight)


class Finding4ScopeIsExplicit(unittest.TestCase):
    def setUp(self):
        self.ctx, self.plan = load_context(CTX), load_plan(C0 / "plan-sequential.json")

    def test_unsupported_phases_rejected(self):
        for phase in ("forecast_downside", "actual"):
            with self.assertRaises(ContractError, msg=phase):
                simulate(self.plan, self.ctx, phase)

    def test_actual_scenario_rejected(self):
        with self.assertRaises(ContractError):
            simulate(self.plan, self.ctx, "forecast_expected", ActualScenario(actual_pv_kw=[0.0] * 96))

    def test_nonzero_essential_load_rejected(self):
        d = ctx_dict()
        d["essential_load_kw"] = [0.1] * 96
        with self.assertRaises(ContractError):
            simulate(self.plan, context_from_dict(with_hashes(d)))

    def test_pass_carries_incomplete_coverage(self):
        _, v = run_fixture_full(CTX, C0 / "plan-sequential.json", REQ)
        self.assertTrue(v.passed)
        self.assertFalse(v.coverage.complete)
        self.assertEqual(v.coverage.phases_checked, ["forecast_expected"])
        self.assertIn("forecast_downside", v.coverage.not_modeled)

    def test_trace_marks_battery_unmodeled(self):
        t = simulate(self.plan, self.ctx)
        self.assertIsNone(t.battery_kwh)
        self.assertIsNone(t.unserved_cooking_kwh)


class BillingBoundaries(unittest.TestCase):
    def test_true_half_cent_rounds_up(self):  # Decimal half-up, not float round()
        self.assertEqual(quote(1.0, 0, 1, [0.02] * 96), 0.01)     # 0.005 -> 0.01
        self.assertEqual(quote(1.0, 0, 1, [0.06] * 96), 0.02)     # 0.015 -> 0.02
        self.assertEqual(quote(0.3, 0, 1, [0.1] * 96), 0.01)      # 0.0075 -> 0.01

    def test_cross_window_session_integrates_each_interval(self):
        ctx = load_context(CTX)
        tariff = {"standard": 8.0, "sun": 4.0, "peak": 13.0}
        # 1 kW from 15:30 to 16:30: steps 62-63 sun (4/kWh), 64-65 peak (13/kWh)
        self.assertEqual(quote_for(1.0, 62, 66, tariff, ctx), 1.0 + 1.0 + 3.25 + 3.25)


if __name__ == "__main__":
    unittest.main()
