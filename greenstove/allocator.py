"""Deterministic capacity offers for cooking at home, before battery modeling.

Reservations allocate electrical capacity, not shared appliances. Greedy allocation
runs in two passes (review decision D1, option 3): first every auto-booking household,
then every household that needs approval, each pass in (preferred_start, request_id)
order. Within a household's window, rank starts by rounded session quote +
shift_penalty per hour moved, then by earliest start.

Pending offers are made only from capacity left after all auto-bookers, and they still
hold that capacity, so a later approval can never overbook and a pending offer can never
displace a household that can actually cook. They are never accepted or dispatched
without household authority. Every call starts fresh; it is not an incremental
booking/approval service.
"""
from __future__ import annotations

import math
from decimal import Decimal

from .billing import quote, step_prices
from .models import (
    UNSERVED_BUDGET,
    UNSERVED_CAPACITY,
    ContractError,
    HouseholdRequest,
    Plan,
    Reservation,
    ScenarioContext,
    Unserved,
    normalize_tariff,
    tariff_version,
)
from .tariff import validate_tariff


def _validated_tariff(tariff: dict[str, float], context: ScenarioContext) -> dict[str, float]:
    if not isinstance(tariff, dict) or any(
        not isinstance(k, str)
        or isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        for k, v in tariff.items()
    ):
        raise ContractError("INVALID_TARIFF: prices must be finite numeric values keyed by window")
    normalized = normalize_tariff(tariff)
    errors = validate_tariff(normalized, context)
    if errors:
        raise ContractError("INVALID_TARIFF: " + "; ".join(e.message for e in errors))
    return normalized


def _validated_requests(
    requests: list[HouseholdRequest], context: ScenarioContext,
) -> list[HouseholdRequest]:
    """Reject duplicate, unknown or altered requests rather than allocate forged needs."""
    authoritative = {r.request_id: r for r in context.requests}
    seen: set[str] = set()
    checked = []
    for request in requests:
        validated = HouseholdRequest.model_validate(request.model_dump())
        if validated.request_id in seen:
            raise ContractError(f"duplicate request_id: {validated.request_id}")
        if validated.request_id not in authoritative:
            raise ContractError(f"unknown request_id: {validated.request_id}")
        if validated.model_dump() != authoritative[validated.request_id].model_dump():
            raise ContractError(f"request {validated.request_id} differs from the scenario")
        seen.add(validated.request_id)
        checked.append(validated)
    # Auto-bookers first (False sorts before True), then pending; time order within each.
    return sorted(checked, key=lambda r: (not r.auto_book, r.preferred_start, r.request_id))


def allocate(
    requests: list[HouseholdRequest],
    tariff: dict[str, float],
    context: ScenarioContext,
    proposal_id: str,
    *,
    max_shift_hours: float | None = None,
) -> Plan:
    """Build deterministic accepted reservations or pending offers under power limits.

    Subsets of context requests are allowed; the verifier still measures service
    against the full scenario. Invalid contracts raise ContractError; an ordinary
    budget/capacity rejection is recorded in plan.unserved as UNSERVED_BUDGET or
    UNSERVED_CAPACITY. No input is mutated. Essential/background demand is included in
    capacity accounting, but this stage does not establish energy availability, battery
    reserve or complete verification.

    max_shift_hours (keyword-only, optional): pass requirements.max_shift_hours so the
    allocator never picks a start the verifier would reject for shift. The preferred
    start (shift 0) always remains a candidate. None keeps household windows as the
    only limit, as before.
    """
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ContractError("proposal_id must be a non-empty string")
    if max_shift_hours is not None and (
        isinstance(max_shift_hours, bool) or not isinstance(max_shift_hours, (int, float))
        or not math.isfinite(max_shift_hours) or max_shift_hours < 0
    ):
        raise ContractError("max_shift_hours must be a finite non-negative number or None")
    max_shift = None if max_shift_hours is None else Decimal(str(max_shift_hours))
    normalized = _validated_tariff(tariff, context)
    ordered = _validated_requests(requests, context)
    prices = step_prices(normalized, context)
    version = tariff_version(normalized)
    load = [Decimal(str(kw)) for kw in context.essential_load_kw]
    limit = Decimal(str(context.inverter_kw))
    reservations: list[Reservation] = []
    unserved: list[Unserved] = []

    for request in ordered:
        ranked = []
        last_start = request.latest_finish - request.duration_steps
        for start in range(request.earliest_start, last_start + 1):
            shift_hours = Decimal(abs(start - request.preferred_start)) * Decimal("0.25")
            if max_shift is not None and shift_hours > max_shift:
                continue
            cost = quote(request.power_kw, start, start + request.duration_steps, prices)
            if Decimal(str(cost)) > Decimal(str(request.max_cost)):
                continue
            score = Decimal(str(cost)) + Decimal(str(request.shift_penalty)) * shift_hours
            ranked.append((score, start, cost))
        ranked.sort()
        if not ranked:
            unserved.append(Unserved(request_id=request.request_id, reason=UNSERVED_BUDGET))
            continue

        power = Decimal(str(request.power_kw))
        choice = next((entry for entry in ranked if all(
            load[t] + power <= limit
            for t in range(entry[1], entry[1] + request.duration_steps)
        )), None)
        if choice is None:
            unserved.append(Unserved(request_id=request.request_id, reason=UNSERVED_CAPACITY))
            continue

        _, start, cost = choice
        end = start + request.duration_steps
        for t in range(start, end):
            load[t] += power
        reservations.append(Reservation(
            reservation_id=f"{proposal_id}:{request.request_id}",
            request_id=request.request_id,
            start_step=start,
            end_step=end,
            power_kw=request.power_kw,
            locked_cost=cost,
            tariff_version=version,
            accepted=request.auto_book,
        ))

    return Plan(proposal_id=proposal_id, tariff=normalized, reservations=reservations, unserved=unserved)
