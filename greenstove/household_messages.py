"""Per-household outcome messages routed by the authoritative request -> household map (review A3).

Built only from the scenario context (``context.requests`` is the routing table), the plan
(``reservations`` and allocator ``unserved``) and an admission trace (``trace.extra["admission"]``
decisions plus ``trace.reservation_outcomes``). Each message carries only its own household's
request, reservation, times and locked quote: never the ledger, another household's commitment,
or any aggregate. These are admission/execution notices, not PLAN_VERIFIED statuses.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from greenstove.models import UNSERVED_NOT_OFFERED, ContractError, Plan, ScenarioContext, Trace, step_label

Status = Literal["BOOKED_START_NOW", "WAIT", "APPROVAL_NEEDED", "COMPLETED", "NOT_SERVED", "NOT_OFFERED"]

_HEADLINE = {"BOOKED_START_NOW": "start now", "WAIT": "wait", "APPROVAL_NEEDED": "approval needed",
             "COMPLETED": "completed", "NOT_SERVED": "not served", "NOT_OFFERED": "not offered"}
_REASON_TEXT = {"RESOURCES_COMMITTED": "energy reserved for your session",
                "INSUFFICIENT_STORED_ENERGY": "insufficient stored energy",
                "APPROVAL_REQUIRED": "confirm the offer to book it",
                "NOT_START_TIME": "booked start not reached yet",
                "INVERTER_CAPACITY": "station power limit", "BATTERY_POWER": "battery power limit",
                "BUDGET_EXCEEDED": "over your budget", "OUTSIDE_WINDOW": "outside your time window",
                "SHIFT_LIMIT": "shift too far from your preferred time",
                "NEW_RESERVATION_REQUIRED": "booked start passed; a new offer is needed",
                "UNSERVED_CAPACITY": "no start fits the station capacity",
                "UNSERVED_BUDGET": "no start is affordable within your budget",
                UNSERVED_NOT_OFFERED: "no offer was made today", "INTERRUPTED": "session interrupted"}


class HouseholdMessage(BaseModel):
    """One household's notice. Every field refers to that household's own request only."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    household_id: str
    request_id: str
    reservation_id: str | None = None
    status: Status
    reason: str
    start: str | None = None          # HH:MM, booked start
    end: str | None = None            # HH:MM, exclusive end
    locked_quote: float | None = None


def _status(reservation, decisions: list[dict], outcome: str | None) -> tuple[str, str]:
    last = decisions[-1] if decisions else None
    admit = next((d for d in decisions if d["status"] == "ADMIT"), None)
    if outcome == "completed":
        return "COMPLETED", admit["reason"] if admit else "COMPLETED"
    if outcome == "interrupted":
        return "NOT_SERVED", "INTERRUPTED"
    if last is not None:
        if last["status"] == "ADMIT":
            return "BOOKED_START_NOW", last["reason"]
        if last["status"] == "UNSERVED" or outcome == "unserved":
            return "NOT_SERVED", last["reason"]
        return ("APPROVAL_NEEDED" if last["reason"] == "APPROVAL_REQUIRED" else "WAIT"), last["reason"]
    if not reservation.accepted:
        return "APPROVAL_NEEDED", "APPROVAL_REQUIRED"
    return "WAIT", "NOT_START_TIME"                                # booked start not reached in this trace


def household_outcomes(context: ScenarioContext, plan: Plan, trace: Trace) -> list[HouseholdMessage]:
    """One message per household request (one per household in every current scenario), in
    ``context.requests`` order. Reservations, unserved entries or decisions that cite a request
    outside the context are a contract break: they are never routed to anyone."""
    owner = {r.request_id: r.household_id for r in context.requests}
    reservations: dict[str, object] = {}
    for res in plan.reservations:
        if res.request_id not in owner:
            raise ContractError(f"reservation {res.reservation_id} cites unknown request {res.request_id}")
        if res.request_id in reservations:
            raise ContractError(f"request {res.request_id} has more than one reservation")
        reservations[res.request_id] = res
    by_reservation = {res.reservation_id: res for res in plan.reservations}
    decisions: dict[str, list[dict]] = {}
    for d in trace.extra.get("admission", []):
        res = by_reservation.get(d.get("reservation_id"))
        if res is None or d.get("request_id") != res.request_id:
            raise ContractError(f"admission decision {d.get('reservation_id')!r} does not match the plan")
        decisions.setdefault(res.reservation_id, []).append(d)
    unserved = {}
    for u in plan.unserved:
        if u.request_id not in owner:
            raise ContractError(f"unserved entry cites unknown request {u.request_id}")
        unserved[u.request_id] = u.reason

    out = []
    for req in context.requests:
        res = reservations.get(req.request_id)
        if res is None:
            status, reason = ("NOT_SERVED", unserved[req.request_id]) if req.request_id in unserved \
                else ("NOT_OFFERED", UNSERVED_NOT_OFFERED)
            out.append(HouseholdMessage(household_id=req.household_id, request_id=req.request_id,
                                        status=status, reason=reason))
            continue
        status, reason = _status(res, decisions.get(res.reservation_id, []),
                                 trace.reservation_outcomes.get(res.reservation_id))
        out.append(HouseholdMessage(household_id=req.household_id, request_id=req.request_id,
                                    reservation_id=res.reservation_id, status=status, reason=reason,
                                    start=step_label(res.start_step), end=step_label(res.end_step),
                                    locked_quote=res.locked_cost))         # step 96 renders as 24:00
    return out


def message_for(messages: list[HouseholdMessage], household_id: str) -> list[HouseholdMessage]:
    """Only what one household may see."""
    return [m for m in messages if m.household_id == household_id]


def execution_events(context: ScenarioContext, plan: Plan, trace: Trace) -> list[dict]:
    """Rebuild notices from VERIFIED actual decisions/outcomes, not arbitrary trace metadata.

    Call only after verification. These records replay the simulated execution; they
    are not device commands. Each record contains only its recipient's information.
    """
    if trace.phase != "actual":
        raise ContractError("household execution notices require actual phase")
    requests = {q.request_id: q for q in context.requests}
    reservations = {r.reservation_id: r for r in plan.reservations}
    events = []
    for d in trace.extra.get("admission", []):
        r = reservations[d["reservation_id"]]
        q = requests[r.request_id]
        status, reason = _status(r, [d], "unserved" if d["status"] == "UNSERVED" else None)
        message = HouseholdMessage(household_id=q.household_id, request_id=q.request_id,
                                    reservation_id=r.reservation_id, status=status, reason=reason,
                                    start=step_label(r.start_step), end=step_label(r.end_step), locked_quote=r.locked_cost)
        events.append({"step": d["step"], "kind": "finalise" if d.get("final") else "admission",
                       "request_id": q.request_id, "messages": [message.model_dump()]})
    for message in household_outcomes(context, plan, trace):
        if message.status == "COMPLETED":
            r = reservations[message.reservation_id]
            events.append({"step": r.end_step, "kind": "completed", "request_id": r.request_id,
                           "messages": [message.model_dump()]})
    return sorted(events, key=lambda e: (e["step"], e["kind"] != "completed", e["request_id"]))


def render_one(m: HouseholdMessage) -> str:
    """One SMS-length line (<= 160 characters) for one household."""
    parts = [f"{m.household_id}: {_HEADLINE[m.status]}"]
    parts.append("session delivered" if m.status == "COMPLETED"
                 else _REASON_TEXT.get(m.reason, m.reason.replace("_", " ").lower()))
    if m.start:
        parts.append(f"{m.start}-{m.end}")
    if m.locked_quote is not None:
        parts.append(f"quote {m.locked_quote:.2f}")
    if m.reservation_id:
        parts.append(f"ref {m.reservation_id}")
    line = " | ".join(parts)
    return line if len(line) <= 160 else line[:157] + "..."


def render(messages: list[HouseholdMessage]) -> list[str]:
    """One short line per message, each addressed to (and about) one household only."""
    return [render_one(m) for m in messages]
