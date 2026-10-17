"""The day's decision record for Green-Stove operators, built from one agent run.

Everything here is recomputed by code from the run's evidence; the model's own words
appear only as its rationale, clearly attributed.
"""
from __future__ import annotations

from greenstove.agent_models import RunRecord
from greenstove.env_protocol import STEP_HOURS, Environment, Requirements, ScenarioContext
from greenstove.pricing_agent import hhmm


def _window_text(ctx: ScenarioContext, tariff: dict[str, float]) -> str:
    return " · ".join(f"{w.name} {tariff[w.name]:g}/kWh ({hhmm(w.start_step)}–{hhmm(w.end_step % 96)})"
                      for w in sorted(ctx.windows, key=lambda w: w.start_step))


def render_decision(ctx: ScenarioContext, run: RunRecord, env: Environment, req: Requirements,
                    memory_lines: dict[str, str] | None = None) -> str:
    sit_batt = ctx.initial_battery_kwh / ctx.battery_capacity_kwh
    out = [f"# Day decision · {ctx.scenario_id}", "",
           f"Forecast **{ctx.weather_class}**, battery starts at {sit_batt:.0%} "
           f"({ctx.initial_battery_kwh:.1f} of {ctx.battery_capacity_kwh:g} kWh, reserve {ctx.reserve_kwh:g}), "
           f"{len(ctx.requests)} household requests. Model: {run.llm_mode}; world: {run.env_name}; "
           f"memory: {run.brief.mode}.", ""]
    out += [f"**Status: {run.status}**: {run.reason}."]
    if run.execution:
        out += [f"Actual simulated day: **EXECUTION_COMPLETE**, outcome "
                f"**{'PASS' if run.execution.outcome_passed else 'FAIL'}** "
                f"(served {run.execution.metrics.get('served_fraction', 0):.2f}, "
                f"min battery {run.execution.metrics.get('min_battery_kwh', 0):.2f} kWh)."]
    if run.status == "INFEASIBLE" and run.infeasibility:
        p = run.infeasibility
        out += ["", f"No tariff can work today: at least {p['lower_bound_kwh']:.2f} kWh is needed and at most "
                f"{p['available_kwh']:.2f} kWh is available. No model calls were spent.", ""]
        out += [f"- {a}" for a in p.get("assumptions", [])]
        out += ["", "**Needs a human:** the committee decides which requests to defer; the agents will not "
                "relax any requirement."]
        return "\n".join(out) + "\n"
    if not run.selected_tariff:
        out += ["", "No verified plan within the attempt budget. Nothing is dispatched; the operator keeps "
                "yesterday's arrangement or decides manually."]
    else:
        sel = next(a for a in run.attempts if a.proposal and a.proposal.proposal_id == run.selected_proposal_id)
        out += ["", "## Today's tariff", "", _window_text(ctx, run.selected_tariff), ""]
        if sel.proposal.rationale:
            out += [f"Pricing Agent's rationale (model text): \"{sel.proposal.rationale}\""]
        if sel.proposal.cited_observation_ids:
            out += ["", "Memory it relied on (from the brief it was given):"]
            for i in sel.proposal.cited_observation_ids:
                out += [f"- `{i}`" + (f": {memory_lines[i]}" if memory_lines and i in memory_lines else "")]
        plan = env.allocate(env.household_requests(ctx), run.selected_tariff, ctx, run.selected_proposal_id or "")
        out += ["", "## Checks (recomputed by code)", "", "| Forecast | Result | Served | Min battery | Max shift | Spend |",
                "| --- | --- | --- | --- | --- | --- |"]
        for ph in ("forecast_expected", "forecast_downside"):
            v = env.verify(env.simulate(plan, ctx, ph), req, ctx, plan)
            m = v.metrics
            out += [f"| {ph.split('_')[1]} | {'PASS' if v.passed else 'FAIL'} | {m.get('served_fraction', 0):.2f} | "
                    f"{m.get('min_battery_kwh', 0):.2f} kWh | {m.get('max_shift_hours', 0):.2f} h | "
                    f"{m.get('total_spend', 0):.2f} |"]
        out += ["", "## Bookings", "", "| Household | Slot | kW | Locked cost | Status |", "| --- | --- | --- | --- | --- |"]
        hh = {q.request_id: q for q in ctx.requests}
        for r in sorted(plan.reservations, key=lambda r: r.start_step):
            out += [f"| {hh[r.request_id].household_id} | {hhmm(r.start_step)}–{hhmm(r.end_step % 96)} | {r.power_kw:g} | "
                    f"{r.locked_cost:g} | {'booked' if r.accepted else 'offer, awaiting household'} |"]
        pending = [hh[r.request_id].household_id for r in plan.reservations if not r.accepted]
        out += ["", "## Needs a human"]
        out += [f"- Households that must confirm their offer: {', '.join(pending)}" if pending else "- No pending offers."]
        out += [f"- Unserved: {u.request_id} ({u.reason})" for u in plan.unserved] or ["- No unserved requests."]
    failed = [a for a in run.attempts if not a.passed]
    if failed:
        out += ["", "## Alternatives rejected today", ""]
        for a in failed:
            prices = " ".join(f"{k}={v:g}" for k, v in sorted(a.proposal.prices.items())) if a.proposal else "invalid"
            out += [f"- attempt {a.n}: {prices} → {', '.join(a.feedback or a.validation_errors)}"]
    tokens = sum(c.prompt_tokens + c.completion_tokens for c in run.calls)
    out += ["", "## Cost and provenance", "",
            f"Model calls {len(run.calls)} · tokens {tokens} · candidate checks {run.candidate_evaluations} · "
            f"simulator runs {run.simulator_runs} · brief {run.brief.tokens}/{run.brief.budget} tokens, "
            f"entries {', '.join(run.brief.entry_ids) or 'none'} · run `{run.agent_run_id}`.",
            "", "Not decided by the model: tariff windows, the allowed price list, the battery reserve, the "
            f"requirements (served ≥ {req.min_service_fraction:g}, shift ≤ {req.max_shift_hours:g} h, essential "
            "load always served) and household preferences."]
    return "\n".join(out) + "\n"


def brief_lines(brief_text: str) -> dict[str, str]:
    """Map entry id -> its brief line, so the decision shows what the agent actually read."""
    lines = {}
    for line in brief_text.splitlines():
        head = line.split(" ", 1)[0]
        if head.startswith("obs-"):
            lines[head] = line.split("|", 1)[1].strip() if "|" in line else line
    return lines
