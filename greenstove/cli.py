"""Command line: python -m greenstove <command>. Every command prints its mode first."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from rich.console import Console

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
    return LLMClient(LLMSettings.from_env(mode), MOCKS)


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
    ctx = make_context(**DEMO_SCENARIOS[name])
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
    ctx = infeasible_context() if a.scenario == "infeasible" else make_context(**named[a.scenario])
    evidence, brain = stores()
    actual = make_actual(ctx, seed=ctx.day + 7) if a.execute else None
    run = supervisor.run_agent_day(ctx, a.brain, DEFAULT_REQUIREMENTS, env=env, llm=llm,
                                   evidence=evidence, brain=brain, actual=actual)
    show_run(console, ctx, run, env, DEFAULT_REQUIREMENTS)
    return 0


def cmd_warmup(a) -> int:
    from greenstove import supervisor
    from greenstove.scenarios import DEFAULT_REQUIREMENTS, DEV_SCENARIOS, make_actual, make_context
    llm, env = make_llm(a.mode), load_environment(prefer_stub=a.stub)
    banner(llm, env.name)
    evidence, brain = stores()
    for name, spec in DEV_SCENARIOS.items():
        ctx = make_context(**spec)
        run = supervisor.run_agent_day(ctx, "curated", DEFAULT_REQUIREMENTS, env=env, llm=llm, evidence=evidence,
                                       brain=brain, actual=make_actual(ctx, seed=ctx.day + 7))
        notes = ", ".join(f"{n.action}:{n.observation_id or '-'}" for n in run.notes)
        console.print(f"{name:32} {run.status:17} attempts={len(run.attempts)} calls={len(run.calls)} notes=[{notes}]")
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
            console.print(json.dumps(ev, default=str))
        return 0
    for e in brain.entries():
        console.print(f"{e.id} v{e.version} [{e.status}] {e.phase} {'PASS' if e.outcome_passed else 'FAIL'} "
                      f"{e.tariff} codes={e.feedback_codes} day {e.created_at}->{e.expires_at} "
                      f"evidence={len(e.evidence_ids)} {e.conflict_group or ''}")
    return 0


def cmd_demo(a) -> int:
    from greenstove import demo
    return demo.main(a)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="greenstove")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke"); s.add_argument("--mode", choices=["mock", "crusoe"]); s.set_defaults(fn=cmd_smoke)
    s = sub.add_parser("plan")
    s.add_argument("--scenario", required=True); s.add_argument("--brain", default="curated",
                                                                choices=["off", "history", "curated"])
    s.add_argument("--execute", action="store_true"); s.add_argument("--mode", choices=["mock", "crusoe"])
    s.add_argument("--stub", action="store_true"); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("warmup"); s.add_argument("--mode", choices=["mock", "crusoe"])
    s.add_argument("--stub", action="store_true"); s.set_defaults(fn=cmd_warmup)
    s = sub.add_parser("brain"); s.add_argument("action", choices=["show", "audit", "freeze"], nargs="?", default="show")
    s.add_argument("--out", default="data/brain-frozen"); s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_brain)
    s = sub.add_parser("demo")
    s.add_argument("--scene", default="all", choices=["all", "world", "proposal", "memory", "handoff", "infeasible"])
    s.add_argument("--mode", choices=["mock", "crusoe"]); s.add_argument("--stub", action="store_true")
    s.add_argument("--replay", help="print a recorded demo transcript (labeled RECORDED)")
    s.add_argument("--pause", action="store_true", help="wait for Enter between scenes")
    s.set_defaults(fn=cmd_demo)
    a = p.parse_args(argv)
    try:
        return a.fn(a)
    except ModelError as exc:
        console.print(f"[red]model error:[/] {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
