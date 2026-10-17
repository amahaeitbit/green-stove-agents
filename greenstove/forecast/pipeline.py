"""Forecast pipeline: You.com search -> Nemotron extraction -> code verification -> PV profile.

A value is used only if its verbatim quote appears in a retrieved result and every number
it reports appears in that quote (the same evidence rule the Keeper applies to memory).
Records are saved and frozen for evaluation; live search never runs inside an evaluation.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from greenstove.env_protocol import ScenarioContext
from greenstove.forecast import solar, youcom
from greenstove.llm import LLMClient

RAIN_START_STEP = 52            # 13:00: afternoon showers pattern
DOWNSIDE_EXTRA_CLOUD = 0.25
CONDITION_CLOUD = [(r"thunder|storm|heavy rain", 0.95), (r"overcast", 0.9), (r"rain|shower|drizzle", 0.85),
                   (r"mostly cloudy", 0.75), (r"partly (cloudy|sunny)", 0.5), (r"cloudy", 0.85),
                   (r"mostly (sunny|clear)", 0.25), (r"sunny|clear", 0.1)]

SYSTEM = """You extract a weather forecast for a solar mini-grid from web search results.
Target: the place and date given. Use ONLY the provided results. For each day you report,
give source_url (exactly one of the result urls) and quote: a short passage copied
verbatim from that result that contains every number you report. Use null when a value
is not stated. Do not estimate. Reply with ONLY JSON:
{"days": [{"date": "YYYY-MM-DD", "condition": "<words from the quote>",
  "cloud_cover_pct": <number or null>, "precip_prob_pct": <number or null>,
  "source_url": "...", "quote": "..."}]}"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class DayValue(_Strict):
    date: str
    condition: str = Field(default="", max_length=120)
    cloud_cover_pct: float | None = Field(default=None, ge=0, le=100)
    precip_prob_pct: float | None = Field(default=None, ge=0, le=100)
    source_url: str
    quote: str = Field(max_length=400)


class ForecastExtraction(_Strict):
    days: list[DayValue] = Field(default_factory=list, max_length=7)


class ForecastRecord(_Strict):
    place: str
    lat: float
    lon: float
    tz_hours: float
    date: str
    kwp: float
    derate: float
    queries: list[str]
    search_mode: str
    extractor_mode: str
    retrieved_at: str
    sources: list[dict[str, Any]]
    accepted: DayValue | None
    rejected: list[dict[str, Any]]
    status: str                                   # VERIFIED | NO_VERIFIED_FORECAST
    cloud_fraction: float
    precip_prob_pct: float | None
    weather_class: str
    forecast_pv_kw: list[float]
    downside_pv_kw: list[float]
    clear_sky_kwh: float
    pv_kwh: float
    downside_pv_kwh: float
    assumptions: list[str]
    calls: list[dict[str, Any]] = []


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _num_in(value: float, quote: str) -> bool:
    nums = {float(x) for x in re.findall(r"\d+(?:\.\d+)?", quote)}
    return any(abs(value - n) < 0.51 for n in nums)


def verify(ext: ForecastExtraction, hits: list[youcom.SearchHit], target: str) -> tuple[DayValue | None, list[dict]]:
    texts = {h.url: _norm(h.text) for h in hits}
    rejected, accepted = [], None
    for d in ext.days:
        reason = None
        if d.date != target:
            reason = "other_date"
        elif d.source_url not in texts:
            reason = "unknown_source"
        elif not d.quote.strip() or _norm(d.quote) not in texts[d.source_url]:
            reason = "quote_not_in_source"
        elif d.cloud_cover_pct is not None and not _num_in(d.cloud_cover_pct, d.quote):
            reason = "cloud_number_not_in_quote"
        elif d.precip_prob_pct is not None and not _num_in(d.precip_prob_pct, d.quote):
            reason = "rain_number_not_in_quote"
        elif d.cloud_cover_pct is None and condition_cloud(d.quote) is None:
            reason = "no_usable_value"
        if reason or accepted is not None:
            rejected.append({"value": d.model_dump(), "reason": reason or "extra_day_value"})
        else:
            accepted = d
    return accepted, rejected


def condition_cloud(text: str) -> float | None:
    t = text.lower()
    return next((c for pat, c in CONDITION_CLOUD if re.search(pat, t)), None)


def weather_class(cloud: float, rain: float | None) -> str:
    if rain is not None and rain >= 50:
        return "rainy_afternoon"
    return "clear" if cloud < 0.3 else ("partly_cloudy" if cloud < 0.6 else "overcast")


def cloud_steps(cloud: float, rain: float | None, extra: float = 0.0) -> list[float]:
    if rain is not None and rain >= 50:
        return [min(1.0, (cloud * 0.6 if s < RAIN_START_STEP else cloud + 0.3) + extra) for s in range(solar.STEPS)]
    return [min(1.0, cloud + extra)] * solar.STEPS


def mock_extractor(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic regex extraction from the retrieved text (mock mode only)."""
    days = []
    for hit in payload["results"]:
        text = hit["text"]
        cloud = re.search(r"[^.]*cloud cover (\d+)%[^.]*\.", text, re.I)
        rain = re.search(r"[^.]*(?:chance of rain|precipitation) (\d+)%[^.]*\.", text, re.I)
        cond = re.search(r"[^.:]*(?:cloudy|sunny|clear|showers|rain|overcast)[^.]*\.", text, re.I)
        if cloud or cond:
            quote = (cloud.group(0) if cloud else cond.group(0)).strip()
            days.append({"date": payload["target_date"], "condition": cond.group(0).strip()[:120] if cond else "",
                         "cloud_cover_pct": float(cloud.group(1)) if cloud else None,
                         "precip_prob_pct": None, "source_url": hit["url"], "quote": quote})
            if rain and days:
                days[-1]["precip_prob_pct"] = float(rain.group(1))
                days[-1]["quote"] = (quote + " " + rain.group(0).strip()) if rain.group(0).strip() not in quote else quote
            break
    return {"days": days}


def build_forecast(place: str, lat: float, lon: float, day: date, *, tz_hours: float = 3.0, kwp: float = 3.0,
                   derate: float = 0.8, search_mode: str = "mock", llm: LLMClient) -> ForecastRecord:
    label = day.strftime("%A %B %d %Y").replace(" 0", " ")
    queries = [f"{place} weather forecast {label} cloud cover chance of rain"]
    hits: list[youcom.SearchHit] = []
    for q in queries:
        hits += youcom.search(q, place=place, day_label=label, mode=search_mode).hits
    payload = {"place": place, "target_date": day.isoformat(),
               "results": [{"url": h.url, "title": h.title, "text": h.text[:1500]} for h in hits[:8]]}
    llm.reset_budget()
    ext, _, error = llm.complete_json("forecast_extractor", SYSTEM, payload, ForecastExtraction)
    accepted, rejected = verify(ext or ForecastExtraction(), hits, day.isoformat())
    if error:
        rejected.append({"value": None, "reason": f"extractor_error: {error[:120]}"})
    if accepted is not None:
        cloud = accepted.cloud_cover_pct / 100 if accepted.cloud_cover_pct is not None else condition_cloud(accepted.quote)
        rain, status = accepted.precip_prob_pct, "VERIFIED"
    else:
        cloud, rain, status = 0.6, None, "NO_VERIFIED_FORECAST"      # conservative default, labeled
    clear = solar.pv_profile(lat, lon, day, tz_hours, kwp, derate, [0.0] * solar.STEPS)
    pv = solar.pv_profile(lat, lon, day, tz_hours, kwp, derate, cloud_steps(cloud, rain))
    down = solar.pv_profile(lat, lon, day, tz_hours, kwp, derate, cloud_steps(cloud, rain, DOWNSIDE_EXTRA_CLOUD))
    return ForecastRecord(
        place=place, lat=lat, lon=lon, tz_hours=tz_hours, date=day.isoformat(), kwp=kwp, derate=derate,
        queries=queries, search_mode=search_mode, extractor_mode=llm.s.mode,
        retrieved_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        sources=[{"url": h.url, "title": h.title, "page_age": h.page_age} for h in hits[:8]],
        accepted=accepted, rejected=rejected, status=status, cloud_fraction=round(cloud, 3), precip_prob_pct=rain,
        weather_class=weather_class(cloud, rain), forecast_pv_kw=pv, downside_pv_kw=down,
        clear_sky_kwh=solar.kwh(clear), pv_kwh=solar.kwh(pv), downside_pv_kwh=solar.kwh(down),
        assumptions=["solar position: NOAA equations; clear sky: Haurwitz; cloud: Kasten-Czeplak",
                     f"horizontal PV {kwp} kWp, derate {derate}, timezone UTC{tz_hours:+g}",
                     "rain >= 50%: lighter cloud before 13:00, heavier after",
                     f"downside: cloud fraction +{DOWNSIDE_EXTRA_CLOUD}",
                     "web text supports an input assumption; it does not validate the simulator"]
                    + (["no verified value: conservative default cloud 0.6"] if status != "VERIFIED" else []),
        calls=[c.model_dump(mode="json") for c in llm.calls])


def save(record: ForecastRecord, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", record.place.lower()).strip("-")
    path = root / f"{slug}-{record.date}-{record.search_mode}.json"
    path.write_text(record.model_dump_json(indent=1))
    return path


def load(path: Path) -> ForecastRecord:
    return ForecastRecord.model_validate_json(Path(path).read_text())


def context_from_forecast(record: ForecastRecord, base: ScenarioContext) -> ScenarioContext:
    """Same station, households and rules; forecast PV and weather class from the record."""
    slug = re.sub(r"[^a-z0-9]+", "-", record.place.lower()).strip("-")
    from greenstove.env_protocol import with_scenario_hash
    return with_scenario_hash(base.model_copy(update={
        "scenario_id": f"fc-{slug}-{record.date}", "weather_class": record.weather_class,
        "forecast_pv_kw": record.forecast_pv_kw, "downside_pv_kw": record.downside_pv_kw}))


def summary_lines(record: ForecastRecord) -> list[str]:
    a = record.accepted
    lines = [f"{record.place} {record.date} · search {record.search_mode} · extractor {record.extractor_mode} · "
             f"status {record.status}",
             f"weather class {record.weather_class} · cloud {record.cloud_fraction:.2f} · rain "
             f"{record.precip_prob_pct if record.precip_prob_pct is not None else 'n/a'}%",
             f"PV {record.pv_kwh} kWh (downside {record.downside_pv_kwh}, clear sky {record.clear_sky_kwh}) "
             f"from {record.kwp} kWp"]
    if a:
        lines.append(f"source: {a.source_url} · quote: \"{a.quote}\"")
    lines += [f"rejected: {r['reason']}" for r in record.rejected]
    return lines


def to_json(record: ForecastRecord) -> str:
    return json.dumps(record.model_dump(mode="json"), indent=1)
