"""Partner-side end-to-end: freeze development memory, run conditions A/B/C on held-out days.

A = facts only ("off"), B = raw development history ("history"), C = curated brain
("curated"). Same model/settings, prompts, attempt budget and feedback in all three; memory
is frozen and evaluation notes are disabled. Abel's evaluate.py owns the official
six-condition table (adds D/E/F and the scoring); this module writes the raw A/B/C records
and a partner-side summary that feeds it.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from greenstove import note_writer, pricing_agent, supervisor
from greenstove.agent_models import EvidenceRecord, RunRecord, stable_hash
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import Environment, Requirements
from greenstove.evidence import EvidenceStore
from greenstove.llm import ROOT, LLMClient

MODES = ("off", "history", "curated")
LABEL = {"off": "A facts only", "history": "B raw history", "curated": "C curated brain"}


def git_commit() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def freeze_memory(evidence: EvidenceStore, brain: BrainStore, out: Path) -> dict[str, Any]:
    """Freeze the curated brain and the raw development history (same source evidence)."""
    out.mkdir(parents=True, exist_ok=True)
    brain_hash = brain.freeze(out / "brain")
    history = sorted(evidence.all(), key=lambda r: (r.day, r.run_id))
    (out / "history.jsonl").write_text("".join(r.model_dump_json() + "\n" for r in history))
    info = {"brain_snapshot": brain_hash, "brain_entries": len(brain.entries()),
            "history_records": len(history), "history_hash": stable_hash([r.run_id for r in history]),
            "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "commit": git_commit()}
    (out / "freeze.json").write_text(json.dumps(info, indent=2))
    return info


def load_frozen(freeze_dir: Path) -> tuple[BrainStore, list[EvidenceRecord]]:
    brain = BrainStore.load_frozen(freeze_dir / "brain")
    lines = (freeze_dir / "history.jsonl").read_text().splitlines()
    return brain, [EvidenceRecord.model_validate_json(x) for x in lines if x.strip()]


def stale_served(run: RunRecord, brain: BrainStore, history_ids: set[str], clock: int, config_hash: str) -> int:
    """Entries supplied that were not valid for this config at this day's clock (must be 0)."""
    if run.memory_mode == "curated":
        valid = {e.id for e in brain.current(config_hash, clock)}
        return sum(1 for i in run.brief.entry_ids if i not in valid)
    if run.memory_mode == "history":
        return sum(1 for i in run.brief.entry_ids if i not in history_ids)
    return len(run.brief.entry_ids)


def run_conditions(names: list[str], contexts: Callable, actuals: Callable, requirements: Requirements, *,
                   env: Environment, llm: LLMClient, freeze_dir: Path, out: Path,
                   modes: tuple[str, ...] = MODES, log: Callable[[str], None] = print, dod=None) -> list[RunRecord]:
    brain, history = load_frozen(freeze_dir)
    history_ids = {r.run_id for r in history}
    evidence = EvidenceStore(out / "evidence")           # evaluation evidence never feeds memory
    runs: list[RunRecord] = []
    with (out / "agent-runs.jsonl").open("w") as f:
        for i, name in enumerate(names):
            ctx, actual = contexts(name), actuals(name)
            order = modes[i % len(modes):] + modes[:i % len(modes)]       # rotate to reduce order bias
            for mode in order:
                run = supervisor.run_agent_day(ctx, mode, requirements, env=env, llm=llm, evidence=evidence,
                                               brain=brain, history=history, phase="evaluation",
                                               actual=actual, write_notes=False, dod=dod)
                stale = stale_served(run, brain, history_ids, ctx.day, ctx.config_hash)
                f.write(json.dumps({"scenario": name, "mode": mode, "stale_served": stale,
                                    "run": run.model_dump(mode="json")}) + "\n")
                runs.append(run)
                log(f"{name:30} {LABEL[mode]:16} {run.status:17} attempts={len(run.attempts)} "
                    f"calls={len(run.calls)} exec={'-' if not run.execution else ('PASS' if run.execution.outcome_passed else 'FAIL')}"
                    f" stale={stale}")
    return runs


def _usd(rs: list[RunRecord]) -> str:
    costs = [c.cost_usd for r in rs for c in r.calls if c.cost_usd is not None]
    return f"{sum(costs) / len(rs):.5f}" if costs else "n/a"


def summarize(runs: list[RunRecord], out: Path, header: dict[str, Any]) -> str:
    rows = ["| Condition | Verified | First candidate | Exec pass | Calls/day | Tokens/day | Cached % | USD/day | "
            "Candidate checks/day | Simulator runs/day | Mean served (actual) |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for mode in MODES:
        rs = [r for r in runs if r.memory_mode == mode]
        if not rs:
            continue
        n = len(rs)
        ver = sum(r.status == "PLAN_VERIFIED" for r in rs)
        first = sum(bool(r.attempts) and r.attempts[0].passed for r in rs)
        exe = sum(bool(r.execution and r.execution.outcome_passed) for r in rs)
        served = [r.execution.metrics.get("served_fraction", 0.0) for r in rs if r.execution]
        mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
        rows.append(f"| {LABEL[mode]} | {ver}/{n} | {first}/{n} | {exe}/{n} | "
                    f"{mean([len(r.calls) for r in rs]):.2f} | "
                    f"{mean([sum(c.prompt_tokens + c.completion_tokens for c in r.calls) for r in rs]):.0f} | "
                    f"{100 * sum(c.cached_tokens for r in rs for c in r.calls) / max(1, sum(c.prompt_tokens for r in rs for c in r.calls)):.0f} | "
                    f"{_usd(rs)} | "
                    f"{mean([r.candidate_evaluations for r in rs]):.2f} | {mean([r.simulator_runs for r in rs]):.2f} | "
                    f"{mean(served):.2f} |")
    lines = ["# Partner-side A/B/C summary", "",
             f"Model: **{header['model']}** · world: **{header['world']}** · commit {header['commit']} · "
             f"brain snapshot {header['brain_snapshot'][:8]} · {header['days']} held-out days", "",
             "Raw records: `agent-runs.jsonl`. Failed days stay in every denominator. This is not the "
             "official table: Abel's evaluation adds D/E/F (reuse and search) and the scoring.", ""] + rows
    if "mock" in header["model"].lower():
        lines += ["", "**Mock mode:** a rule-based stand-in for the model. These numbers test the "
                  "harness, not Nemotron and not the value of memory."]
    if "stub" in header["world"]:
        lines += ["", "**Stub world:** development stand-in, not Abel's simulator."]
    text = "\n".join(lines) + "\n"
    (out / "agent-summary.md").write_text(text)
    return text


def manifest_fragment(freeze_info: dict, names: list[str], requirements: Requirements, llm: LLMClient,
                      env: Environment, config_hash: str, max_attempts: int = 3, brief_budget: int = 600,
                      dod=None) -> dict:
    return {"partner": {
        "definition_of_done": ({"spec_hash": dod.spec_hash(), "goal": dod.goal,
                                "checks": [c.model_dump() for c in dod.checks]} if dod else None),
        "commit": git_commit(),
        "llm": {"mode": llm.s.mode, **llm.s.public(),
                "models_by_role": {r: llm.s.model_for(r) for r in ("pricing", "note_writer", "forecast_extractor")},
                "thinking_by_role": dict(llm.s.thinking), "structured": llm.s.structured},
        "prompt_hashes": {"pricing": stable_hash(pricing_agent.SYSTEM), "note_writer": stable_hash(note_writer.SYSTEM)},
        "memory": freeze_info, "modes": list(MODES), "held_out": names, "max_attempts": max_attempts,
        "brief_token_budget": brief_budget, "requirements": requirements.model_dump(), "world": env.name,
        "config_hash": config_hash, "evaluation_notes": "disabled"}}
