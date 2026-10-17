"""Terminal demo for the partner's segments (see docs/demo-runbook.md).

Scenes: world -> proposal -> memory -> handoff -> infeasible. Every run is recorded to
data/demo/ and can be replayed with --replay (labeled RECORDED). The demo shows agent
behavior on one illustrative scenario; the evaluation table is the evidence.
"""
from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from greenstove.agent_models import ObservationProposal, RunRecord
from greenstove.env_protocol import STEP_HOURS, Environment, Requirements, ScenarioContext
from greenstove.pricing_agent import hhmm

BLOCKS = " ▁▂▃▄▅▆▇█"
PROPOSAL_SCENARIO = "dev-overcast-low-battery"   # first attempt usually fails here
HANDOFF_SCENARIO = "demo-handoff-overcast"       # not used during warm-up


def spark(values: list[float], top: float) -> str:
    return "".join(BLOCKS[min(8, max(0, round(8 * v / top)))] if top > 0 else " " for v in values)


def hourly(series: list[float]) -> list[float]:
    return [sum(series[i:i + 4]) / 4 for i in range(0, len(series), 4)]


def show_world(c: Console, ctx: ScenarioContext) -> None:
    c.print(Panel(f"Station: inverter {ctx.inverter_kw} kW · battery {ctx.battery_capacity_kwh} kWh "
                  f"(start {ctx.initial_battery_kwh:.1f}, reserve {ctx.reserve_kwh}) · weather forecast "
                  f"{ctx.weather_class} · windows " + ", ".join(
                      f"{w.name} {hhmm(w.start_step)}-{hhmm(w.end_step % 96)} {ctx.lattice[w.name]}" for w in ctx.windows),
                  title=f"World · {ctx.scenario_id}"))
    t = Table("household", "kW", "min", "preferred", "window", "budget", "auto-book")
    for r in ctx.requests:
        t.add_row(r.household_id, f"{r.power_kw:g}", str(r.duration_steps * 15), hhmm(r.preferred_start),
                  f"{hhmm(r.earliest_start)}-{hhmm(r.latest_finish % 96)}", f"{r.max_cost:g}",
                  "yes" if r.auto_book else "asks")
    c.print(t)


def show_run(c: Console, ctx: ScenarioContext, run: RunRecord, env: Environment, req: Requirements) -> None:
    if run.brief.entry_ids:
        c.print(Panel(escape(run.brief.text), title=f"Brief supplied ({run.brief.mode}, {run.brief.tokens}/"
                                            f"{run.brief.budget} tokens)"))
    t = Table("attempt", "prices", "cited memory", "expected", "downside", "feedback")
    for a in run.attempts:
        prices = " ".join(f"{k}={v:g}" for k, v in sorted(a.proposal.prices.items())) if a.proposal else "invalid"
        verdict = ["PASS" if a.passed else "FAIL"] * 2 if a.evidence_ids else ["-", "-"]
        t.add_row(str(a.n), prices, ",".join(a.proposal.cited_observation_ids) if a.proposal else "",
                  verdict[0], verdict[1], escape(", ".join(a.feedback or a.validation_errors)[:60]))
    c.print(t)
    tokens = sum(x.prompt_tokens + x.completion_tokens for x in run.calls)
    style = "green" if run.status == "PLAN_VERIFIED" else "red"
    c.print(f"[bold {style}]{run.status}[/] — {run.reason} · model calls {len(run.calls)} · tokens {tokens} · "
            f"candidate checks {run.candidate_evaluations} · simulator runs {run.simulator_runs}")
    if run.execution:
        c.print(f"EXECUTION_COMPLETE on actual simulated weather: outcome "
                f"{'PASS' if run.execution.outcome_passed else 'FAIL'}")
    if run.selected_tariff:
        plan = env.allocate(env.household_requests(ctx), run.selected_tariff, ctx, run.selected_proposal_id or "")
        r = Table("household", "start", "end", "kW", "locked cost", "status")
        for res in plan.reservations:
            hh = next(q.household_id for q in ctx.requests if q.request_id == res.request_id)
            r.add_row(hh, hhmm(res.start_step), hhmm(res.end_step % 96), f"{res.power_kw:g}",
                      f"{res.locked_cost:g}", "accepted" if res.accepted else "pending")
        for u in plan.unserved:
            r.add_row(u.request_id, "-", "-", "-", "-", f"unserved: {u.reason}")
        c.print(r)
        trace = env.simulate(plan, ctx, "forecast_downside")
        top = max(ctx.inverter_kw, max(trace.demand_kw))
        c.print(f"demand vs {ctx.inverter_kw} kW limit  |{spark(hourly(trace.demand_kw), top)}|  (00-24h, downside)")
        c.print(f"battery vs reserve {ctx.reserve_kwh} kWh |{spark(hourly(trace.battery_kwh), ctx.battery_capacity_kwh)}|"
                f"  min {min(trace.battery_kwh):.2f} kWh")


def show_notes(c: Console, run: RunRecord) -> None:
    t = Table("keeper decision", "entry", "reasons")
    for n in run.notes:
        t.add_row(n.action, n.observation_id or "-", escape("; ".join(n.reasons)[:80]))
    c.print(t)


def main(a) -> int:
    from greenstove import cli, supervisor
    from greenstove.brain import keeper
    from greenstove.env_protocol import load_environment
    from greenstove.scenarios import DEFAULT_REQUIREMENTS, DEMO_SCENARIOS, DEV_SCENARIOS, infeasible_context, \
        make_actual, make_context

    out_dir = cli.data_dir() / "demo"
    if a.replay:
        text = Path(a.replay).read_text()
        Console().print(Panel(f"RECORDED demo transcript: {a.replay}", style="bold magenta"))
        print(text)
        return 0
    c = Console(record=True)
    llm, env = cli.make_llm(a.mode), load_environment(prefer_stub=a.stub)
    cli.console = c
    cli.banner(llm, env.name)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if a.dev_brain:
        evidence, brain = cli.stores()
        c.print("memory: development brain from `warmup`")
    else:                                  # fresh workspace: scene 3 writes what scene 4 reads
        from greenstove.brain.store import BrainStore
        from greenstove.evidence import EvidenceStore
        work = out_dir / f"work-{stamp}"
        evidence, brain = EvidenceStore(work / "evidence"), BrainStore(work / "brain")
        c.print(f"memory: fresh demo workspace {work.relative_to(cli.ROOT)}")
    req = DEFAULT_REQUIREMENTS
    scenarios = {**DEV_SCENARIOS, **DEMO_SCENARIOS}
    pick = lambda name, pool: name if name in scenarios else next(iter(pool))  # noqa: E731
    pctx = cli.context_for(pick(PROPOSAL_SCENARIO, DEV_SCENARIOS))
    hctx = cli.context_for(pick(HANDOFF_SCENARIO, DEMO_SCENARIOS))
    scenes = ["world", "proposal", "memory", "handoff", "infeasible"] if a.scene == "all" else [a.scene]
    run = None
    for scene in scenes:
        c.rule(f"[bold]{scene}")
        if scene == "world":
            show_world(c, pctx)
        elif scene in ("proposal", "memory"):
            if run is None:
                run = supervisor.run_agent_day(pctx, "off", req, env=env, llm=llm, evidence=evidence, brain=brain,
                                               actual=make_actual(pctx, seed=pctx.day + 7))
            if scene == "proposal":
                show_run(c, pctx, run, env, req)
            else:
                show_notes(c, run)
                recs = [e for e in evidence.all() if e.agent_run_id == run.agent_run_id
                        and e.proposal_id == (run.selected_proposal_id or e.proposal_id)]
                if recs:
                    pid = recs[-1].proposal_id
                    ids = [e.run_id for e in recs if e.proposal_id == pid]
                    fake = ObservationProposal(evidence_ids=ids, situation_tags=[str(recs[-1].situation["weather_class"])],
                                               description="These prices served 0.99 of meals with 42 kWh spare.")
                    cfg = keeper.KeeperConfig(config_hash=pctx.config_hash, clock=pctx.day)
                    res = keeper.admit(fake, evidence, cfg, brain)
                    c.print(f"[magenta]TEST INJECTION[/] fabricated note -> [bold]{res.action}[/]: {escape('; '.join(res.reasons))}")
                c.print(f"brain now: {len(brain.entries())} entries · snapshot {brain.snapshot_hash()[:8]}")
        elif scene == "handoff":
            rows = []
            for mode in ("off", "curated"):
                r = supervisor.run_agent_day(hctx, mode, req, env=env, llm=llm, evidence=evidence, brain=brain,
                                             write_notes=False)
                rows.append((mode, r))
                if mode == "curated":
                    show_run(c, hctx, r, env, req)
            t = Table("fresh agent", "brief entries", "attempts", "model calls", "tokens", "status")
            for mode, r in rows:
                t.add_row(mode, ",".join(r.brief.entry_ids) or "-", str(len(r.attempts)), str(len(r.calls)),
                          str(sum(x.prompt_tokens + x.completion_tokens for x in r.calls)), r.status)
            c.print(t)
            c.print("[dim]One illustrative scenario; the held-out table is the evidence.[/]")
        elif scene == "infeasible":
            ictx = infeasible_context()
            r = supervisor.run_agent_day(ictx, "curated", req, env=env, llm=llm, evidence=evidence, brain=brain)
            p = r.infeasibility or {}
            c.print(f"[bold]{r.status}[/] — needs ≥ {p.get('lower_bound_kwh', 0):.2f} kWh, at most "
                    f"{p.get('available_kwh', 0):.2f} kWh available · model calls {len(r.calls)}")
            for line in p.get("assumptions", []):
                c.print(f"  · {line}")
        if a.pause and scene != scenes[-1]:
            input("[Enter] next scene ")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"demo-{llm.s.mode}-{stamp}.txt"
    c.save_text(str(path))
    c.print(f"recorded transcript: {path}")
    return 0
