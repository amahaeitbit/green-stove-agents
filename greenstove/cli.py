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
MOCKS = {"pricing": pricing_agent.mock_pricing, "note_writer": note_writer.mock_note_writer}


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
    named = {**DEV_SCENARIOS, **DEMO_SCENARIOS}
    if a.scenario != "infeasible" and a.scenario not in named:
        console.print(f"[red]unknown scenario[/] {a.scenario}; choose from {sorted(named) + ['infeasible']}")
        return 1
    ctx = context_for(a.scenario)
    evidence, brain = stores()
    actual = make_actual(ctx, seed=ctx.day + 7) if a.execute else None
    run = supervisor.run_agent_day(ctx, a.brain, DEFAULT_REQUIREMENTS, env=env, llm=llm,
                                   evidence=evidence, brain=brain, actual=actual)
    show_run(console, ctx, run, env, DEFAULT_REQUIREMENTS)
    from greenstove.decision import brief_lines, render_decision
    text = render_decision(ctx, run, env, DEFAULT_REQUIREMENTS, brief_lines(run.brief.text))
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
    """smoke -> warmup -> freeze -> held-out A/B/C -> recorded demo, in one isolated data folder."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    root = data_dir() / f"e2e-{stamp}"
    os.environ["GREENSTOVE_DATA_DIR"] = str(root.relative_to(ROOT))
    mode = ["--mode", a.mode] if a.mode else []
    steps = [("smoke", ["smoke"] + mode), ("warmup", ["warmup"] + mode),
             ("freeze", ["freeze", "--out", str(root / "freeze")]),
             ("eval-agents", ["eval-agents", "--freeze", str(root / "freeze"), "--out", str(root / "eval"),
                              "--days", str(a.days)] + mode)]
    if not a.skip_demo:
        steps.append(("demo", ["demo"] + mode))
    for name, argv in steps:
        console.rule(f"[bold]e2e · {name}")
        code = main(argv)
        if code != 0:
            console.print(f"[red]e2e stopped at {name} (exit {code})[/]; records so far in {root.relative_to(ROOT)}")
            return code
    console.rule("[bold green]e2e complete")
    console.print(f"everything is under {root.relative_to(ROOT)}: smoke/, evidence/, brain/, freeze/, eval/, demo/")
    return 0


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
    s.add_argument("--stub", action="store_true"); s.set_defaults(fn=cmd_plan)
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
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("decide", help="run one day and print the operator decision record")
    s.add_argument("--condition", choices=list(CONDITIONS), default="C")
    s.add_argument("--scenario", default="demo-handoff-overcast")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("preflight"); s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.set_defaults(fn=cmd_preflight)
    s = sub.add_parser("freeze"); s.add_argument("--out"); s.set_defaults(fn=cmd_freeze)
    s = sub.add_parser("eval-agents", help="conditions A/B/C on held-out days with frozen memory")
    s.add_argument("--freeze", required=True); s.add_argument("--out"); s.add_argument("--days", type=int, default=8)
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.set_defaults(fn=cmd_eval_agents)
    s = sub.add_parser("e2e", help="smoke, warmup, freeze, held-out A/B/C, recorded demo")
    s.add_argument("--mode", choices=["mock", "live", "crusoe"]); s.add_argument("--days", type=int, default=8)
    s.add_argument("--skip-demo", action="store_true"); s.set_defaults(fn=cmd_e2e)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except ModelError as exc:
        console.print(f"[red]model error:[/] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
