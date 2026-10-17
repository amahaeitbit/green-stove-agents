"""Command line: python -m greenstove <command>. Every command prints its mode first."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from greenstove import note_writer, pricing_agent
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import load_environment
from greenstove.evidence import EvidenceStore
from greenstove.llm import LLMClient, LLMSettings, ModelError, ROOT

console = Console()
def _mock_forecast(payload):
    from greenstove.forecast.pipeline import mock_extractor
    return mock_extractor(payload)


MOCKS = {"pricing": pricing_agent.mock_pricing, "note_writer": note_writer.mock_note_writer,
         "forecast_extractor": _mock_forecast}


def data_dir() -> Path:
    return ROOT / os.environ.get("GREENSTOVE_DATA_DIR", "data")


def make_llm(mode: str | None) -> LLMClient:
    return LLMClient(LLMSettings.from_env(mode), MOCKS, usage_path=data_dir() / "usage.jsonl")


CONDITIONS = {"A": "off", "B": "history", "C": "curated"}


def cmd_decide(a) -> int:
    """The day's full decision record for operators (runs one agent day, then prints decision.md)."""
    a.brain = CONDITIONS[a.condition]
    a.execute = True
    a.show_decision = True
    return cmd_plan(a)


def cmd_run(a) -> int:
    """gsa run --condition A|B|C --mode mock|live: one fresh agent day (development phase)."""
    a.brain = CONDITIONS[a.condition]
    a.execute = True
    return cmd_plan(a)


def banner(llm: LLMClient, env_name: str) -> None:
    mode = ("[bold yellow]MOCK (rule-based stand-in, not model quality)[/]" if llm.s.mode == "mock"
            else f"[bold green]LIVE Crusoe · {llm.s.model}[/]")
    env = f"[yellow]{env_name}[/]" if "stub" in env_name else f"[green]{env_name}[/]"
    console.print(f"model: {mode}   world: {env}")


def cmd_smoke(a) -> int:
    llm = make_llm(a.mode)
    env = load_environment()
    banner(llm, env.name)
    if llm.s.mode == "crusoe":
        models = llm.list_models()
        nemo = [m for m in models if "nemotron" in m.get("id", "").lower()]
        console.print(f"/v1/models: {len(models)} models; Nemotron: {[m['id'] for m in nemo]}")
        chosen = next((m for m in models if m.get("id") == llm.s.model), None)
        if chosen is None:
            console.print(f"[red]GREENSTOVE_MODEL={llm.s.model} is not in the catalog; copy an exact id.[/]")
            return 1
        console.print(f"supported_parameters: {chosen.get('supported_parameters', 'n/a')}")
    from greenstove.scenarios import DEMO_SCENARIOS, make_context
    from greenstove.agent_models import Brief
    name = next(iter(DEMO_SCENARIOS))
    ctx = context_for(name)
    t0 = time.monotonic()
    prop, errors, calls = pricing_agent.propose(ctx, Brief(role="pricing", mode="off"), [], llm, 1, "smoke-p1")
    console.print(f"proposal: {prop.prices if prop else None}  errors: {errors}  "
                  f"({time.monotonic() - t0:.1f}s)")
    for c in llm.calls:
        console.print(f"call: outcome={c.outcome} tokens={c.prompt_tokens}+{c.completion_tokens} "
                      f"latency={c.latency_ms}ms request_id={c.request_id}")
    out = data_dir() / "smoke"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"smoke-{llm.s.mode}-{int(time.time())}.json"
    path.write_text(json.dumps({"proposal": prop.model_dump() if prop else None, "errors": errors,
                                "calls": [c.model_dump() for c in llm.calls]}, indent=2))
    console.print(f"recorded: {path.relative_to(ROOT)}")
    return 0 if prop else 1


def context_for(name: str):
    """Named development/demo scenario; its scenario_id equals the name."""
    from greenstove.scenarios import infeasible_context, scenario
    if name == "c0-fixture":                      # Abel's C0 fixture (two households, 2 kW inverter)
        from greenstove.io import load_context
        return load_context(ROOT / "fixtures" / "c0" / "context.json")
    return infeasible_context() if name == "infeasible" else scenario(name)


def stores() -> tuple[EvidenceStore, BrainStore]:
    d = data_dir()
    return EvidenceStore(d / "evidence"), BrainStore(d / "brain")


def cmd_plan(a) -> int:
    from greenstove import supervisor
    from greenstove.demo import show_run
    from greenstove.scenarios import (DEFAULT_REQUIREMENTS, DEMO_SCENARIOS, DEV_SCENARIOS, infeasible_context,
                                      make_actual, make_context)
    llm, env = make_llm(a.mode), load_environment(prefer_stub=a.stub)
    banner(llm, env.name)
    from greenstove.scenarios import HELDOUT_SCENARIOS
    named = {**DEV_SCENARIOS, **DEMO_SCENARIOS, **HELDOUT_SCENARIOS}
    if a.scenario not in ("infeasible", "c0-fixture") and a.scenario not in named:
        console.print(f"[red]unknown scenario[/] {a.scenario}; choose from {sorted(named) + ['infeasible', 'c0-fixture']}")
        return 1
    ctx = context_for(a.scenario)
    if getattr(a, "forecast", None):
        from greenstove.forecast.pipeline import context_from_forecast, load
        rec = load(Path(a.forecast))
        ctx = context_from_forecast(rec, ctx)
        console.print(f"forecast: {rec.place} {rec.date} · {rec.weather_class} · PV {rec.pv_kwh} kWh "
                      f"(downside {rec.downside_pv_kwh}) · search {rec.search_mode} · {rec.status}", markup=False)
    evidence, brain = stores()
    actual = make_actual(ctx, seed=ctx.day + 7) if a.execute else None
    req = DEFAULT_REQUIREMENTS
    if a.scenario == "c0-fixture":                # Abel's fixture comes with its own requirements
        from greenstove.io import load_requirements
        req = load_requirements(ROOT / "fixtures" / "c0" / "requirements.json")
    run = supervisor.run_agent_day(ctx, a.brain, req, env=env, llm=llm,
                                   evidence=evidence, brain=brain, actual=actual)
    show_run(console, ctx, run, env, req)
    from greenstove.decision import brief_lines, render_decision
    text = render_decision(ctx, run, env, req, brief_lines(run.brief.text))
    path = evidence.run_dir(run.agent_run_id) / "decision.md"
    path.write_text(text)
    if getattr(a, "show_decision", False):
        from rich.markdown import Markdown
        console.print(Markdown(text))
    console.print(f"decision record: {path.relative_to(ROOT)}")
    return 0


def cmd_warmup(a) -> int:
    from greenstove import supervisor
    from greenstove.scenarios import DEFAULT_REQUIREMENTS, DEV_SCENARIOS, make_actual, make_context
    llm, env = make_llm(a.mode), load_environment(prefer_stub=a.stub)
    banner(llm, env.name)
    evidence, brain = stores()
    for name in DEV_SCENARIOS:
        ctx = context_for(name)
        run = supervisor.run_agent_day(ctx, "curated", DEFAULT_REQUIREMENTS, env=env, llm=llm, evidence=evidence,
                                       brain=brain, actual=make_actual(ctx, seed=ctx.day + 7))
        notes = ", ".join(f"{n.action}:{n.observation_id or '-'}" for n in run.notes)
        console.print(f"{name:32} {run.status:17} attempts={len(run.attempts)} calls={len(run.calls)} notes=[{notes}]",
                      markup=False)
    console.print(f"brain: {len(brain.entries())} entries, snapshot {brain.snapshot_hash()[:8]}")
    return 0


def cmd_brain(a) -> int:
    _, brain = stores()
    if a.action == "freeze":
        dest = Path(a.out)
        brain.freeze(dest)
        console.print(f"frozen snapshot {brain.snapshot_hash()[:8]} -> {dest}")
        return 0
    if a.action == "audit":
        for ev in brain.audit_log()[-a.limit:]:
            console.print(json.dumps(ev, default=str), markup=False)
        return 0
    for e in brain.entries():
        console.print(f"{e.id} v{e.version} [{e.status}] {e.phase} {'PASS' if e.outcome_passed else 'FAIL'} "
                      f"{e.tariff} codes={e.feedback_codes} day {e.created_at}->{e.expires_at} "
                      f"evidence={len(e.evidence_ids)} {e.conflict_group or ''}", markup=False)
    return 0


def cmd_forecast(a) -> int:
    """You.com search -> Nemotron extraction -> verified quote -> PV profile for the station."""
    from datetime import date as _date, timedelta
    from greenstove.demo import hourly, spark
    from greenstove.forecast import pipeline
    search_mode = "live" if a.mode in ("live", "crusoe") else "mock"
    llm = make_llm(a.mode)
    console.print(f"forecast: You.com search [bold]{search_mode}[/] · extraction model "
                  f"{'MOCK (rule-based)' if llm.s.mode == 'mock' else 'LIVE ' + llm.s.model}")
    day = _date.fromisoformat(a.date) if a.date else _date.today() + timedelta(days=1)
    rec = pipeline.build_forecast(a.place, a.lat, a.lon, day, tz_hours=a.tz, kwp=a.kwp, search_mode=search_mode, llm=llm)
    for line in pipeline.summary_lines(rec):
        console.print(line, markup=False)
    top = max(max(rec.forecast_pv_kw), 0.1)
    console.print(f"PV forecast |{spark(hourly(rec.forecast_pv_kw), top)}|  downside |"
                  f"{spark(hourly(rec.downside_pv_kw), top)}|  (00-24h)", markup=False)
    path = pipeline.save(rec, data_dir() / "forecast")
    console.print(f"saved: {path.relative_to(ROOT)}  ->  gsa decide --forecast {path.relative_to(ROOT)}")
    return 0 if rec.status == "VERIFIED" else 3


def _table(rows: list[dict], title: str | None = None) -> None:
    if not rows:
        console.print("(no rows)")
        return
    t = Table(*rows[0].keys(), title=title)
    for r in rows:
        t.add_row(*(escape("" if v is None else str(v)) for v in r.values()))
    console.print(t)


def cmd_models(a) -> int:
    """Crusoe /v1/models: exact ids, Nemotron first, supported parameters and pricing (listing is free)."""
    from greenstove import crusoe
    llm = make_llm("crusoe")
    models = llm.list_models()
    path = crusoe.save_catalog(models, data_dir() / "crusoe")
    _table(crusoe.catalog_rows(models), title=f"Crusoe catalog ({len(models)} models)")
    console.print(f"pinned: GREENSTOVE_MODEL={llm.s.model} (pricing role: {llm.s.model_for('pricing')}) · saved {path.relative_to(ROOT)}")
    return 0 if any(m.get("id") == llm.s.model_for("pricing") for m in models) else 1


def cmd_bench(a) -> int:
    """Same pricing task on each model: valid outputs, latency, tokens, cost. Pick by evidence."""
    from greenstove import crusoe
    llm = make_llm(a.mode)
    ids = [m.strip() for m in a.models.split(",")] if a.models else None
    if ids is None:
        cat = llm.ensure_catalog() if llm.s.mode == "crusoe" else {}
        ids = [i for i in cat if "nemotron" in i.lower() and "omni" not in i.lower() and "voice" not in i.lower()] or [llm.s.model]
    rows = crusoe.bench(llm, ids, context_for(a.scenario), n=a.n)
    _table(rows, title=f"Pricing Agent benchmark · {a.scenario} · {a.n} runs per model · {llm.s.mode}")
    out = data_dir() / "crusoe" / f"bench-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1))
    console.print(f"saved {out.relative_to(ROOT)} · pin the winner with GREENSTOVE_MODEL (same model for A/B/C)")
    return 0


def cmd_usage(a) -> int:
    from greenstove import crusoe
    path = Path(a.file) if a.file else data_dir() / "usage.jsonl"
    if not path.exists():
        console.print(f"no usage log at {path}")
        return 1
    _table(crusoe.usage_rows(path), title=f"Model usage · {path}")
    console.print("cross-check live runs: https://console.crusoecloud.com/foundry/metrics")
    return 0


def cmd_preflight(a) -> int:
    """Readiness for an end-to-end or live demo run. Prints no secrets."""
    import subprocess
    from greenstove.agent_eval import git_commit
    s = LLMSettings.from_env(a.mode)
    rows: list[tuple[str, str, str]] = []
    add = lambda name, status, note: rows.append((name, status, note))  # noqa: E731
    env_file = ROOT / ".env"
    add(".env file", "PASS" if env_file.exists() else "WARN", "present" if env_file.exists()
        else "missing: cp .env.example .env (mock mode works without it)")
    add("model mode", "PASS", f"{s.mode} ({s.model if s.mode == 'crusoe' else 'mock-rules'})")
    if s.mode == "crusoe":
        add("Crusoe key", "PASS" if len(s.api_key) >= 20 else "FAIL",
            f"{len(s.api_key)} characters" if s.api_key else "missing: python scripts/set_crusoe_key.py")
        add("live spend flag", "PASS" if s.live_spend_ok else "FAIL",
            "GREENSTOVE_LIVE_SPEND_OK=1" if s.live_spend_ok else "set GREENSTOVE_LIVE_SPEND_OK=1 in .env")
        if s.api_key and s.live_spend_ok:
            try:
                ids = [m.get("id") for m in LLMClient(s, MOCKS).list_models()]   # listing is not billed
                add("model in catalog", "PASS" if s.model in ids else "FAIL",
                    s.model if s.model in ids else f"not found; Nemotron ids: {[i for i in ids if 'emotron' in (i or '')]}")
            except Exception as exc:  # noqa: BLE001 - report any transport/auth failure
                add("model in catalog", "FAIL", f"{exc.__class__.__name__}: {str(exc)[:80]}")
    ydc = os.environ.get("YDC_API_KEY", "").strip()
    add("You.com key (forecast)", "PASS" if len(ydc) >= 20 else "WARN",
        f"{len(ydc)} characters" if ydc else "missing: scripts/set_crusoe_key.py --var YDC_API_KEY (mock forecast works)")
    env = load_environment()
    add("world", "WARN" if "stub" in env.name else "PASS", env.name)
    evidence, brain = stores()
    add("development brain", "PASS" if brain.entries() else "WARN",
        f"{len(brain.entries())} entries" if brain.entries() else "empty: run warmup (demo uses a fresh workspace anyway)")
    tests = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, capture_output=True, text=True)
    last = (tests.stdout.strip().splitlines() or ["no output"])[-1]
    add("tests", "PASS" if tests.returncode == 0 else "FAIL", last)
    git = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"], capture_output=True, text=True).stdout
    add("git", "PASS" if not git.strip() else "WARN", f"commit {git_commit()}" + ("" if not git.strip() else ", uncommitted changes"))
    t = Table("check", "status", "detail")
    for name, status, note in rows:
        color = {"PASS": "green", "WARN": "yellow", "FAIL": "red"}[status]
        t.add_row(name, f"[{color}]{status}[/]", escape(note))
    console.print(t)
    return 1 if any(st == "FAIL" for _, st, _ in rows) else 0


def cmd_freeze(a) -> int:
    from greenstove.agent_eval import freeze_memory
    evidence, brain = stores()
    out = Path(a.out) if a.out else data_dir() / f"freeze-{time.strftime('%Y%m%d-%H%M%S')}"
    info = freeze_memory(evidence, brain, out)
    console.print(f"frozen: brain {info['brain_snapshot'][:8]} ({info['brain_entries']} entries), "
                  f"history {info['history_records']} records -> {out}")
    return 0


def cmd_eval_agents(a) -> int:
    from greenstove import agent_eval, scenarios
    llm, env = make_llm(a.mode), load_environment(prefer_stub=a.stub)
    banner(llm, env.name)
    names = list(scenarios.HELDOUT_SCENARIOS)[: a.days]
    out = Path(a.out) if a.out else data_dir() / f"eval-agents-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    freeze_dir = Path(a.freeze)
    info = json.loads((freeze_dir / "freeze.json").read_text())
    runs = agent_eval.run_conditions(names, scenarios.scenario, scenarios.actual_for, scenarios.DEFAULT_REQUIREMENTS,
                                     env=env, llm=llm, freeze_dir=freeze_dir, out=out,
                                     log=lambda s: console.print(s, markup=False))
    header = {"model": llm.s.public()["model"] if llm.s.mode == "crusoe" else "mock-rules (MOCK)",
              "world": env.name, "commit": agent_eval.git_commit(), "brain_snapshot": info["brain_snapshot"],
              "days": len(names)}
    console.print(agent_eval.summarize(runs, out, header), markup=False)
    frag = agent_eval.manifest_fragment(info, names, scenarios.DEFAULT_REQUIREMENTS, llm, env, scenarios.config_hash())
    (out / "manifest-partner.json").write_text(json.dumps(frag, indent=2))
    console.print(f"records: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


def cmd_e2e(a) -> int:
    """Complete end to end in one isolated folder: every event resource, required steps stop the run."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = data_dir() / f"e2e-{stamp}"
    os.environ["GREENSTOVE_DATA_DIR"] = str(root.relative_to(ROOT))
    live = a.mode in ("live", "crusoe")
    mode = ["--mode", a.mode] if a.mode else []
    day = a.date or time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
    fc = root / "forecast" / f"asmara-eritrea-{day}-{'live' if live else 'mock'}.json"
    has_runner = "runner" in _subcommands()
    steps = [  # (name, argv, required, what it shows)
        ("preflight", ["preflight"] + mode, False, "readiness: keys, spend flag, model id, world, tests"),
        ("smoke", ["smoke"] + mode, True, "one structured Crusoe call recorded"),
        ("models", ["models"], False, "Crusoe catalog: exact Nemotron ids and pricing") if live else None,
        ("bench", ["bench", "--n", "2"] + mode, False, "Nemotron model chosen by measurement"),
        ("forecast", ["forecast", "--date", day] + mode, False, "You.com + Nemotron forecast -> solar profile"),
        ("decide-forecast", ["decide", "--forecast", str(fc), "--condition", "A"] + mode, False,
         "Done Means Done: one day planned on the forecast, decision.md"),
        ("warmup", ["warmup"] + mode, True, "Shared Brain written from development days"),
        ("freeze", ["freeze", "--out", str(root / "freeze")], True, "brain snapshot + raw history hashed"),
        ("eval-agents", ["eval-agents", "--freeze", str(root / "freeze"), "--out", str(root / "eval"),
                         "--days", str(a.days)] + mode, True, "Shared Brain: A/B/C on held-out days"),
        ("evolve", ["evolve", "--rounds", "2"] + mode, False, "Version N+1: playbook kept only if it helps"),
        ("before-after", ["playbook", "before-after"] + mode, False, "Version N+1: held-out before/after"),
        ("runner-event", ["event", "day_start", "--date", day, "--scenario", "demo-handoff-overcast"], False,
         "always-on: enqueue a day_start event") if has_runner else None,
        ("runner", ["runner", "--once"] + mode, False, "always-on: event processed, zero idle calls") if has_runner else None,
        ("demo", ["demo"] + mode, True, "recorded demo transcript"),
        ("usage", ["usage"], False, "calls, tokens, cache hits, latency, cost"),
    ]
    results, warned, failed = [], [], []
    for step in filter(None, steps):
        name, argv, required, what = step
        console.rule(f"[bold]e2e · {name}")
        try:
            code = main(argv)
        except SystemExit as exc:                 # argparse errors in optional steps
            code = exc.code if isinstance(exc.code, int) else 2
        status = "ok" if code == 0 else ("FAILED" if required else f"skipped/warn (exit {code})")
        results.append((name, status, what))
        if not required and code != 0:
            warned.append(name)
        if code != 0 and required:
            failed.append(name)
            break
    lines = [f"# End-to-end run {stamp}", "", f"Model: {'LIVE Crusoe' if live else 'MOCK (rule-based stand-in)'} · "
             f"world: {load_environment().name} · forecast day {day}", "", "| Step | Status | Shows |", "| --- | --- | --- |"]
    lines += [f"| {n} | {s} | {w} |" for n, s, w in results]
    lines += ["", "Outputs: smoke/, crusoe/, forecast/, evidence/ (runs, decision.md), brain/, freeze/, eval/, playbook/, "
              "events/, demo/, usage.jsonl"]
    (root / "e2e-summary.md").write_text("\n".join(lines) + "\n")
    console.rule("[bold]e2e summary")
    for n, s, w in results:
        console.print(f"{n:16} {s:24} {w}", markup=False)
    console.print(f"summary: {(root / 'e2e-summary.md').relative_to(ROOT)}")
    return 1 if failed else 0


def _subcommands() -> set[str]:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    try:
        from greenstove import runner
        runner.register(sub)
    except ImportError:
        return set()
    return set(sub.choices)

def cmd_demo(a) -> int:
    from greenstove import demo
    return demo.main(a)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="greenstove")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke"); s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.set_defaults(fn=cmd_smoke)
    s = sub.add_parser("plan")
    s.add_argument("--scenario", required=True); s.add_argument("--brain", default="curated",
                                                                choices=["off", "history", "curated"])
    s.add_argument("--execute", action="store_true"); s.add_argument("--mode", choices=["mock", "live", "crusoe"])
    s.add_argument("--stub", action="store_true"); s.add_argument("--forecast", help="saved forecast record (gsa forecast)")
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("warmup"); s.add_argument("--mode", choices=["mock", "live", "crusoe"])
    s.add_argument("--stub", action="store_true"); s.set_defaults(fn=cmd_warmup)
    s = sub.add_parser("brain"); s.add_argument("action", choices=["show", "audit", "freeze"], nargs="?", default="show")
    s.add_argument("--out", default="data/brain-frozen"); s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_brain)
    s = sub.add_parser("demo")
    s.add_argument("--scene", default="all", choices=["all", "world", "proposal", "memory", "handoff", "infeasible"])
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.add_argument("--replay", help="print a recorded demo transcript (labeled RECORDED)")
    s.add_argument("--pause", action="store_true", help="wait for Enter between scenes")
    s.add_argument("--dev-brain", action="store_true", help="use the warm-up brain instead of a fresh demo workspace")
    s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("run", help="one agent day: --condition A (facts) | B (raw history) | C (curated brain)")
    s.add_argument("--condition", choices=list(CONDITIONS), default="C")
    s.add_argument("--scenario", default="demo-handoff-overcast")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.add_argument("--forecast", help="saved forecast record (gsa forecast)"); s.set_defaults(fn=cmd_run)
    s = sub.add_parser("decide", help="run one day and print the operator decision record")
    s.add_argument("--condition", choices=list(CONDITIONS), default="C")
    s.add_argument("--scenario", default="demo-handoff-overcast")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.add_argument("--forecast", help="saved forecast record (gsa forecast)"); s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("forecast", help="weather forecast (You.com + Nemotron) -> solar generation profile")
    s.add_argument("--place", default="Asmara, Eritrea"); s.add_argument("--lat", type=float, default=15.3229)
    s.add_argument("--lon", type=float, default=38.9251); s.add_argument("--tz", type=float, default=3.0)
    s.add_argument("--date", help="YYYY-MM-DD (default tomorrow)"); s.add_argument("--kwp", type=float, default=3.0)
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.set_defaults(fn=cmd_forecast)
    s = sub.add_parser("models", help="Crusoe catalog: ids, supported parameters, pricing")
    s.set_defaults(fn=cmd_models)
    s = sub.add_parser("bench", help="benchmark models on the same Pricing Agent task")
    s.add_argument("--models", help="comma-separated ids (default: Nemotron text models in the catalog)")
    s.add_argument("--n", type=int, default=3); s.add_argument("--scenario", default="dev-overcast-low-battery")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.set_defaults(fn=cmd_bench)
    s = sub.add_parser("usage", help="calls, tokens, cache hits, latency and cost by role and model")
    s.add_argument("--file"); s.set_defaults(fn=cmd_usage)
    s = sub.add_parser("preflight"); s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.set_defaults(fn=cmd_preflight)
    s = sub.add_parser("freeze"); s.add_argument("--out"); s.set_defaults(fn=cmd_freeze)
    s = sub.add_parser("eval-agents", help="conditions A/B/C on held-out days with frozen memory")
    s.add_argument("--freeze", required=True); s.add_argument("--out"); s.add_argument("--days", type=int, default=8)
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.set_defaults(fn=cmd_eval_agents)
    s = sub.add_parser("e2e", help="smoke, warmup, freeze, held-out A/B/C, recorded demo")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--days", type=int, default=8)
    s.add_argument("--skip-demo", action="store_true"); s.add_argument("--date", help="forecast day YYYY-MM-DD")
    s.set_defaults(fn=cmd_e2e)
    from greenstove import evolve
    evolve.register(sub)                          # playbook show|history|before-after, evolve (Version N+1)
    try:
        from greenstove import runner
        runner.register(sub)                      # runner --once|--watch|status, event ... (always-on)
    except ImportError:
        pass
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except ModelError as exc:
        console.print(f"[red]model error:[/] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
