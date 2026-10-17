"""Seeded scenario generation for the development stub (greenstove/stub_env.py).

EVERY VALUE HERE IS SYNTHETIC. The station, PV profile, cloud factors, households,
budgets and prices are invented round numbers chosen so the stub has feasible,
price-sensitive and infeasible days. None of it comes from pilot records, and none
of it is Abel's world model; replace with his configuration when it lands.

Day layout: 96 fifteen-minute steps, partitioned into three contiguous fixed tariff
windows -- "standard" [0,40) 00:00-10:00, "sun" [40,64) 10:00-16:00 and
"peak" [64,96) 16:00-24:00. Tier order is sun <= standard <= peak.

Designed behaviour (checked by brute force in tests/test_stub_env.py):
* 6 of 8 households may auto-book, so DEFAULT_REQUIREMENTS' 0.75 service floor means
  every auto-bookable session must complete.
* Price levers: standard-minus-sun moves the breakfast household (hh-02) off the
  low-battery morning trough; peak-minus-sun moves flexible dinners into the sun
  window; peak 16 prices the rigid dinner household (hh-06) out of its budget.
* dev-overcast-low-battery fails the cheapest tariff but passes some; dev-rigid-high-
  demand fails all 27 without an energy proof; infeasible_context() has a proof.
Seeds: dev 0-99, demo 100-199; 1000+ is left for held-out evaluation.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import random

from greenstove.env_protocol import (
    STEPS, ActualScenario, HouseholdRequest, Requirements, ScenarioContext,
    TariffWindow,
)

STATION = {"inverter_kw": 3.0, "battery_capacity_kwh": 6.0, "reserve_kwh": 1.2,
           "pv_peak_kw": 2.4, "pv_first_step": 26, "pv_end_step": 74,  # 06:30-18:30
           "essential_kw": 0.12}                                        # clinic fridge stand-in
WINDOWS = [TariffWindow(name="standard", start_step=0, end_step=40),
           TariffWindow(name="sun", start_step=40, end_step=64),
           TariffWindow(name="peak", start_step=64, end_step=96)]
LATTICE = {"standard": [6.0, 8.0, 10.0], "sun": [3.0, 4.0, 5.0], "peak": [10.0, 13.0, 16.0]}
TIER_ORDER = ["sun", "standard", "peak"]
DOWNSIDE_FACTOR = 0.75
WEATHER_CLASSES = ("clear", "partly_cloudy", "overcast", "rainy_afternoon")

# Synthetic household profiles:
# (household_id, power_kw, duration_steps, preferred, earliest, latest_finish(excl),
#  max_cost, shift_penalty per hour, auto_book)
HOUSEHOLDS = [
    ("hh-01", 2.0, 2, 48, 46, 54, 8.0, 4.0, True),     # lunch, rigid 12:00
    ("hh-02", 1.2, 3, 32, 28, 56, 8.0, 2.0, True),     # breakfast 08:00, flexible
    ("hh-03", 0.8, 4, 50, 44, 60, 6.0, 1.0, False),    # lunch, needs approval
    ("hh-04", 1.5, 3, 72, 50, 80, 20.0, 2.5, True),    # dinner, very flexible
    ("hh-05", 1.8, 3, 74, 54, 82, 24.0, 3.5, True),    # dinner, flexible, high penalty
    ("hh-06", 1.0, 4, 76, 72, 84, 15.0, 4.0, True),    # dinner, rigid, budget-capped
    ("hh-07", 1.6, 2, 78, 56, 84, 14.0, 1.9, True),    # dinner, flexible
    ("hh-08", 1.2, 3, 73, 68, 80, 12.0, 4.0, False),   # dinner, rigid, needs approval
]

# Fixed requirements for the stub (agents never relax these).
DEFAULT_REQUIREMENTS = Requirements(min_service_fraction=0.75, max_shift_hours=4.0,
                                    essential_unserved_kwh=0.0)

# name -> (seed, weather_class, battery_frac, demand_scale, flex_scale, day)
DEV_SCENARIOS = {
    "dev-clear-full": (1, "clear", 1.0, 1.0, 1.0, 0),
    "dev-partly-cloudy-mid": (2, "partly_cloudy", 0.6, 1.0, 1.0, 1),
    "dev-overcast-low-battery": (3, "overcast", 0.35, 1.0, 1.0, 2),
    "dev-rainy-afternoon": (4, "rainy_afternoon", 0.5, 1.0, 1.0, 3),
    "dev-rigid-high-demand": (5, "overcast", 0.45, 1.2, 0.5, 4),
}
DEMO_SCENARIOS = {
    "demo-handoff-overcast": (101, "overcast", 0.4, 1.0, 1.0, 10),
    "demo-clear-evening-rush": (102, "clear", 0.5, 1.15, 1.0, 11),
}


def config_hash() -> str:
    """Hash of station, windows, lattice and household profiles (not seed/weather)."""
    cfg = {"station": STATION, "windows": [w.model_dump() for w in WINDOWS],
           "lattice": LATTICE, "tier_order": TIER_ORDER, "households": HOUSEHOLDS,
           "downside_factor": DOWNSIDE_FACTOR}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]


def clear_sky_pv() -> list[float]:
    """Half-sine PV bell from 06:30 to 18:30 peaking at pv_peak_kw."""
    first, end, peak = STATION["pv_first_step"], STATION["pv_end_step"], STATION["pv_peak_kw"]
    span = end - first
    return [round(peak * math.sin(math.pi * (t + 0.5 - first) / span), 4) if first <= t < end
            else 0.0 for t in range(STEPS)]


def cloud_profile(weather_class: str, rng: random.Random) -> list[float]:
    """Multiplicative cloud factor per step, held constant within each hour."""
    if weather_class not in WEATHER_CLASSES:
        raise ValueError(f"unknown weather_class {weather_class!r}; use one of {WEATHER_CLASSES}")
    factors = []
    for hour in range(STEPS // 4):
        if weather_class == "clear":
            f = rng.uniform(0.95, 1.0)
        elif weather_class == "partly_cloudy":
            f = rng.uniform(0.6, 0.9)
        elif weather_class == "overcast":
            f = rng.uniform(0.55, 0.65)
        else:  # rainy_afternoon: clear morning, heavy cloud from step 52 (13:00)
            f = rng.uniform(0.9, 1.0) if hour * 4 < 52 else rng.uniform(0.1, 0.2)
        factors.extend([f] * 4)
    return factors


def _requests(demand_scale: float, flex_scale: float) -> list[HouseholdRequest]:
    out = []
    for hid, power, dur, pref, early, late, budget, penalty, auto in HOUSEHOLDS:
        early_s = max(0, pref - round((pref - early) * flex_scale))
        late_s = min(STEPS, pref + dur + round((late - pref - dur) * flex_scale))
        out.append(HouseholdRequest(
            request_id=f"req-{hid}", household_id=hid, power_kw=round(power * demand_scale, 2),
            duration_steps=dur, preferred_start=pref, earliest_start=early_s,
            latest_finish=late_s, max_cost=budget, shift_penalty=penalty, auto_book=auto))
    return out


def make_context(seed: int, weather_class: str, battery_frac: float, demand_scale: float = 1.0,
                 flex_scale: float = 1.0, day: int = 0,
                 scenario_id: str | None = None) -> ScenarioContext:
    """Build a seeded synthetic ScenarioContext (forecasts only; no actual weather)."""
    if not 0.0 <= battery_frac <= 1.0:
        raise ValueError("battery_frac must be within [0, 1]")
    rng = random.Random(seed)
    clouds = cloud_profile(weather_class, rng)
    forecast = [round(p * c, 4) for p, c in zip(clear_sky_pv(), clouds)]
    cap = STATION["battery_capacity_kwh"]
    sid = scenario_id or (f"stub-{weather_class}-s{seed}-b{battery_frac:g}"
                          f"-ds{demand_scale:g}-fs{flex_scale:g}-d{day}")
    return ScenarioContext(
        scenario_id=sid, config_hash=config_hash(), day=day, weather_class=weather_class,
        initial_battery_kwh=round(cap * battery_frac, 4), battery_capacity_kwh=cap,
        reserve_kwh=STATION["reserve_kwh"], inverter_kw=STATION["inverter_kw"],
        forecast_pv_kw=forecast,
        downside_pv_kw=[round(p * DOWNSIDE_FACTOR, 4) for p in forecast],
        essential_load_kw=[STATION["essential_kw"]] * STEPS,
        requests=_requests(demand_scale, flex_scale),
        windows=[w.model_copy() for w in WINDOWS],
        lattice={k: list(v) for k, v in LATTICE.items()}, tier_order=list(TIER_ORDER))


def make_actual(context: ScenarioContext, seed: int) -> ActualScenario:
    """Actual PV = forecast with seeded hourly noise in [-20%, +20%]. Evaluator-only."""
    rng = random.Random(f"actual:{context.scenario_id}:{seed}")
    noise = [n for _ in range(STEPS // 4) for n in [rng.uniform(-0.2, 0.2)] * 4]
    return ActualScenario(actual_pv_kw=[round(max(0.0, p * (1 + n)), 4)
                                        for p, n in zip(context.forecast_pv_kw, noise)])


def scenario(name: str) -> ScenarioContext:
    """Context for a named DEV_/DEMO_ scenario; scenario_id is the name."""
    spec = DEV_SCENARIOS.get(name) or DEMO_SCENARIOS.get(name)
    if spec is None:
        raise KeyError(f"unknown scenario {name!r}")
    seed, weather, frac, dscale, fscale, day = spec
    return make_context(seed, weather, frac, dscale, fscale, day, scenario_id=name)


def actual_for(name: str) -> ActualScenario:
    spec = DEV_SCENARIOS.get(name) or DEMO_SCENARIOS[name]
    return make_actual(scenario(name), spec[0])


def infeasible_context() -> ScenarioContext:
    """Rainy afternoon, nearly empty battery, heavy demand: energy cannot cover it."""
    return make_context(seed=9, weather_class="rainy_afternoon", battery_frac=0.05,
                        demand_scale=1.4, flex_scale=1.0, day=0,
                        scenario_id="fixture-infeasible-energy")


def lattice_tariffs(context: ScenarioContext | None = None) -> list[dict[str, float]]:
    """All tier-ordered lattice tariffs (27 here), in a fixed window-order enumeration."""
    lat = context.lattice if context else LATTICE
    order = context.tier_order if context else TIER_ORDER
    names = [w.name for w in (context.windows if context else WINDOWS)]
    combos = [dict(zip(names, c)) for c in itertools.product(*(lat[n] for n in names))]
    return [t for t in combos if all(t[a] <= t[b] for a, b in zip(order, order[1:]))]
