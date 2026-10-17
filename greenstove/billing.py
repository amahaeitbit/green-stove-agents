"""Deterministic pricing. One rounding rule, defined once.

A quote integrates power_kw * 0.25 h * price over every interval of the session, using
each interval's own window price (so sessions crossing windows are priced correctly), then
rounds the total half-up to 0.01 currency. Decimal arithmetic avoids float drift.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .models import STEPS, ContractError, ScenarioContext

CENT = Decimal("0.01")


def step_prices(tariff: dict[str, float], context: ScenarioContext) -> list[float]:
    if set(tariff) != {w.name for w in context.windows}:
        raise ContractError(f"tariff keys {sorted(tariff)} do not match windows {[w.name for w in context.windows]}")
    owner = {t: w.name for w in context.windows for t in range(w.start_step, w.end_step)}
    return [float(tariff[owner[t]]) for t in range(STEPS)]


def quote(power_kw: float, start_step: int, end_step: int, prices: list[float]) -> float:
    total = sum(
        (Decimal(str(power_kw)) * Decimal("0.25") * Decimal(str(prices[t])) for t in range(start_step, end_step)),
        Decimal(0),
    )
    return float(total.quantize(CENT, rounding=ROUND_HALF_UP))


def quote_for(power_kw: float, start_step: int, end_step: int, tariff: dict[str, float], context: ScenarioContext) -> float:
    return quote(power_kw, start_step, end_step, step_prices(tariff, context))
