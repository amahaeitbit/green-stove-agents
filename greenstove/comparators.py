"""Non-LLM comparators D/E/F for the six-condition evaluation (partner's labeled implementation).

PARTNER IMPLEMENTATION FOR REVIEW: Abel owns baselines/evaluate per PLAN.md. These are the
reference D/E/F rows he can review, replace or adopt; nothing here changes A/B/C.

Every comparator uses the SAME environment calls as the Supervisor (allocate -> simulate ->
verify on forecast_expected and forecast_downside), the SAME frozen development evidence that
condition B reads (``history.jsonl`` in the freeze dir), the SAME requirements (the frozen
definition of done's ``requirements(...)`` plus its ``evaluate(...)`` checks) and the SAME pass
rule (``supervisor.complete_pass`` on every forecast phase and no definition-of-done failure).
The selected plan is then executed once on the day's actual weather, exactly as for A/B/C.

D      last verified tariff: newest same-config development tariff that passed both forecasts.
E      nearest-context reuse: verified same-config development tariff whose situation is
       nearest to today's ``situation_of(context)`` under a fixed normalized distance.
F3     deterministic search over ``lattice_tariffs(context)`` in a frozen order, budget 3.
Ffull  the same order over the whole lattice; stop at the first pass.
All four: 0 model calls, 0 tokens. No fallback: a comparator that has no candidate, or whose
candidate fails, records that failure.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict

from greenstove import supervisor
from greenstove.agent_models import EvidenceRecord, ExecutionRecord, RunRecord, situation_of, stable_hash
from greenstove.env_protocol import ActualScenario, ContractError, Environment, Requirements, ScenarioContext
from greenstove.scenarios import lattice_tariffs

FORECAST_PHASES = supervisor.FORECAST_PHASES
COMPARATORS = ("D", "E", "F3", "Ffull")
ABC = {"off": "A", "history": "B", "curated": "C"}
CONDITIONS = ("A", "B", "C") + COMPARATORS
LABEL = {"A": "A facts only (LLM)", "B": "B raw history (LLM)", "C": "C curated brain (LLM)",
         "D": "D last verified tariff", "E": "E nearest-context reuse",
         "F3": "F3 lattice search, budget 3", "Ffull": "Ffull lattice search, full"}
F3_BUDGET = 3

# E's distance: fixed before evaluation (scales are the stub's development spread, not tuned on held-out days)
DISTANCE = {"weather_mismatch": 1.0, "battery_frac_scale": 0.3, "demand_rel_scale": 0.3, "pv_rel_scale": 0.3,
            "flex_hours_scale": 3.0}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CandidateCheck(_Strict):
    """One candidate evaluation = allocate one tariff + verify both forecast phases."""
    n: int
    proposal_id: str
    tariff: dict[str, float]
    passed: bool
    feedback: list[str] = []
    phases: dict[str, bool] = {}
    verdict_ids: list[str] = []
    unmodeled: list[str] = []


class ComparatorRun(_Strict):
    condition: Literal["D", "E", "F3", "Ffull"]
    label: str
    scenario_id: str
    env_name: str
    phase: Literal["evaluation"] = "evaluation"
    status: Literal["PLAN_VERIFIED", "NO_VERIFIED_PLAN", "INFEASIBLE"]
    reason: str
    candidates: list[CandidateCheck] = []
    candidate_evaluations: int = 0
    simulator_runs: int = 0                           # forecast verifications + the actual-day replay
    model_calls: int = 0
    tokens: int = 0
    selected_tariff: dict[str, float] | None = None
    execution: ExecutionRecord | None = None
    source: dict[str, Any] = {}                       # library entry / distance / search order used
    definition_of_done: str | None = None             # spec hash


# ---- development library (same frozen evidence B reads) -------------------------------------

@dataclass(frozen=True)
class LibraryEntry:
    run_id: str                                       # agent run that proposed it (tie-break key)
    proposal_id: str
    scenario_id: str
    config_hash: str
    day: int
    situation: dict = field(hash=False, compare=False)
    tariff: dict = field(hash=False, compare=False)
    evidence_ids: tuple[str, ...] = ()


def _tkey(t: dict[str, float]) -> tuple:
    return tuple(sorted((k, float(v)) for k, v in t.items()))


def verified_library(history: list[EvidenceRecord], dod=None) -> list[LibraryEntry]:
    """Development tariffs whose records passed BOTH forecast phases (and the definition of done,
    when given, on the recorded metrics). Grouped by (config, scenario, day, tariff) so dedup'd
    verdict records still count; sorted by (day, run_id)."""
    groups: dict[tuple, list[EvidenceRecord]] = {}
    for r in history:
        if r.phase in FORECAST_PHASES:
            groups.setdefault((r.config_hash, r.scenario_id, r.day, _tkey(r.tariff)), []).append(r)
    out = []
    for recs in groups.values():
        ok = all(any(r.phase == ph for r in recs) for ph in FORECAST_PHASES)
        ok = ok and all(r.passed and not (dod.evaluate(r.metrics) if dod is not None else []) for r in recs)
        if ok:
            first = min(recs, key=lambda r: (r.agent_run_id, r.run_id))
            out.append(LibraryEntry(run_id=first.agent_run_id, proposal_id=first.proposal_id,
                                    scenario_id=first.scenario_id, config_hash=first.config_hash, day=first.day,
                                    situation=dict(first.situation), tariff=dict(first.tariff),
                                    evidence_ids=tuple(sorted(r.run_id for r in recs))))
    return sorted(out, key=lambda e: (e.day, e.run_id))


def incompatibility(entry: LibraryEntry, context: ScenarioContext) -> str | None:
    """Why a library tariff cannot be reused today (None if compatible)."""
    if entry.config_hash != context.config_hash:
        return "config_hash"
    if entry.day >= context.day:
        return "not_before_today"
    names = {w.name for w in context.windows}
    if set(entry.tariff) != names:
        return "windows"
    if any(float(entry.tariff[k]) not in [float(x) for x in context.lattice.get(k, [])] for k in names):
        return "off_lattice"
    order = context.tier_order
    if not all(entry.tariff[a] <= entry.tariff[b] for a, b in zip(order, order[1:])):
        return "tier_order"
    return None


def distance(today: dict, past: dict) -> float:
    """Fixed normalized situation distance (DISTANCE); today's demand/PV set the relative scales."""
    d, pv = float(today["demand_kwh"]), float(today["forecast_pv_kwh"])
    return (DISTANCE["weather_mismatch"] * (today["weather_class"] != past["weather_class"])
            + abs(float(today["battery_frac"]) - float(past["battery_frac"])) / DISTANCE["battery_frac_scale"]
            + abs(d - float(past["demand_kwh"])) / max(1.0, d) / DISTANCE["demand_rel_scale"]
            + abs(pv - float(past["forecast_pv_kwh"])) / max(1.0, pv) / DISTANCE["pv_rel_scale"]
            + abs(float(today["flex_hours"]) - float(past["flex_hours"])) / DISTANCE["flex_hours_scale"])


def choose_last(library: list[LibraryEntry], context: ScenarioContext) -> tuple[LibraryEntry | None, int]:
    ok = [e for e in library if incompatibility(e, context) is None]
    return (max(ok, key=lambda e: (e.day, e.run_id)) if ok else None), len(library) - len(ok)


def choose_nearest(library: list[LibraryEntry], context: ScenarioContext
                   ) -> tuple[LibraryEntry | None, float | None, int]:
    sit = situation_of(context)
    ok = [e for e in library if incompatibility(e, context) is None]
    if not ok:
        return None, None, len(library)
    best = min(ok, key=lambda e: (round(distance(sit, e.situation), 9), e.run_id))
    return best, round(distance(sit, best.situation), 4), len(library) - len(ok)


def search_order(library: list[LibraryEntry], context: ScenarioContext) -> list[dict[str, float]]:
    """F's frozen order over the day's lattice. Primary key from development data: number of distinct
    development days on which the tariff verified (more first). Ties: cheapest tier ascending, most
    expensive tier descending (widest spread pulls flexible load into the cheap window), middle tiers
    ascending. Uses no held-out information and no actual weather."""
    order = context.tier_order
    days: dict[tuple, set] = {}
    for e in library:
        if e.config_hash == context.config_hash and e.day < context.day:
            days.setdefault(_tkey(e.tariff), set()).add((e.scenario_id, e.day))

    def key(t):
        return (-len(days.get(_tkey(t), ())), t[order[0]], -t[order[-1]], *[t[x] for x in order[1:-1]])
    return sorted(lattice_tariffs(context), key=key)


# ---- one comparator day ------------------------------------------------------------------

def _check(env: Environment, requests, tariff: dict, context: ScenarioContext, dod_req: Requirements, dod,
           n: int, pid: str) -> tuple[CandidateCheck, Any, int, list, list[str]]:
    plan = env.allocate(requests, tariff, context, pid)
    verdicts, unmodeled, sims = [], [], 0
    for ph in FORECAST_PHASES:
        try:
            v = env.verify(env.simulate(plan, context, ph), dod_req, context, plan)
        except ContractError as exc:
            if not str(exc).startswith(("UNSUPPORTED_PHASE:", "UNSUPPORTED_PROFILE:")):
                raise
            unmodeled.append(ph)
            continue
        sims += 1
        verdicts.append(v)
    dod_fail = sorted({c for v in verdicts for c in dod.evaluate(v.metrics)}) if dod is not None else []
    passed = bool(verdicts) and not unmodeled and all(supervisor.complete_pass(v) for v in verdicts) and not dod_fail
    codes = sorted({f.code for v in verdicts for f in v.feedback_codes} | set(dod_fail))
    chk = CandidateCheck(n=n, proposal_id=pid, tariff=dict(tariff), passed=passed, feedback=codes,
                         phases={v.phase: supervisor.complete_pass(v) for v in verdicts},
                         verdict_ids=[v.run_id for v in verdicts], unmodeled=unmodeled)
    return chk, plan, sims, verdicts, dod_fail


def run_comparator(condition: str, context: ScenarioContext, candidates: list[dict], requirements: Requirements, *,
                   env: Environment, dod=None, actual: ActualScenario | None = None, source: dict | None = None,
                   empty_reason: str = "no candidate") -> ComparatorRun:
    """Check candidates in order, stop at the first pass, execute it on the actual day."""
    dod_req = dod.requirements(requirements) if dod is not None else requirements
    if hasattr(env, "use_requirements"):
        env.use_requirements(dod_req)
    rec = ComparatorRun(condition=condition, label=LABEL[condition], scenario_id=context.scenario_id,
                        env_name=env.name, status="NO_VERIFIED_PLAN", reason="", source=source or {},
                        definition_of_done=dod.spec_hash() if dod is not None else None)
    if env.lower_bound(context, dod_req) is not None:
        rec.status, rec.reason = "INFEASIBLE", "deterministic energy bound exceeds available energy"
        return rec
    if not candidates:
        rec.reason = empty_reason
        return rec
    requests = env.household_requests(context)
    plan = None
    for n, tariff in enumerate(candidates, 1):
        chk, p, sims, verdicts, dod_fail = _check(env, requests, tariff, context, dod_req, dod, n,
                                        f"{condition}-{context.scenario_id}-c{n}")
        rec.candidates.append(chk)
        rec.candidate_evaluations += 1
        rec.simulator_runs += sims
        if chk.passed:
            rec.status, rec.reason = "PLAN_VERIFIED", f"verified at candidate {n}"
            rec.selected_tariff, plan = dict(tariff), p
            break
        if not verdicts:
            rec.reason = "the world cannot check this scenario; not a verified plan"
            break
        if all(v.passed for v in verdicts) and all(c.startswith("DOD_UNCHECKED") for c in dod_fail):
            rec.reason = (f"checked conditions passed at candidate {n}, but the world's coverage is incomplete; "
                          "not a verified plan")     # same stop as the Supervisor: never claim done
            break
    else:
        rec.reason = (f"all {len(candidates)} candidate(s) failed" if condition != "F3"
                      else f"budget of {len(candidates)} exhausted")
    if plan is not None and actual is not None:
        try:
            v = env.verify(env.simulate(plan, context, "actual", actual), dod_req, context, plan)
        except ContractError as exc:
            if not str(exc).startswith(("UNSUPPORTED_PHASE:", "UNSUPPORTED_PROFILE:")):
                raise
            rec.reason += f"; actual execution not modeled by this world ({str(exc)[:60]})"
        else:
            rec.simulator_runs += 1
            rec.execution = ExecutionRecord(outcome_passed=supervisor.complete_pass(v), evidence_id=v.run_id,
                                            metrics=dict(v.metrics))
    return rec


def run_comparators(names: list[str], contexts: Callable, actuals: Callable, requirements: Requirements, *,
                    env: Environment, history: list[EvidenceRecord], dod=None,
                    log: Callable[[str], None] = print) -> list[ComparatorRun]:
    library = verified_library(history, dod)
    runs: list[ComparatorRun] = []
    for name in names:
        ctx, actual = contexts(name), actuals(name)
        last, rej_d = choose_last(library, ctx)
        near, dist, rej_e = choose_nearest(library, ctx)
        order = search_order(library, ctx)
        none = "no compatible candidate in the development library (no fallback)"
        plan = [
            ("D", [last.tariff] if last else [], {"entry": last and last.proposal_id, "day": last and last.day,
                                                  "rejected_incompatible": rej_d}),
            ("E", [near.tariff] if near else [], {"entry": near and near.proposal_id, "distance": dist,
                                                  "from_scenario": near and near.scenario_id,
                                                  "rejected_incompatible": rej_e}),
            ("F3", order[:F3_BUDGET], {"order_hash": stable_hash(order), "lattice_size": len(order)}),
            ("Ffull", order, {"order_hash": stable_hash(order), "lattice_size": len(order)}),
        ]
        for cond, cands, src in plan:
            r = run_comparator(cond, ctx, cands, requirements, env=env, dod=dod, actual=actual, source=src,
                               empty_reason=none)
            runs.append(r)
            log(f"{name:30} {LABEL[cond]:28} {r.status:17} checks={r.candidate_evaluations} "
                f"sims={r.simulator_runs} exec={'-' if not r.execution else ('PASS' if r.execution.outcome_passed else 'FAIL')}")
    return runs


# ---- one row format for all seven conditions --------------------------------------------------

def row_of(run: RunRecord | ComparatorRun) -> dict[str, Any]:
    if isinstance(run, RunRecord):
        cond, first = ABC[run.memory_mode], bool(run.attempts) and run.attempts[0].passed
        calls, tokens = len(run.calls), sum(c.prompt_tokens + c.completion_tokens for c in run.calls)
    else:
        cond, first = run.condition, bool(run.candidates) and run.candidates[0].passed
        calls, tokens = run.model_calls, run.tokens
    ex = run.execution
    return {"condition": cond, "label": LABEL[cond], "scenario": run.scenario_id, "status": run.status,
            "reason": run.reason, "verified": run.status == "PLAN_VERIFIED", "first_candidate_pass": first,
            "model_calls": calls, "tokens": tokens, "candidate_checks": run.candidate_evaluations,
            "simulator_runs": run.simulator_runs, "exec_pass": bool(ex and ex.outcome_passed),
            "served_actual": ex.metrics.get("served_fraction") if ex else None,
            "selected_tariff": run.selected_tariff}


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def aggregate(rows: list[dict]) -> dict[str, dict[str, Any]]:
    agg = {}
    for cond in CONDITIONS:
        rs = [r for r in rows if r["condition"] == cond]
        if not rs:
            continue
        served = [r["served_actual"] if r["served_actual"] is not None else 0.0 for r in rs]
        agg[cond] = {"n": len(rs), "verified": sum(r["verified"] for r in rs),
                     "first": sum(r["first_candidate_pass"] for r in rs), "exec": sum(r["exec_pass"] for r in rs),
                     "calls": _mean([r["model_calls"] for r in rs]), "tokens": _mean([r["tokens"] for r in rs]),
                     "checks": _mean([r["candidate_checks"] for r in rs]),
                     "sims": _mean([r["simulator_runs"] for r in rs]),
                     "served": _mean(served), "worst": min(served)}
    return agg


def interpretation(agg: dict[str, dict[str, Any]]) -> list[str]:
    """Honest-interpretation notes (protocol 'Results table and interpretation'), computed, not written."""
    if "C" not in agg:
        return ["Condition C did not run; no memory claim can be made."]
    c, out = agg["C"], []
    for x in ("A", "B", "D", "E", "F3", "Ffull"):
        if x not in agg:
            continue
        o = agg[x]
        verb = "beats" if c["verified"] > o["verified"] else ("ties" if c["verified"] == o["verified"] else "does NOT beat")
        exe = "beats" if c["exec"] > o["exec"] else ("ties" if c["exec"] == o["exec"] else "does NOT beat")
        out.append(f"C {verb} {x} on verified plans ({c['verified']}/{c['n']} vs {o['verified']}/{o['n']}); "
                   f"{exe} it on actual execution ({c['exec']}/{c['n']} vs {o['exec']}/{o['n']}); "
                   f"candidate checks/day {c['checks']:.2f} vs {o['checks']:.2f}.")
    beats_llm = all(c["verified"] > agg[x]["verified"] for x in ("A", "B") if x in agg)
    det = [x for x in ("D", "E", "F3", "Ffull") if x in agg and agg[x]["verified"] >= c["verified"]]
    if beats_llm and det:
        out.append(f"C beats A/B but not {', '.join(det)}: report a benefit to the LLM workflow, and that the "
                   f"deterministic baseline(s) {', '.join(det)} are as strong or stronger.")
    if "Ffull" in agg and agg["Ffull"]["verified"] >= c["verified"]:
        out.append(f"Full deterministic search over the same lattice verified {agg['Ffull']['verified']}/"
                   f"{agg['Ffull']['n']} days with 0 model calls ({agg['Ffull']['checks']:.2f} checks/day): this run "
                   "gives no evidence that the LLM is necessary to find a verified plan.")
    out.append("D/E/F make 0 inference calls, so fewer LLM calls is not a claim against them; compare candidate "
               "checks, simulator runs and service instead. Warm-up/curation overhead for C is not in these rows.")
    out.append("Small single-run sample: descriptive, not statistical significance. Failed days stay in every "
               "denominator.")
    return out


def results_table(rows: list[dict], header: dict[str, Any]) -> str:
    agg = aggregate(rows)
    t = ["| Condition | Verified | First candidate | Calls/day | Tokens/day | Candidate checks/day | "
         "Simulator runs/day | Exec pass | Mean served (actual) | Worst-day served |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for cond, a in agg.items():
        n = a["n"]
        t.append(f"| {LABEL[cond]} | {a['verified']}/{n} | {a['first']}/{n} | {a['calls']:.2f} | {a['tokens']:.0f} | "
                 f"{a['checks']:.2f} | {a['sims']:.2f} | {a['exec']}/{n} | {a['served']:.2f} | {a['worst']:.2f} |")
    mock = "mock" in str(header.get("model", "")).lower()
    stub = "stub" in str(header.get("world", "")).lower()
    tag = " · ".join(x for x in ("MOCK" if mock else "", "STUB world" if stub else "") if x)
    lines = [f"# Six-condition results{f' ({tag})' if tag else ''}", "",
             f"Model: **{header.get('model')}** · world: **{header.get('world')}** · commit {header.get('commit')} · "
             f"brain snapshot {str(header.get('brain_snapshot', ''))[:8]} · definition of done "
             f"{str(header.get('dod', ''))[:8]} · {header.get('days')} held-out days", "",
             "Per-day rows: `six-conditions.jsonl` (A/B/C raw records also in `agent-runs.jsonl`). One candidate "
             "check = allocate one tariff and verify both forecast phases; simulator runs include the actual-day "
             "replay for every condition. Served is the actual-weather served fraction; a day with no dispatched "
             "plan counts as 0 served (nothing cooks). D/E/F are the partner's labeled implementation for Abel to "
             "review.", ""]
    lines += t
    if mock:
        lines += ["", "**MOCK mode:** A/B/C use a rule-based stand-in for the model. These numbers test the "
                  "harness, not Nemotron and not the value of memory."]
    if stub:
        lines += ["", "**STUB world:** development stand-in, not Abel's simulator."]
    lines += ["", "## Interpretation", ""] + [f"- {x}" for x in interpretation(agg)]
    return "\n".join(lines) + "\n"


def compare(freeze_dir: Path, out_dir: Path, days: int | list[str], env: Environment, llm, dod, *,
            contexts: Callable | None = None, actuals: Callable | None = None,
            requirements: Requirements | None = None, log: Callable[[str], None] = print) -> str:
    """A/B/C via agent_eval.run_conditions and D/E/F3/Ffull on the same held-out days."""
    from greenstove import agent_eval, scenarios
    if contexts is None:
        from greenstove.cli import context_for as contexts
    actuals = actuals or scenarios.actual_for
    requirements = requirements or scenarios.DEFAULT_REQUIREMENTS
    names = list(scenarios.HELDOUT_SCENARIOS)[:days] if isinstance(days, int) else list(days)
    freeze_dir, out_dir = Path(freeze_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    abc = agent_eval.run_conditions(names, contexts, actuals, requirements, env=env, llm=llm, freeze_dir=freeze_dir,
                                    out=out_dir, log=log, dod=dod)
    _, history = agent_eval.load_frozen(freeze_dir)
    det = run_comparators(names, contexts, actuals, requirements, env=env, history=history, dod=dod, log=log)
    rows = []
    with (out_dir / "six-conditions.jsonl").open("w") as f:
        for run in list(abc) + list(det):
            row = row_of(run)
            rows.append(row)
            f.write(json.dumps({**row, "record": run.model_dump(mode="json")}) + "\n")
    info = {}
    if (freeze_dir / "freeze.json").exists():
        info = json.loads((freeze_dir / "freeze.json").read_text())
    header = {"model": llm.s.public()["model"] if llm.s.mode != "mock" else "mock-rules (MOCK)", "world": env.name,
              "commit": agent_eval.git_commit(), "brain_snapshot": info.get("brain_snapshot", ""),
              "dod": dod.spec_hash() if dod is not None else "none", "days": len(names)}
    text = results_table(rows, header)
    (out_dir / "results-table.md").write_text(text)
    (out_dir / "comparators-manifest.json").write_text(json.dumps({
        "conditions": list(CONDITIONS), "held_out": names, "freeze": info, "definition_of_done": header["dod"],
        "distance": DISTANCE, "f3_budget": F3_BUDGET,
        "f_order_rule": "dev verified-day count desc, cheapest tier asc, dearest tier desc, middle tiers asc",
        "library_size": len(verified_library(history, dod)), "requirements": requirements.model_dump(),
        "world": env.name, "llm_mode": llm.s.mode, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2))
    return text


def cmd_compare(a) -> int:
    from greenstove import cli, scenarios
    from greenstove.env_protocol import load_environment
    llm, env = cli.make_llm(a.mode), load_environment(prefer_stub=a.stub)
    cli.banner(llm, env.name)
    dod = cli.current_spec(llm, scenarios.DEFAULT_REQUIREMENTS)     # frozen: same for all seven conditions
    out = Path(a.out) if a.out else cli.data_dir() / f"compare-{time.strftime('%Y%m%d-%H%M%S')}"
    text = compare(Path(a.freeze), out, a.days, env, llm, dod, log=lambda s: cli.console.print(s, markup=False))
    cli.console.print(text, markup=False)
    cli.console.print(f"records: {out}")
    return 0


def register(sub) -> None:
    """Add `compare --freeze DIR [--out DIR] [--days N] [--mode] [--stub]` to cli.main()'s subparsers."""
    s = sub.add_parser("compare", help="six conditions A-F (A/B/C LLM + D/E/F3/Ffull non-LLM) on held-out days")
    s.add_argument("--freeze", required=True, help="freeze dir from `freeze` (brain + history.jsonl)")
    s.add_argument("--out", help="output folder (default <data dir>/compare-<stamp>)")
    s.add_argument("--days", type=int, default=8)
    s.add_argument("--mode", choices=["mock", "live", "crusoe"])
    s.add_argument("--stub", action="store_true")
    s.set_defaults(fn=cmd_compare)
