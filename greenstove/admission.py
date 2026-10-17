"""Battery-backed start admission and immutable interval accounting.

Future PV receives zero admission credit. Essential demand is protected through step
96. Only admitted sessions commit stored energy; pending bookings do not authorize
starting. Pure transitions must be serialized by the supervisor (one ledger per grid).
"""
from decimal import Decimal
import math

from .admission_models import (
    AdmissionDecision, AdmissionState, BatterySpec, Commitment, IntervalResult,
)
from .models import ContractError, Plan, Requirements, Reservation, ScenarioContext, short_hash
from .simulator import check_plan
from .tariff import validate_tariff

ENERGY_TOL = Decimal("1e-12")
DT = Decimal("0.25")


def D(value):
    return Decimal(str(value))


def _scope(context: ScenarioContext, battery: BatterySpec) -> str:
    if battery.capacity_kwh != context.battery_capacity_kwh or battery.floor_kwh != context.reserve_kwh:
        raise ContractError("battery capacity/floor must match scenario configuration")
    return short_hash({"context": context.model_dump(mode="json"), "battery": battery.model_dump()})


def initial_state(context: ScenarioContext, battery: BatterySpec, *, start_step: int = 0) -> AdmissionState:
    """Initial battery is measured at start_step, allowing explicit late-day fixtures."""
    return AdmissionState(scope_id=_scope(context, battery), step=start_step,
                          battery_kwh=context.initial_battery_kwh)


def _check_state(state: AdmissionState, context: ScenarioContext, battery: BatterySpec):
    if state.scope_id != _scope(context, battery):
        raise ContractError("ledger belongs to another scenario or battery configuration")
    if state.battery_kwh > battery.capacity_kwh:
        raise ContractError("ledger battery exceeds capacity")
    ids = [c.request_id for c in state.commitments]
    if len(set(ids)) != len(ids) or any(not c.start_step <= state.step < c.end_step for c in state.commitments):
        raise ContractError("invalid active commitments")


def outstanding_storage_kwh(state: AdmissionState, battery: BatterySpec) -> float:
    """Battery-side remaining cooking commitment, not a second energy withdrawal."""
    return float(sum((D(c.power_kw) * DT * (c.end_step - state.step)
                      for c in state.commitments), Decimal(0)) / D(battery.discharge_efficiency))


def can_start(reservation: Reservation, tariff: dict[str, float], context: ScenarioContext,
              state: AdmissionState, battery: BatterySpec, *,
              requirements: Requirements | None = None) -> AdmissionDecision:
    """ADMIT returns the new ledger atomically; caller must persist it before next call.

    DEFER requests an alternative, not permission to retime a locked quote. A revised
    reservation must be explicitly authorized and repriced before retry. Manual approval
    is not implemented: only accepted auto-book requests may start in this increment.
    """
    _check_state(state, context, battery)
    requirements = requirements or Requirements()
    req = next((r for r in context.requests if r.request_id == reservation.request_id), None)
    if req is None:
        raise ContractError("reservation cites unknown household request")
    invalid = validate_tariff(tariff, context)
    if invalid:
        raise ContractError("INVALID_TARIFF: " + "; ".join(f.message for f in invalid))
    check_plan(Plan(proposal_id="admission", tariff=dict(tariff), reservations=[reservation]), context)
    available = max(Decimal(0), D(state.battery_kwh) - D(battery.floor_kwh))

    def decision(reason, *, required=Decimal(0), admit=False):
        status = "ADMIT" if admit else (
            "UNSERVED" if state.step >= req.latest_finish - req.duration_steps else "DEFER")
        next_state = state
        if admit:
            commitment = Commitment(reservation_id=reservation.reservation_id, request_id=req.request_id,
                                    start_step=state.step, end_step=reservation.end_step, power_kw=req.power_kw)
            next_state = state.model_copy(update={
                "commitments": (*state.commitments, commitment),
                "admitted_request_ids": (*state.admitted_request_ids, req.request_id),
                "admitted_reservation_ids": (*state.admitted_reservation_ids, reservation.reservation_id),
            })
        return AdmissionDecision(status=status, reason=reason, request_id=req.request_id,
                                 reservation_id=reservation.reservation_id, step=state.step,
                                 required_storage_kwh=float(required), available_storage_kwh=float(available),
                                 state=next_state)

    if (req.request_id in state.admitted_request_ids
            or reservation.reservation_id in state.admitted_reservation_ids):
        raise ContractError("request/reservation already admitted; do not dispatch twice")
    if not reservation.accepted or not req.auto_book:
        return decision("APPROVAL_REQUIRED")
    if state.step < reservation.start_step:
        return decision("NOT_START_TIME")
    if state.step > reservation.start_step:
        return decision("NEW_RESERVATION_REQUIRED")
    if not req.earliest_start <= state.step or reservation.end_step > req.latest_finish:
        return decision("OUTSIDE_WINDOW")
    if reservation.locked_cost > req.max_cost:
        return decision("BUDGET_EXCEEDED")
    if abs(state.step - req.preferred_start) * .25 > requirements.max_shift_hours:
        return decision("SHIFT_LIMIT")

    cooking = [Decimal(0)] * 96
    for c in state.commitments:
        for t in range(state.step, c.end_step):
            cooking[t] += D(c.power_kw)
    for t in range(state.step, reservation.end_step):
        cooking[t] += D(req.power_kw)
    for t in range(state.step, 96):
        demand = cooking[t] + D(context.essential_load_kw[t])
        if demand > D(context.inverter_kw):
            return decision("INVERTER_CAPACITY")
        if demand > D(battery.max_discharge_kw):
            return decision("BATTERY_POWER")
    required = sum((cooking[t] + D(context.essential_load_kw[t])
                    for t in range(state.step, 96)), Decimal(0)) * DT / D(battery.discharge_efficiency)
    if required > available + ENERGY_TOL:
        return decision("INSUFFICIENT_STORED_ENERGY", required=required)
    return decision("RESOURCES_COMMITTED", required=required, admit=True)


def advance_interval(state: AdmissionState, context: ScenarioContext, battery: BatterySpec,
                     *, pv_kw: float = 0.0) -> IntervalResult:
    """Consume one interval, release completed commitments, and charge from observed PV.

    Essential shortages are recorded explicitly. A shortfall against an admitted
    session raises ContractError rather than silently interrupting it or claiming it
    completed; it indicates broken ledger assumptions or invalid external state.
    """
    _check_state(state, context, battery)
    if state.step == 96:
        raise ContractError("simulation day already ended")
    if isinstance(pv_kw, bool) or not isinstance(pv_kw, (int, float)) or not math.isfinite(pv_kw) or pv_kw < 0:
        raise ContractError("observed PV must be finite and non-negative")
    t = state.step
    stored, floor, cap = D(state.battery_kwh), D(battery.floor_kwh), D(battery.capacity_kwh)
    eta = D(battery.discharge_efficiency)
    cooking = sum((D(c.power_kw) for c in state.commitments), Decimal(0))
    essential = D(context.essential_load_kw[t])
    pv = D(pv_kw)
    discharge_kw = min(D(battery.max_discharge_kw), max(Decimal(0), stored - floor) * eta / DT)
    available_kw = min(D(context.inverter_kw), pv + discharge_kw)
    essential_served = min(essential, available_kw)
    if cooking > available_kw - essential_served + ENERGY_TOL:
        raise ContractError("admitted cooking commitment cannot be supplied; ledger assumptions violated")
    delivered_kw = essential_served + cooking
    pv_used = min(pv, delivered_kw)
    withdrawal = (delivered_kw - pv_used) * DT / eta
    stored -= withdrawal
    if stored < floor and floor - stored <= ENERGY_TOL:
        stored = floor
    charge_input = min(max(Decimal(0), pv - pv_used), D(battery.max_charge_kw),
                       max(Decimal(0), cap - stored) / DT / D(battery.charge_efficiency))
    charged = charge_input * DT * D(battery.charge_efficiency)
    stored += charged
    shortage = (essential - essential_served) * DT
    completed = tuple(c.request_id for c in state.commitments if c.end_step == t + 1)
    next_state = state.model_copy(update={
        "step": t + 1, "battery_kwh": float(stored),
        "commitments": tuple(c for c in state.commitments if c.end_step > t + 1),
        "completed_request_ids": (*state.completed_request_ids, *completed),
        "delivered_cooking_kwh": state.delivered_cooking_kwh + float(cooking * DT),
        "unserved_essential_kwh": state.unserved_essential_kwh + float(shortage),
    })
    return IntervalResult(step=t, state=next_state, served_essential_kwh=float(essential_served * DT),
                          delivered_cooking_kwh=float(cooking * DT), unserved_essential_kwh=float(shortage),
                          pv_to_load_kwh=float(pv_used * DT), battery_withdrawal_kwh=float(withdrawal),
                          battery_charge_kwh=float(charged),
                          curtailed_pv_kwh=float((pv - pv_used - charge_input) * DT))
