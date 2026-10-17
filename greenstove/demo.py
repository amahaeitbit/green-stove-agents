"""Terminal demo for the partner's segments (see docs/demo-runbook.md).

Scenes: world -> proposal -> memory -> handoff -> infeasible. Every run is recorded to
data/demo/ and can be replayed with --replay (labeled RECORDED). The demo shows agent
behavior on one illustrative scenario; the evaluation table is the evidence.
"""
from __future__ import annotations

import json
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


def show_run(c: Console, ctx: ScenarioContext, run: RunRecord, env: Environment, req: Requirements,
             details: bool = True) -> None:
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
    if run.decisions:
        c.print("decisions: " + " → ".join(f"{d.decision}" for d in run.decisions)
                + f"  ({escape(run.decisions[-1].reason)})")
    tokens = sum(x.prompt_tokens + x.completion_tokens for x in run.calls)
    style = "green" if run.status == "PLAN_VERIFIED" else "red"
    c.print(f"[bold {style}]{run.status}[/] — {run.reason} · model calls {len(run.calls)} · tokens {tokens} · "
            f"candidate checks {run.candidate_evaluations} · simulator runs {run.simulator_runs}")
    if run.execution:
        c.print(f"EXECUTION_COMPLETE on actual simulated weather: outcome "
                f"{'PASS' if run.execution.outcome_passed else 'FAIL'}")
    if run.selected_tariff and details:
        plan = env.allocate(env.household_requests(ctx), run.selected_tariff, ctx, run.selected_proposal_id or "")
        r = Table("household", "start", "end", "kW", "locked cost", "status")
        for res in plan.reservations:
            hh = next(q.household_id for q in ctx.requests if q.request_id == res.request_id)
            r.add_row(hh, hhmm(res.start_step), hhmm(res.end_step % 96), f"{res.power_kw:g}",
                      f"{res.locked_cost:g}", "accepted" if res.accepted else "pending")
        for u in plan.unserved:
            r.add_row(u.request_id, "-", "-", "-", "-", f"unserved: {u.reason}")
        c.print(r)
        from greenstove.env_protocol import ContractError
        try:
            trace, label = env.simulate(plan, ctx, "forecast_downside"), "downside"
        except ContractError:
            trace, label = env.simulate(plan, ctx, "forecast_expected"), "expected; downside not modeled"
        top = max(ctx.inverter_kw, max(trace.demand_kw))
        c.print(f"demand vs {ctx.inverter_kw} kW limit  |{spark(hourly(trace.demand_kw), top)}|  (00-24h, {label})")
        if trace.battery_kwh:
            c.print(f"battery vs reserve {ctx.reserve_kwh} kWh |{spark(hourly(trace.battery_kwh), ctx.battery_capacity_kwh)}|"
                    f"  min {min(trace.battery_kwh):.2f} kWh")
        else:
            c.print("battery: not modeled by this world")


def show_spec(c: Console, dod) -> None:
    c.print(Panel(escape(dod.goal), title="One-line goal"))
    t = Table("check the agents will run without you", "source", "why", title=f"Definition of done · spec {dod.spec_hash()[:8]}")
    for ch in dod.checks:
        color = {"agent": "cyan", "code_added": "yellow", "code_tightened": "magenta"}[ch.source]
        t.add_row(f"{ch.metric} {ch.op} {ch.threshold:g}", f"[{color}]{ch.source}[/]", escape(ch.why))
    c.print(t)
    c.print("[dim]The Spec Agent writes the checks; code may tighten them, never loosen them.[/]")


def show_notes(c: Console, run: RunRecord) -> None:
    t = Table("keeper decision", "entry", "reasons")
    for n in run.notes:
        t.add_row(n.action, n.observation_id or "-", escape("; ".join(n.reasons)[:80]))
    c.print(t)


ACTS = {
    "goal": ("1", "One line becomes checks", "Can agents turn a vague goal into tests they run without us?",
             "Done Means Done: the agents wrote their own definition of done; code may tighten it, never loosen it."),
    "world": ("2", "The day ahead", "What does the mini-grid face today?",
              "Every household is different; the station's limits are fixed and enforced by code."),
    "proposal": ("3", "Agents propose, code decides", "Who decides whether a plan is good enough?",
                 "The model never grades itself: an independent verifier checks two forecasts, failures come back "
                 "as reasons, and the Supervisor decides continue, re-plan or stop."),
    "memory": ("4", "Only proven knowledge enters the Shared Brain", "How do we stop agents from remembering wrong things?",
               "Shared Brain: the Keeper rebuilds every fact from evidence; made-up numbers are rejected."),
    "handoff": ("5", "A fresh agent starts where the last one ended", "Does shared memory actually help?",
                "Shared Brain: a new agent read the memory, cited it, and needed fewer calls. "
                "One example; the held-out table is the evidence."),
    "tomorrow": ("6", "Tomorrow, planned on a real forecast", "Can the agents plan the next day from real-world data?",
                 "You.com found the forecast, Nemotron extracted it, code checked the quote, and a fresh agent with "
                 "the Shared Brain planned tomorrow on it."),
    "infeasible": ("7", "Knowing when to stop", "What if the day simply cannot be done?",
                   "Done Means Done: a calculation proves it before any model call, and a human is pinged; "
                   "no fake success."),
    "improve": ("8", "Version N+1: the team improves itself", "Does the team get better from its own history?",
                "A playbook change is kept only if it measurably helps, then checked on days it has never seen."),
}


def title_card(c: Console, llm, env) -> None:
    mode = "MOCK model (rule-based stand-in)" if llm.s.mode == "mock" else f"LIVE · Crusoe Managed Inference · {llm.s.model}"
    c.print(Panel(
        "[bold]GREEN-STOVE AGENTS[/]\n"
        "An agent team that runs a community solar cooking mini-grid unattended,\n"
        "proves every plan before acting, and shares what it learns.\n\n"
        f"[dim]{escape(mode)}  ·  world: {escape(env.name)}\n"
        "Built today: agents, Shared Brain, Keeper, Supervisor, tooling. Green-Stove hardware is earlier work.[/]",
        border_style="green", padding=(1, 4)))


def act_header(c: Console, scene: str) -> None:
    n, title, question, _ = ACTS[scene]
    c.print()
    c.rule(f"[bold green]Act {n} · {title}")
    c.print(f"[italic]{question}[/]")


def takeaway(c: Console, scene: str, override: str | None = None) -> None:
    c.print(f"[bold green]✓[/] {override or ACTS[scene][3]}")


def bars(c: Console, rows: list[tuple[str, RunRecord]]) -> None:
    top = max(len(r.calls) for _, r in rows) or 1
    for label, r in rows:
        tokens = sum(x.prompt_tokens + x.completion_tokens for x in r.calls)
        c.print(f"{label:14} {'█' * max(1, round(24 * len(r.calls) / top)):24} "
                f"{len(r.calls)} model call{'s' if len(r.calls) != 1 else ''} · {tokens:,} tokens · "
                f"{len(r.attempts)} attempt{'s' if len(r.attempts) != 1 else ''} · {r.status}")


def show_improvement(c: Console, root: Path) -> dict:
    """Version N+1 history from the playbook store, if `gsa evolve` has run."""
    index = root / "index.json"
    if not index.exists():
        c.print("[dim]Run `gsa evolve --rounds 2` and `gsa playbook before-after` before the demo to show this act.[/]")
        return {}
    import json as _json
    data = _json.loads(index.read_text())
    versions = data.get("versions", data if isinstance(data, list) else [])
    t = Table("version", "author", "status", "decision")
    for v in versions:
        t.add_row(str(v.get("id")), str(v.get("author")), str(v.get("status")), escape(str(v.get("reason") or "")[:70]))
    c.print(t)
    ba = root / "before-after.md"
    if ba.exists():
        rows = [ln for ln in ba.read_text().splitlines() if ln.startswith("|")]
        for ln in rows[:5]:
            c.print(escape(ln))
    return {"versions": len(versions)}


def _forecast(c: Console, llm, a):
    """Build (or reuse today's) forecast record; live You.com only when the key and spend flag are set."""
    import os as _os
    from datetime import date as _date, timedelta
    from greenstove import cli
    from greenstove.forecast import pipeline
    live = llm.s.mode == "crusoe" and bool(_os.environ.get("YDC_API_KEY"))
    day = _date.today() + timedelta(days=1)
    try:
        rec = pipeline.build_forecast(a.place, a.lat, a.lon, day, search_mode="live" if live else "mock", llm=llm)
    except Exception as exc:  # noqa: BLE001 - the demo continues on the scenario's own forecast
        c.print(f"[yellow]forecast unavailable ({exc.__class__.__name__}); using the scenario's forecast[/]")
        return None
    pipeline.save(rec, cli.data_dir() / "forecast")
    return rec


def show_forecast(c: Console, rec) -> None:
    top = max(max(rec.forecast_pv_kw), 0.1)
    src = rec.accepted.source_url if rec.accepted else "no verified source"
    c.print(Panel(
        f"You.com search ({rec.search_mode}) → Nemotron extraction ({rec.extractor_mode}) → quote checked by code\n"
        f"{escape(rec.place)} · {rec.date} · [bold]{rec.weather_class}[/] · cloud {rec.cloud_fraction:.2f} · "
        f"rain {rec.precip_prob_pct if rec.precip_prob_pct is not None else 'n/a'}% · {rec.status}\n"
        f"source: {escape(src)}\n"
        f"solar today |{spark(hourly(rec.forecast_pv_kw), top)}| {rec.pv_kwh} kWh   "
        f"downside |{spark(hourly(rec.downside_pv_kw), top)}| {rec.downside_pv_kwh} kWh",
        title="Forecast that feeds the agents", border_style="blue"))


def guardrail_status() -> str:
    """Run the NemoClaw/OpenShell guardrail check; honest about where it is running."""
    import subprocess
    import sys as _sys
    from greenstove.llm import ROOT
    script = ROOT / "deploy" / "nemoclaw" / "guardrail-check.py"
    if not script.exists():
        return "kit missing"
    try:
        r = subprocess.run([_sys.executable, str(script)], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return "guardrail check timed out"
    last = (r.stdout.strip().splitlines() or ["no output"])[-1]
    return ("sandbox guardrails ACTIVE: " if r.returncode == 0 else "not in a sandbox here (kit ready for the WSL2 laptop): ") + last[:90]


def receipt(c: Console, facts: dict, llm) -> None:
    live = llm.s.mode == "crusoe"
    facts["rows"].append(("Crusoe + Nemotron", "every model call",
                          f"{len(facts['calls'])} calls via Crusoe Managed Inference · {llm.s.public('pricing')['model']}"
                          if live else f"{len(facts['calls'])} calls to the MOCK stand-in (live mode sends them to "
                          f"Crusoe Managed Inference, Nemotron {llm.s.model})"))
    facts["rows"].append(("NemoClaw / OpenShell", "always-on agents in a sandbox", escape(guardrail_status())))
    t = Table("track", "requirement", "what you just saw", title="What the demo proved", show_lines=True)
    for row in facts["rows"]:
        t.add_row(*row)
    c.print()
    c.print(t)
    tokens = sum(x.prompt_tokens + x.completion_tokens for x in facts["calls"])
    cost = [x.cost_usd for x in facts["calls"] if x.cost_usd is not None]
    c.print(f"Model use in this demo: {len(facts['calls'])} calls · {tokens:,} tokens"
            + (f" · ${sum(cost):.4f}" if cost else "") + f" · {'LIVE Crusoe' if llm.s.mode == 'crusoe' else 'MOCK'}")
    c.print("[dim]Scorecard for every track requirement: `gsa tracks` (17 checks). Full pipeline: `gsa e2e`.[/]")


def main(a) -> int:
    from greenstove import cli, supervisor
    from greenstove.brain import keeper
    from greenstove.env_protocol import load_environment
    from greenstove.scenarios import DEFAULT_REQUIREMENTS, DEMO_SCENARIOS, DEV_SCENARIOS, infeasible_context, \
        make_actual, make_context

    out_dir = cli.data_dir() / "demo"
    if a.replay:
        if a.replay == "latest":
            found = sorted(out_dir.glob("demo-*.txt"), key=lambda q: q.stat().st_mtime)
            if not found:
                Console().print("no recorded demo yet; run `demo` first")
                return 1
            a.replay = str(found[-1])
        text = Path(a.replay).read_text()
        Console().print(Panel(f"RECORDED demo transcript: {a.replay}", style="bold magenta"))
        print(text)
        return 0
    c = Console(record=True)
    llm, env = cli.make_llm(a.mode), load_environment(prefer_stub=a.stub)
    cli.console = c
    title_card(c, llm, env)
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
    dod = cli.current_spec(llm, req)
    eff = dod.requirements(req)
    scenarios = {**DEV_SCENARIOS, **DEMO_SCENARIOS}
    pick = lambda name, pool: name if name in scenarios else next(iter(pool))  # noqa: E731
    pctx = cli.context_for(pick(PROPOSAL_SCENARIO, DEV_SCENARIOS))
    hctx = cli.context_for(pick(HANDOFF_SCENARIO, DEMO_SCENARIOS))
    forecast = None
    if not a.no_forecast:                          # You.com + Nemotron supply the day's solar forecast
        forecast = _forecast(c, llm, a)
    scenes = (["goal", "world", "proposal", "memory", "handoff", "tomorrow", "infeasible", "improve"]
              if a.scene == "all" else [a.scene])
    run = None
    override = None
    facts: dict = {"rows": [], "calls": []}
    snap: dict = {"meta": {"stamp": stamp, "mode": llm.s.mode, "model": llm.s.public("pricing")["model"],
                           "world": env.name, "goal": dod.goal},
                  "spec": {**dod.model_dump(mode="json"), "spec_hash": dod.spec_hash()}}

    def traces_for(ctx, r, actual=None):
        """Re-simulate the run's selected plan per phase for charts (same deterministic world)."""
        if not r.selected_tariff:
            return {}
        plan = env.allocate(env.household_requests(ctx), r.selected_tariff, ctx, r.selected_proposal_id or "")
        out = {"plan": plan.model_dump(mode="json")}
        for ph in ("forecast_expected", "forecast_downside"):
            try:
                out[ph] = env.simulate(plan, ctx, ph).model_dump(mode="json")
            except Exception:  # noqa: BLE001 - a world may not model a phase
                pass
        if actual is not None:
            try:
                out["actual"] = env.simulate(plan, ctx, "actual", actual).model_dump(mode="json")
            except Exception:  # noqa: BLE001
                pass
        return out
    for scene in scenes:
        act_header(c, scene)
        if scene == "goal":
            show_spec(c, dod)
            src = [ch.source for ch in dod.checks]
            facts["rows"].append(("Done Means Done", "writes its own definition of done",
                                  f"{len(dod.checks)} checks from one line; {src.count('code_tightened')} tightened and "
                                  f"{src.count('code_added')} added by code (spec {dod.spec_hash()[:8]})"))
        elif scene == "world":
            show_world(c, pctx)
            snap["world"] = {"context": pctx.model_dump(mode="json")}
        elif scene == "tomorrow":
            if forecast is None:
                c.print("[yellow]no forecast available; skipping[/]")
            else:
                from greenstove.forecast.pipeline import context_from_forecast
                show_forecast(c, forecast)
                tctx = context_from_forecast(forecast, hctx)
                tr = supervisor.run_agent_day(tctx, "curated", req, env=env, llm=llm, evidence=evidence, brain=brain,
                                              write_notes=False, dod=dod)
                facts["calls"] += tr.calls
                show_run(c, tctx, tr, env, eff, details=False)
                snap["tomorrow"] = {"forecast": forecast.model_dump(mode="json"), "context": tctx.model_dump(mode="json"),
                                    "run": tr.model_dump(mode="json"), "traces": traces_for(tctx, tr)}
                facts["rows"].append(("You.com + Nemotron", "real-world input the agents plan on",
                                      f"{forecast.place} {forecast.date}: {forecast.weather_class}, PV {forecast.pv_kwh} kWh "
                                      f"(downside {forecast.downside_pv_kwh}), {forecast.status}, search {forecast.search_mode}; "
                                      f"tomorrow: {tr.status} in {len(tr.attempts)} attempt(s)"))
        elif scene in ("proposal", "memory"):
            if run is None:
                run = supervisor.run_agent_day(pctx, "off", req, env=env, llm=llm, evidence=evidence, brain=brain,
                                               actual=make_actual(pctx, seed=pctx.day + 7), dod=dod)
            if scene == "proposal":
                show_run(c, pctx, run, env, eff)
                snap["proposal"] = {"context": pctx.model_dump(mode="json"), "run": run.model_dump(mode="json"),
                                    "traces": traces_for(pctx, run, make_actual(pctx, seed=pctx.day + 7))}
                facts["calls"] += run.calls
                facts["rows"].append(("Done Means Done", "checks its work, catches drift and loops, decides",
                                      f"{len(run.attempts)} attempts judged by the verifier; decisions "
                                      + " → ".join(d.decision for d in run.decisions) + f"; {run.status}"))
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
                snap["memory"] = {"notes": [n.model_dump(mode="json") for n in run.notes],
                                  "fabricated": ({"description": fake.description, **res.model_dump(mode="json")}
                                                 if recs else None),
                                  "entries": [e.model_dump(mode="json") for e in brain.entries()],
                                  "snapshot": brain.snapshot_hash()}
                added = [n for n in run.notes if n.action in ("added", "confirmed", "kept_separate")]
                facts["rows"].append(("Shared Brain", "updated afterward, kept current and conflict-free",
                                      f"{len(added)} evidence-backed observations admitted; fabricated note "
                                      f"{res.action if recs else 'n/a'} ({res.code if recs else '-'})"))
        elif scene == "handoff":
            rows = []
            for mode in ("off", "curated"):
                r = supervisor.run_agent_day(hctx, mode, req, env=env, llm=llm, evidence=evidence, brain=brain,
                                             write_notes=False, dod=dod)
                rows.append((mode, r))
                facts["calls"] += r.calls
                if mode == "curated":
                    show_run(c, hctx, r, env, eff, details=False)
            c.print()
            bars(c, [("No memory", rows[0][1]), ("Shared Brain", rows[1][1])])
            snap["handoff"] = {"context": hctx.model_dump(mode="json"),
                               "off": rows[0][1].model_dump(mode="json"), "curated": rows[1][1].model_dump(mode="json"),
                               "traces": traces_for(hctx, rows[1][1])}
            off, cur = rows[0][1], rows[1][1]
            diff = len(off.calls) - len(cur.calls)
            override = ("Shared Brain: the new agent read the memory, cited it, and needed "
                        f"{diff} fewer model call{'s' if diff != 1 else ''}. One example; the held-out table is the evidence."
                        if diff > 0 else
                        "Shared Brain: on this day the memory did not save calls; the held-out table, not one day, "
                        "decides whether it helps.")
            facts["rows"].append(("Shared Brain", "every agent reads before it works; measurable gain",
                                  f"fresh agent read {len(cur.brief.entry_ids)} entries and cited "
                                  f"{','.join(i for a in cur.attempts if a.proposal for i in a.proposal.cited_observation_ids) or '-'}"
                                  f"; {len(off.calls)} → {len(cur.calls)} model calls on this day (held-out table = evidence)"))
        elif scene == "infeasible":
            ictx = infeasible_context()
            r = supervisor.run_agent_day(ictx, "curated", req, env=env, llm=llm, evidence=evidence, brain=brain, dod=dod)
            p = r.infeasibility or {}
            c.print(f"[bold]{r.status}[/] — needs ≥ {p.get('lower_bound_kwh', 0):.2f} kWh, at most "
                    f"{p.get('available_kwh', 0):.2f} kWh available · model calls {len(r.calls)}")
            for line in p.get("assumptions", []):
                c.print(f"  · {line}")
            c.print("decisions: " + " → ".join(d.decision for d in r.decisions))
            snap["infeasible"] = {"context": ictx.model_dump(mode="json"), "run": r.model_dump(mode="json")}
            facts["rows"].append(("Done Means Done", "stops honestly, pings a human rarely",
                                  f"needs ≥ {p.get('lower_bound_kwh', 0):.2f} kWh vs ≤ {p.get('available_kwh', 0):.2f}; "
                                  f"INFEASIBLE with 0 model calls → ping_human"))
        elif scene == "improve":
            info = show_improvement(c, cli.data_dir() / "playbook")
            pb = cli.data_dir() / "playbook"
            snap["improve"] = {"index": (json.loads((pb / "index.json").read_text()) if (pb / "index.json").exists() else None),
                               "before_after_md": ((pb / "before-after.md").read_text() if (pb / "before-after.md").exists() else None)}
            facts["rows"].append(("Version N+1", "keeps a change only if it measurably helps; unseen tasks",
                                  f"{info.get('versions', 0)} playbook versions with keep/reject decisions; "
                                  "before/after on held-out days" if info else "run `gsa evolve` before the demo"))
        if scene == "tomorrow" and forecast is not None and forecast.search_mode == "mock":
            override = ("MOCK forecast fixture and extractor here; live mode uses You.com search and Nemotron "
                        "extraction, the same quote check, and the same agents.")
        takeaway(c, scene, override if scene in ("handoff", "tomorrow") else None)
        if a.pause and scene != scenes[-1]:
            input("[Enter] next act ")
    if a.scene == "all":
        receipt(c, facts, llm)
        snap["receipt"] = {"rows": [list(r) for r in facts["rows"]], "calls": len(facts["calls"]),
                           "tokens": sum(x.prompt_tokens + x.completion_tokens for x in facts["calls"]),
                           "cost_usd": sum(x.cost_usd or 0 for x in facts["calls"])}
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"demo-{llm.s.mode}-{stamp}.txt"
    (out_dir / f"demo-{llm.s.mode}-{stamp}.json").write_text(json.dumps(snap, indent=1, default=str))
    c.save_text(str(path))
    c.print(f"recorded transcript: {path}")
    return 0
