from greenstove.agent_models import Brief, PricingOutput
from greenstove.env_protocol import HouseholdRequest, ScenarioContext, TariffWindow
from greenstove.pricing_agent import build_payload, mock_pricing, validate


def ctx():
    return ScenarioContext(
        scenario_id="t", config_hash="c", scenario_hash="s", weather_class="overcast", initial_battery_kwh=2, battery_capacity_kwh=6,
        reserve_kwh=1.2, inverter_kw=3, forecast_pv_kw=[1.0] * 96, downside_pv_kw=[0.75] * 96,
        essential_load_kw=[0.12] * 96,
        requests=[HouseholdRequest(request_id="r1", household_id="h1", power_kw=1, duration_steps=2,
                                   preferred_start=72, earliest_start=56, latest_finish=84, max_cost=20,
                                   shift_penalty=2, auto_book=True)],
        windows=[TariffWindow(name="standard", start_step=0, end_step=40),
                 TariffWindow(name="sun", start_step=40, end_step=64),
                 TariffWindow(name="peak", start_step=64, end_step=96)],
        lattice={"standard": [6, 8, 10], "sun": [3, 4, 5], "peak": [10, 13, 16]},
        tier_order=["sun", "standard", "peak"])


def test_validate_lattice_order_and_coverage():
    c = ctx()
    assert validate(PricingOutput(prices={"sun": 4, "standard": 8, "peak": 13}), c) == []
    assert validate(PricingOutput(prices={"sun": 4.5, "standard": 8, "peak": 13}), c)       # off lattice
    assert validate(PricingOutput(prices={"sun": 4, "standard": 8}), c)                     # missing window
    assert validate(PricingOutput(prices={"sun": 5, "standard": 6, "peak": 10}), c) == []
    bad = ctx(); bad.lattice["standard"] = [2, 8, 10]
    assert validate(PricingOutput(prices={"sun": 4, "standard": 2, "peak": 13}), bad)       # tier order


def test_payload_hides_actual_weather_and_carries_brief():
    p = build_payload(ctx(), Brief(role="pricing", mode="curated", text="SHARED BRAIN"), [])
    assert p["shared_memory_brief"] == "SHARED BRAIN" and "actual" not in str(p).lower()


def test_mock_reuses_passing_memory_and_skips_failed():
    c = ctx()
    brief = ("SHARED BRAIN\nobs-001 v1 | overcast | prices peak=13 standard=8 sun=4 | forecast FAIL BATTERY_RESERVE\n"
             "obs-002 v1 | overcast | prices peak=16 standard=8 sun=3 | forecast PASS")
    out = mock_pricing(build_payload(c, Brief(role="pricing", mode="curated", text=brief), []))
    assert out["prices"] == {"peak": 16.0, "standard": 8.0, "sun": 3.0} and out["cited_observation_ids"] == ["obs-002"]


def test_mock_reacts_to_feedback_and_never_repeats():
    c = ctx()
    fb = [{"attempt": 1, "prices": {"sun": 4, "standard": 8, "peak": 13},
           "results": [{"phase": "forecast_downside", "passed": False, "codes": ["BATTERY_RESERVE"]}]}]
    out = mock_pricing(build_payload(c, Brief(role="pricing", mode="off"), fb))
    assert out["prices"] != fb[0]["prices"] and out["prices"]["peak"] == 16 and out["prices"]["sun"] == 3
    assert validate(PricingOutput(**out), c) == []
