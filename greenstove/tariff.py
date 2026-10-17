"""Tariff validation against the context's fixed windows, lattice and tier order.

Returns every problem at once as INVALID_TARIFF feedback (empty list = valid).
"""
from __future__ import annotations

from .models import Feedback, ScenarioContext

TOL = 1e-9


def validate_tariff(tariff: dict[str, float], context: ScenarioContext) -> list[Feedback]:
    problems: list[str] = []
    names = [w.name for w in context.windows]
    missing, extra = sorted(set(names) - set(tariff)), sorted(set(tariff) - set(names))
    if missing:
        problems.append(f"missing prices for windows {missing}")
    if extra:
        problems.append(f"unknown windows {extra}")
    for name in names:
        if name in tariff and not any(abs(float(tariff[name]) - p) <= TOL for p in context.lattice[name]):
            problems.append(f"{name} price {tariff[name]} is not in the lattice {context.lattice[name]}")
    order = context.tier_order
    if not missing:
        for a, b in zip(order, order[1:]):
            if float(tariff[a]) > float(tariff[b]) + TOL:
                problems.append(f"tier order violated: {a} {tariff[a]} > {b} {tariff[b]}")
    return [Feedback(code="INVALID_TARIFF", message=m) for m in problems]
