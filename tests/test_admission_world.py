"""Partner glue over Abel's admission ledger (GREENSTOVE_WORLD=abel-admission)."""
from greenstove.scenarios import DEFAULT_REQUIREMENTS, actual_for, lattice_tariffs, scenario
from greenstove.world_adapter import AdmissionWorld

W = AdmissionWorld(DEFAULT_REQUIREMENTS)


def check(name, tariff):
    c = scenario(name)
    p = W.allocate(c.requests, tariff, c, "p1")
    return c, p, [W.verify(W.simulate(p, c, ph), DEFAULT_REQUIREMENTS, c, p)
                  for ph in ("forecast_expected", "forecast_downside")]


def test_some_tariff_passes_every_phase_with_complete_coverage():
    c = scenario("dev-clear-full")
    passing = [t for t in lattice_tariffs(c) if all(v.passed for v in check("dev-clear-full", t)[2])]
    assert passing
    _, p, vs = check("dev-clear-full", passing[0])
    assert all(v.coverage.complete and "pending sign-off" in v.coverage.scope for v in vs)
    actual = W.verify(W.simulate(p, c, "actual", actual_for("dev-clear-full")), DEFAULT_REQUIREMENTS, c, p)
    assert actual.phase == "actual" and "min_battery_kwh" in actual.metrics


def test_refused_starts_become_feedback_and_never_breach_the_floor():
    c, p, vs = check("dev-overcast-low-battery", {"standard": 8.0, "sun": 4.0, "peak": 13.0})
    codes = {f.code for v in vs for f in v.feedback_codes}
    assert not all(v.passed for v in vs) and "BATTERY_RESERVE" in codes
    for v in vs:
        assert v.metrics["min_battery_kwh"] >= c.reserve_kwh - 1e-6          # admission protects the floor


def test_deterministic_and_predawn_infeasibility_proof():
    a = check("dev-clear-full", {"standard": 8.0, "sun": 4.0, "peak": 13.0})[2]
    b = check("dev-clear-full", {"standard": 8.0, "sun": 4.0, "peak": 13.0})[2]
    assert [v.run_id for v in a] == [v.run_id for v in b]
    proof = W.lower_bound(scenario("ho-clear-low-battery"), DEFAULT_REQUIREMENTS)
    assert proof is not None and proof.lower_bound_kwh > proof.available_kwh
    assert W.lower_bound(scenario("dev-clear-full"), DEFAULT_REQUIREMENTS) is None
