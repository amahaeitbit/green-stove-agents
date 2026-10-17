"""Pricing Agent: proposes one lattice-valid three-tier tariff per attempt.

It sees current facts, household request constraints, a memory brief and current-run
feedback. It cannot change windows, bounds, requirements or household preferences.
"""
from __future__ import annotations

import re
from typing import Any

from greenstove.agent_models import Brief, CallRecord, PricingOutput, TariffProposal
from greenstove.env_protocol import STEP_HOURS, ScenarioContext
from greenstove.llm import LLMClient

SYSTEM = """You are the Pricing Agent for a simulated community solar cooking station.
Each day you choose one price per fixed tariff window so that household cooking demand
fits the station: inverter power limit, battery reserve (the essential load must never go
unserved), household budgets and the required share of served cooking sessions.
Households pick the start that minimizes their cost plus inconvenience inside their own
acceptable window, so cheaper windows attract demand. Rules you cannot change: the
windows, the allowed price list for each window, the tier order (prices must be
non-decreasing in tier_order), the requirements and household preferences.
Use the shared memory brief (verified observations from earlier runs) and the feedback
from earlier attempts today. Cite the ids of memory entries you actually relied on.
Reply with ONLY a JSON object:
{"prices": {"<window>": <price from that window's allowed list>, ...},
 "cited_observation_ids": ["obs-001", ...], "rationale": "<one or two sentences>"}"""


def hhmm(step: int) -> str:
    return f"{(step * 15) // 60:02d}:{(step * 15) % 60:02d}"


def build_payload(context: ScenarioContext, brief: Brief, feedback: list[dict[str, Any]]) -> dict[str, Any]:
    """Planner-visible facts only: never actual weather or evaluation outcomes."""
    windows = []
    for w in context.windows:
        sl = slice(w.start_step, w.end_step)
        windows.append({
            "name": w.name, "start": hhmm(w.start_step), "end": hhmm(w.end_step % 96) if w.end_step < 96 else "24:00",
            "allowed_prices": context.lattice[w.name],
            "pv_expected_kwh": round(sum(context.forecast_pv_kw[sl]) * STEP_HOURS, 2),
            "pv_downside_kwh": round(sum(context.downside_pv_kw[sl]) * STEP_HOURS, 2),
            "essential_kwh": round(sum(context.essential_load_kw[sl]) * STEP_HOURS, 2),
        })
    requests = [{
        "household": r.household_id, "power_kw": r.power_kw, "minutes": r.duration_steps * 15,
        "preferred": hhmm(r.preferred_start), "earliest": hhmm(r.earliest_start),
        "latest_finish": hhmm(r.latest_finish) if r.latest_finish < 96 else "24:00",
        "max_cost": r.max_cost, "shift_penalty_per_hour": r.shift_penalty, "auto_book": r.auto_book,
    } for r in context.requests]
    return {
        "scenario": context.scenario_id, "weather_forecast_class": context.weather_class,
        "station": {"inverter_kw": context.inverter_kw, "battery_capacity_kwh": context.battery_capacity_kwh,
                    "battery_start_kwh": context.initial_battery_kwh, "reserve_kwh": context.reserve_kwh},
        "tier_order": context.tier_order, "windows": windows, "requests": requests,
        "shared_memory_brief": brief.text or "(none)",
        "earlier_attempts_today": feedback,
    }


def validate(out: PricingOutput, context: ScenarioContext) -> list[str]:
    errors = []
    names = {w.name for w in context.windows}
    if set(out.prices) != names:
        errors.append(f"prices must cover exactly the windows {sorted(names)}")
    for name, price in out.prices.items():
        if name in context.lattice and price not in context.lattice[name]:
            errors.append(f"{name} price {price} not in allowed list {context.lattice[name]}")
    ordered = [out.prices.get(n) for n in context.tier_order if n in out.prices]
    if any(a > b for a, b in zip(ordered, ordered[1:])):
        errors.append(f"prices must be non-decreasing in tier order {context.tier_order}")
    return errors


def propose(context: ScenarioContext, brief: Brief, feedback: list[dict[str, Any]], llm: LLMClient,
            attempt: int, proposal_id: str) -> tuple[TariffProposal | None, list[str], list[CallRecord]]:
    """One proposal attempt. Invalid output counts as an attempt (returned as errors)."""
    payload = build_payload(context, brief, feedback)
    out, records, error = llm.complete_json("pricing", SYSTEM, payload, PricingOutput,
                                            memory_hash=brief.snapshot_hash, attempt=attempt)
    if out is None:
        return None, [error or "invalid output"], records
    errors = validate(out, context)
    if errors:
        return None, errors, records
    cited = [i for i in out.cited_observation_ids if i in brief.entry_ids]
    return TariffProposal(proposal_id=proposal_id, prices=dict(out.prices), cited_observation_ids=cited,
                          rationale=out.rationale), [], records


# ---- deterministic mock (rule-based; exercises plumbing and memory mechanics only) ----

_PAIR = re.compile(r"\b([a-z_]+)=(\d+(?:\.\d+)?)\b")


def _memory_entries(brief_text: str, names: set[str]) -> list[tuple[str, dict, bool, set[str]]]:
    """Brief lines in order (closest situation first): (id, tariff, passed, codes)."""
    out = []
    for line in brief_text.splitlines():
        prices = {k: float(v) for k, v in _PAIR.findall(line) if k in names}
        if set(prices) != names:
            continue
        failed = bool(re.search(r"\bFAIL\b|passed=False|\bfail\b", line))
        passed = not failed and bool(re.search(r"\bPASS\b|passed=True|\bpass\b", line))
        if failed or passed:
            codes = set(re.findall(r"\b[A-Z]+(?:_[A-Z]+)+\b", line))
            out.append((line.split()[0], prices, passed, codes))
    return out


def _adjust(prices: dict, codes: set[str], lattice: dict, order: list[str]) -> dict:
    prices, cheap, dear = dict(prices), order[0], order[-1]
    if codes & {"BATTERY_RESERVE", "INVERTER_LIMIT", "ESSENTIAL_UNSERVED"}:
        prices[dear] = _step(lattice[dear], prices[dear], +1)
        prices[cheap] = _step(lattice[cheap], prices[cheap], -1)
    if "BUDGET_EXCEEDED" in codes:
        prices[dear] = _step(lattice[dear], prices[dear], -1)
    if codes == {"SERVICE_SHORTFALL"}:
        prices[cheap] = _step(lattice[cheap], prices[cheap], -1)
    for n in order[1:-1]:
        prices[n] = max(prices[n], prices[cheap])
    return prices


def _step(lattice: list[float], price: float, delta: int) -> float:
    i = min(range(len(lattice)), key=lambda j: abs(lattice[j] - price))
    return lattice[max(0, min(len(lattice) - 1, i + delta))]


def mock_pricing(payload: dict[str, Any]) -> dict[str, Any]:
    """Closest verified pass among the top 3 memory entries, else adjust from the closest failure."""
    lattice = {w["name"]: w["allowed_prices"] for w in payload["windows"]}
    order, names = payload["tier_order"], set(lattice)
    entries = _memory_entries(payload["shared_memory_brief"], names)
    attempts = payload["earlier_attempts_today"]
    tried = {tuple(sorted(a["prices"].items())) for a in attempts if a.get("prices")}
    key = lambda p: tuple(sorted(p.items()))  # noqa: E731
    cited: list[str] = []
    if attempts:
        last = attempts[-1]
        base = last.get("prices") or {n: lattice[n][len(lattice[n]) // 2] for n in names}
        codes = {c for r in last.get("results", []) for c in r.get("codes", [])} | set(last.get("errors_codes", []))
        prices = _adjust(base, codes, lattice, order)
        rationale = f"Adjusted after {sorted(codes) or 'no codes'} on the previous attempt."
    else:
        failed_closer: set[tuple] = set()
        choice = None
        for eid, p, passed, _ in entries[:3]:
            if passed and key(p) not in failed_closer:
                choice = (eid, p)
                break
            if not passed:
                failed_closer.add(key(p))
        if choice:
            cited, prices = [choice[0]], dict(choice[1])
            rationale = f"Reused the closest verified observation {choice[0]}."
        elif entries:
            eid, p, _, codes = entries[0]
            cited, prices = [eid], _adjust(p, codes, lattice, order)
            rationale = f"Closest memory {eid} failed with {sorted(codes)}; adjusted away from it."
        else:
            prices = {n: lattice[n][len(lattice[n]) // 2] for n in names}
            rationale = "No applicable memory; starting from mid-lattice prices."
    if key(prices) in tried:
        mid = order[len(order) // 2]
        prices[mid] = _step(lattice[mid], prices[mid], +1)
    return {"prices": prices, "cited_observation_ids": cited, "rationale": rationale}
