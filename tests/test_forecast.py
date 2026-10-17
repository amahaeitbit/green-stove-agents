from datetime import date

from greenstove.forecast import pipeline, solar
from greenstove.forecast.youcom import SearchHit
from greenstove.llm import LLMClient, LLMSettings

HIT = SearchHit(url="https://w.example/asmara", title="Asmara",
                text="Saturday: Partly cloudy. Cloud cover 40%. Chance of rain 20%.")


def test_clear_sky_is_physical():
    pv = solar.pv_profile(15.32, 38.93, date(2026, 10, 18), 3.0, 3.0, 0.8, [0.0] * 96)
    assert pv[8] == 0.0 and pv[88] == 0.0                     # 02:00 and 22:00 dark
    assert 2.0 < max(pv) < 2.4 and 12 < solar.kwh(pv) < 18     # 3 kWp x 0.8 in October sun
    assert solar.cloud_factor(1.0) == 0.25 and solar.cloud_factor(0.0) == 1.0


def day(**kw):
    base = dict(date="2026-10-18", condition="Partly cloudy", cloud_cover_pct=40, precip_prob_pct=20,
                source_url=HIT.url, quote="Cloud cover 40%. Chance of rain 20%.")
    return pipeline.DayValue(**{**base, **kw})


def test_verify_accepts_only_quoted_numbers_from_real_sources():
    ok, rej = pipeline.verify(pipeline.ForecastExtraction(days=[day()]), [HIT], "2026-10-18")
    assert ok and not rej
    for bad, reason in [(day(quote="Cloud cover 10%."), "quote_not_in_source"),
                        (day(cloud_cover_pct=70), "cloud_number_not_in_quote"),
                        (day(source_url="https://made.up/x"), "unknown_source"),
                        (day(date="2026-10-19"), "other_date")]:
        ok, rej = pipeline.verify(pipeline.ForecastExtraction(days=[bad]), [HIT], "2026-10-18")
        assert ok is None and rej[0]["reason"] == reason


def test_mock_pipeline_builds_labeled_record_and_context():
    from greenstove import cli
    from greenstove.scenarios import scenario
    llm = LLMClient(LLMSettings(mode="mock"), cli.MOCKS)
    rec = pipeline.build_forecast("Asmara, Eritrea", 15.32, 38.93, date(2026, 10, 18), search_mode="mock", llm=llm)
    assert rec.status == "VERIFIED" and rec.search_mode == "mock" and "example.invalid" in rec.sources[0]["url"]
    assert rec.weather_class == "rainy_afternoon" and rec.downside_pv_kwh < rec.pv_kwh < rec.clear_sky_kwh
    ctx = pipeline.context_from_forecast(rec, scenario("demo-handoff-overcast"))
    assert ctx.forecast_pv_kw == rec.forecast_pv_kw and ctx.weather_class == "rainy_afternoon"


def test_no_verified_value_is_labeled_and_conservative():
    def liar(payload):
        return {"days": [{"date": payload["target_date"], "condition": "sunny", "cloud_cover_pct": 5,
                          "precip_prob_pct": None, "source_url": payload["results"][0]["url"],
                          "quote": "Sunny all day, cloud cover 5%."}]}
    llm = LLMClient(LLMSettings(mode="mock"), {"forecast_extractor": liar})
    rec = pipeline.build_forecast("Asmara, Eritrea", 15.32, 38.93, date(2026, 10, 18), search_mode="mock", llm=llm)
    assert rec.status == "NO_VERIFIED_FORECAST" and rec.cloud_fraction == 0.6
    assert rec.rejected[0]["reason"] == "quote_not_in_source"
