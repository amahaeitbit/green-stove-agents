"""Version N+1: the team reviews its development runs, rewrites its playbook, keeps a change
only if it measurably helps, then proves it before/after on held-out days it never saw.

SEPARATE, LABELED EXPERIMENT. It does not change the headline A/B/C Shared Brain experiment:
every evaluation here runs condition A (memory "off") so the playbook effect is isolated from
the Shared Brain, writes no notes, uses a throwaway evidence store and never touches the brain.

Loop per round: reviewer (LLM role "playbook_reviewer") reads the active playbook plus compact
summaries of DEVELOPMENT runs (never held-out) -> candidate playbook -> fresh agent days on the
development tuning set for candidate vs active -> keep rule -> kept candidate becomes active.
Keep rule: verified rate does not drop AND (first-candidate pass rate improves OR mean model
calls drop). Ties reject.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field

from greenstove import pricing_agent, supervisor
from greenstove.agent_models import RunRecord, situation_of, stable_hash
from greenstove.env_protocol import Environment, Requirements
from greenstove.evidence import EvidenceStore
from greenstove.llm import LLMClient, ModelError
from greenstove.playbook import (MAX_CHARS, PlaybookError, PlaybookStore, features_from_situation,
                                 parse_directives, text_hash, validate_text)

ROLE = "playbook_reviewer"
CONDITION = "off"                    # condition A: facts only, so memory cannot mask the playbook

REVIEWER_SYSTEM = f"""You are the Playbook Reviewer for a team of pricing agents at a simulated
community solar cooking station. You read the team's current playbook and compact summaries of
recent DEVELOPMENT runs: the day's situation (weather class, battery fraction, forecast PV,
demand), each attempted tariff, the verifier's feedback codes, the final status and the number of
model calls. Rewrite the playbook so the Pricing Agent's FIRST proposal passes more often and it
needs fewer attempts. Keep what worked, remove lines that misled, add lines only for patterns
the runs show.
Hard limits: at most {MAX_CHARS} characters; every line is a bullet starting with "- "; you can
never change rules (tariff windows, the allowed price list, tier order, battery reserve,
inverter limit, service requirements, household preferences) and never name specific scenario
or day ids. Prefer machine-readable directives:
"- when <feature> <op> <value> [and ...]: start sun=<price> standard=<price> peak=<price>"
with features battery_frac, weather (in [..]), forecast_pv_kwh, downside_pv_kwh, demand_kwh,
peak_request_kw and prices taken from tariffs that the verifier passed.
Reply with ONLY a JSON object:
{{"playbook": "<the full revised playbook>", "changes": ["<one line per change>"],
 "rationale": "<one or two sentences>"}}"""


class ReviewerOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    playbook: str = Field(max_length=4000)          # the 900-character limit is enforced by the store
    changes: list[str] = Field(default_factory=list, max_length=12)
    rationale: str = Field(default="", max_length=800)


# ---- run summaries (development only) ---------------------------------------------------

def _heldout_ids() -> set[str]:
    from greenstove import scenarios
    return set(scenarios.HELDOUT_SCENARIOS)


def summarize_runs(pairs: list[tuple[RunRecord, dict]]) -> list[dict[str, Any]]:
    """Compact, id-free summaries of development runs. Held-out/evaluation runs are dropped."""
    held = _heldout_ids()
    out = []
    for run, sit in pairs:
        if run.phase != "development" or run.scenario_id in held:
            continue
        out.append({
            "day": len(out) + 1,
            "situation": {k: sit[k] for k in ("weather_class", "battery_frac", "forecast_pv_kwh",
                                              "downside_pv_kwh", "demand_kwh", "peak_request_kw") if k in sit},
            "status": run.status,
            "attempts": [{"n": a.n, "prices": a.proposal.prices if a.proposal else None, "passed": a.passed,
                          "feedback": a.feedback} for a in run.attempts],
            "calls": len(run.calls),
        })
    return out


def review_payload(playbook: str, pairs: list[tuple[RunRecord, dict]], tier_order: list[str]) -> dict[str, Any]:
    return {"current_playbook": playbook or "(empty)", "tier_order": tier_order,
            "development_runs": summarize_runs(pairs)}


# ---- deterministic mock reviewer (tests the mechanism, not model quality) ---------------

def _start_text(prices: dict[str, float], order: list[str]) -> str:
    names = [n for n in order if n in prices] + sorted(n for n in prices if n not in order)
    return " ".join(f"{n}={prices[n]:g}" for n in names)


def mock_reviewer(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop directives whose start failed the first check; add one for each day that verified
    only after a failed first attempt, scoped by the first failure's codes."""
    current = payload.get("current_playbook", "")
    lines = [ln.strip() for ln in ("" if current == "(empty)" else current).splitlines() if ln.strip()]
    order = payload.get("tier_order") or []
    runs = payload.get("development_runs", [])
    changes: list[str] = []

    def first_match(feats: dict) -> str | None:
        for d in parse_directives("\n".join(lines)):
            if d.matches(feats):
                return d.line
        return None

    for r in runs:                                   # what didn't work: a directive that misled
        feats = features_from_situation(r["situation"])
        line = first_match(feats)
        first = r["attempts"][0] if r["attempts"] else None
        if line and first and not first["passed"]:
            d = parse_directives(line)[0]
            if first["prices"] and all(abs(first["prices"].get(k, -1) - v) < 1e-9 for k, v in d.start.items()):
                lines.remove(line)
                changes.append(f"removed '{line}': its start failed the first check "
                               f"({', '.join(first['feedback']) or 'no codes'})")
    for r in runs:                                   # what worked: a tariff found only after a retry
        if r["status"] != "PLAN_VERIFIED" or not r["attempts"] or r["attempts"][0]["passed"]:
            continue
        sit, feats = r["situation"], features_from_situation(r["situation"])
        if first_match(feats):
            continue
        codes = set(r["attempts"][0]["feedback"])
        winner = next(a["prices"] for a in r["attempts"] if a["passed"])
        if codes & {"BATTERY_RESERVE", "ESSENTIAL_UNSERVED"} and "battery_frac" in sit:
            cond = f"battery_frac < {round(sit['battery_frac'] + 0.1, 2):g}"
        elif "INVERTER_LIMIT" in codes and "demand_kwh" in sit:
            cond = f"demand_kwh > {round(sit['demand_kwh'] * 0.95, 1):g}"
        else:
            cond = f"weather in [{sit.get('weather_class', 'unknown')}]"
        line = f"- when {cond}: start {_start_text(winner, order)}"
        lines.append(line)
        changes.append(f"added '{line}': first attempt failed with {sorted(codes)}, "
                       f"verified at attempt {next(a['n'] for a in r['attempts'] if a['passed'])}")
    while len("\n".join(lines)) > MAX_CHARS and lines:
        changes.append(f"dropped oldest '{lines.pop(0)}' to fit {MAX_CHARS} characters")
    return {"playbook": "\n".join(lines), "changes": changes,
            "rationale": "Rules derived from failure patterns in development runs." if changes
            else "No failure pattern with a verified fix; playbook unchanged."}


MOCKS = {ROLE: mock_reviewer}


def review(playbook: str, pairs: list[tuple[RunRecord, dict]], llm: LLMClient,
           tier_order: list[str]) -> tuple[ReviewerOutput | None, list, str | None]:
    llm.mock_handlers.setdefault(ROLE, mock_reviewer)
    llm.reset_budget()
    out, records, error = llm.complete_json(ROLE, REVIEWER_SYSTEM, review_payload(playbook, pairs, tier_order),
                                            ReviewerOutput)
    return out, records, error


# ---- gate --------------------------------------------------------------------------------

_DOD = None                                       # frozen definition of done (set by the CLI), same for every version


def set_definition_of_done(dod) -> None:
    global _DOD
    _DOD = dod

def run_playbook_days(text: str, names: list[str], env: Environment, llm: LLMClient, *,
                      requirements: Requirements | None = None, contexts: Callable | None = None,
                      actuals: Callable | None = None, out_dir: Path | None = None,
                      max_attempts: int = 3) -> list[tuple[RunRecord, dict]]:
    """Fresh condition-A development days with this playbook. No brain, no notes, throwaway evidence."""
    from greenstove import scenarios
    requirements = requirements or scenarios.DEFAULT_REQUIREMENTS
    contexts, actuals = contexts or scenarios.scenario, actuals or scenarios.actual_for
    tmp = None
    if out_dir is None:
        tmp = tempfile.TemporaryDirectory(prefix="gsa-playbook-eval-")
        out_dir = Path(tmp.name)
    try:
        evidence = EvidenceStore(Path(out_dir) / "evidence")
        pairs = []
        for name in names:
            ctx = contexts(name)
            run = supervisor.run_agent_day(ctx, CONDITION, requirements, env=env, llm=llm, evidence=evidence,
                                           brain=None, history=[], phase="development", actual=actuals(name),
                                           max_attempts=max_attempts, write_notes=False, playbook=text,
                                           dod=_DOD)
            pairs.append((run, situation_of(ctx)))
        with (Path(out_dir) / "runs.jsonl").open("w") as f:
            for run, _ in pairs:                     # playbook hash returned alongside each RunRecord
                f.write(json.dumps({"playbook_hash": text_hash(text), "run": run.model_dump(mode="json")}) + "\n")
        return pairs
    finally:
        if tmp is not None:
            tmp.cleanup()


def score_runs(runs: list[RunRecord]) -> dict[str, Any]:
    n = len(runs)
    mean = lambda xs: round(sum(xs) / n, 4) if n else 0.0  # noqa: E731
    ver = sum(r.status == "PLAN_VERIFIED" for r in runs)
    first = sum(bool(r.attempts) and r.attempts[0].passed for r in runs)
    exe = sum(bool(r.execution and r.execution.outcome_passed) for r in runs)
    return {"n": n, "verified": ver, "first_candidate": first, "exec_pass": exe,
            "verified_rate": round(ver / n, 4) if n else 0.0, "first_candidate_rate": round(first / n, 4) if n else 0.0,
            "exec_pass_rate": round(exe / n, 4) if n else 0.0,
            "calls": mean([len(r.calls) for r in runs]),
            "tokens": mean([sum(c.prompt_tokens + c.completion_tokens for c in r.calls) for r in runs]),
            "candidate_checks": mean([r.candidate_evaluations for r in runs]),
            "scenarios": [r.scenario_id for r in runs]}


def evaluate_playbook(text: str, scenarios: list[str], env: Environment, llm: LLMClient, **kw) -> dict[str, Any]:
    """Scores for fresh condition-A agent days under this playbook (see run_playbook_days)."""
    return score_runs([r for r, _ in run_playbook_days(text, scenarios, env, llm, **kw)])


def keep_decision(cand: dict[str, Any], base: dict[str, Any]) -> tuple[bool, str]:
    eps = 1e-9
    if cand["verified_rate"] < base["verified_rate"] - eps:
        return False, (f"rejected: verified rate dropped {base['verified']}/{base['n']} -> "
                       f"{cand['verified']}/{cand['n']}")
    if cand["first_candidate_rate"] > base["first_candidate_rate"] + eps:
        return True, (f"kept: first-candidate pass {base['first_candidate']}/{base['n']} -> "
                      f"{cand['first_candidate']}/{cand['n']}, verified {cand['verified']}/{cand['n']} held")
    if cand["calls"] < base["calls"] - eps:
        return True, f"kept: calls/day {base['calls']:.2f} -> {cand['calls']:.2f}, verified rate held"
    return False, (f"rejected: no measurable gain (first-candidate {cand['first_candidate']}/{cand['n']} vs "
                   f"{base['first_candidate']}/{base['n']}, calls/day {cand['calls']:.2f} vs {base['calls']:.2f}); "
                   "ties reject")


# ---- the loop ----------------------------------------------------------------------------

def _tuning_default() -> list[str]:
    from greenstove import scenarios
    return list(scenarios.DEV_SCENARIOS)


def evolve(rounds: int, root: Path, env: Environment, llm: LLMClient, *, tuning: list[str] | None = None,
           log: Callable[[str], None] = print, **kw) -> list[dict[str, Any]]:
    """Review -> candidate -> gate on the development tuning set -> keep/reject, `rounds` times."""
    from greenstove import scenarios
    tuning = tuning or _tuning_default()
    held = _heldout_ids()
    if set(tuning) & held:
        raise ValueError("the tuning set must not contain held-out scenarios")
    store = PlaybookStore(root)
    evals = Path(root) / "evals"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    active = store.active()
    tier_order = scenarios.scenario(tuning[0]).tier_order
    base_pairs = run_playbook_days(active.text, tuning, env, llm, out_dir=evals / f"{stamp}-r0-{active.id}", **kw)
    base = score_runs([r for r, _ in base_pairs])
    log(f"round 0 · active {active.id}: {_line(base)}")
    results = []
    for rnd in range(1, rounds + 1):
        entry: dict[str, Any] = {"round": rnd, "active_before": active.id, "active_score": base}
        try:
            out, records, error = review(active.text, base_pairs, llm, tier_order)
        except ModelError as exc:
            out, records, error = None, list(llm.calls), f"model_error: {exc}"
        entry["reviewer_calls"] = len(records)
        if out is None:
            entry.update(decision="no_proposal", reason=f"reviewer gave no valid output: {error}")
        elif text_hash(out.playbook) == text_hash(active.text):
            entry.update(decision="no_change", reason="reviewer proposed no change; nothing to test",
                         rationale=out.rationale)
        elif (reasons := validate_text(out.playbook)):
            entry.update(decision="invalid", reason="; ".join(reasons), changes=out.changes)
        else:
            try:
                cid = store.propose(out.playbook, parent=active.id, author="reviewer-agent")
            except PlaybookError as exc:
                entry.update(decision="invalid", reason=str(exc))
            else:
                cand_pairs = run_playbook_days(out.playbook, tuning, env, llm,
                                               out_dir=evals / f"{stamp}-r{rnd}-{cid}", **kw)
                cand = score_runs([r for r, _ in cand_pairs])
                keep, reason = keep_decision(cand, base)
                store.decide(cid, keep, {"set": "development tuning", "candidate": cand, "active": base,
                                         "active_id": active.id}, reason)
                entry.update(decision="kept" if keep else "rejected", candidate=cid, candidate_score=cand,
                             reason=reason, changes=out.changes, rationale=out.rationale)
                if keep:
                    active, base, base_pairs = store.get(cid), cand, cand_pairs
        entry["active_after"] = active.id
        log(f"round {rnd} · {entry['decision']}"
            f"{' ' + entry['candidate'] if 'candidate' in entry else ''} · {entry['reason']}")
        for c in entry.get("changes", []):
            log(f"    change: {c}")
        with (Path(root) / "evolve-log.jsonl").open("a") as f:
            f.write(json.dumps(entry) + "\n")
        results.append(entry)
    return results


def _line(s: dict[str, Any]) -> str:
    return (f"verified {s['verified']}/{s['n']} · first-candidate {s['first_candidate']}/{s['n']} · "
            f"calls/day {s['calls']:.2f} · checks/day {s['candidate_checks']:.2f}")


# ---- before/after on unseen tasks -------------------------------------------------------

def before_after(root: Path, env: Environment, llm: LLMClient, *, scenarios: list[str] | None = None,
                 **kw) -> dict[str, Any]:
    """Seed playbook vs final active playbook on HELDOUT_SCENARIOS, same model/settings, condition A."""
    from greenstove import scenarios as sc
    held = list(sc.HELDOUT_SCENARIOS)
    names = scenarios or held
    if not set(names) <= set(held):
        raise ValueError("before/after runs only on held-out scenarios")
    store = PlaybookStore(root)
    seed, final = store.seed(), store.active()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    rows = []
    for label, v in (("before", seed), ("after", final)):
        s = evaluate_playbook(v.text, names, env, llm, out_dir=Path(root) / "evals" / f"{stamp}-heldout-{label}-{v.id}",
                              **kw)
        rows.append({"label": label, "version": v.id, "author": v.meta["author"], "hash": v.meta["hash"], **s})
    before, after = rows
    if seed.id == final.id:
        verdict = "No candidate was kept: the active playbook is still the seed, so before equals after."
    else:
        helped, why = keep_decision(after, before)
        verdict = (f"Held-out check under the same keep rule: {'HELPED' if helped else 'DID NOT HELP'} "
                   f"({why.split(': ', 1)[1]}).")
    mock, stub = llm.s.mode == "mock", "stub" in env.name
    result = {"experiment": "Version N+1 (playbook self-improvement), separate from the headline A/B/C",
              "condition": "A (facts only, memory off)", "set": "held-out", "scenarios": names,
              "model": "mock-rules (MOCK)" if mock else llm.s.model_for("pricing"), "llm_mode": llm.s.mode,
              "world": env.name, "settings": llm.s.public("pricing"), "generated_at": stamp,
              "prompt_hashes": {"pricing": stable_hash(pricing_agent.SYSTEM + pricing_agent.PLAYBOOK_NOTE),
                                "playbook_reviewer": stable_hash(REVIEWER_SYSTEM)},
              "rows": rows, "verdict": verdict, "labels": [x for x, on in (("MOCK", mock), ("STUB WORLD", stub)) if on]}
    (Path(root) / "before-after.json").write_text(json.dumps(result, indent=2))
    (Path(root) / "before-after.md").write_text(render_before_after(result, final.text))
    return result


def render_before_after(result: dict[str, Any], final_text: str) -> str:
    head = " · ".join(result["labels"])
    lines = [f"# Version N+1 before/after{' — ' + head if head else ''}", "",
             f"Separate experiment, not the headline A/B/C claim. Condition {result['condition']}; "
             f"{len(result['scenarios'])} held-out days never used for review or tuning; model "
             f"**{result['model']}**; world **{result['world']}**; same settings for both rows.", "",
             "| Version | Verified | First candidate | Calls/day | Tokens/day | Checks/day | Exec pass |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for r in result["rows"]:
        n = r["n"]
        lines.append(f"| {r['label']} {r['version']} ({r['author']}) | {r['verified']}/{n} | {r['first_candidate']}/{n} | "
                     f"{r['calls']:.2f} | {r['tokens']:.0f} | {r['candidate_checks']:.2f} | {r['exec_pass']}/{n} |")
    lines += ["", result["verdict"], "", "Final active playbook:", "", "```", final_text.strip() or "(empty)", "```"]
    if "MOCK" in result["labels"]:
        lines += ["", "**MOCK:** rule-based stand-ins for the reviewer and the Pricing Agent. The mock agent obeys "
                  "`start` directives literally, so this tests the mechanism (review, gate, held-out check), "
                  "not Nemotron's ability to write or use a playbook."]
    if "STUB WORLD" in result["labels"]:
        lines += ["", "**STUB WORLD:** development stand-in, not Abel's simulator."]
    lines += ["", f"Small sample ({len(result['scenarios'])} days): a difference of one day is not significant."]
    return "\n".join(lines) + "\n"


# ---- CLI (the lead wires `register(sub)` into greenstove/cli.py) --------------------------

def _setup(mode: str | None, stub: bool):
    from greenstove import cli
    from greenstove.scenarios import DEFAULT_REQUIREMENTS
    from greenstove.env_protocol import load_environment
    llm = cli.make_llm(mode)
    llm.mock_handlers.setdefault(ROLE, mock_reviewer)
    env = load_environment(prefer_stub=stub)
    cli.banner(llm, env.name)
    set_definition_of_done(cli.current_spec(llm, DEFAULT_REQUIREMENTS))   # same "done" for every version
    return cli, llm, env, cli.data_dir() / "playbook"


def cmd_playbook(a) -> int:
    from greenstove import cli
    root = cli.data_dir() / "playbook"
    if a.action == "before-after":
        cli, llm, env, root = _setup(a.mode, a.stub)
        res = before_after(root, env, llm)
        cli.console.print((root / "before-after.md").read_text(), markup=False)
        cli.console.print(f"written: {root / 'before-after.md'} and .json", markup=False)
        return 0 if res["rows"] else 1
    store = PlaybookStore(root)
    if a.action == "history":
        for v in store.history():
            ds = (v.get("dev_score") or {}).get("candidate")
            score = f" dev first-candidate {ds['first_candidate']}/{ds['n']} calls {ds['calls']:.2f}" if ds else ""
            cli.console.print(f"{v['id']} [{v['status']}] {v['author']} parent={v['parent']} hash={v['hash']} "
                              f"{v['created_at']}{score} · {v['reason']}", markup=False)
        return 0
    act = store.active()
    cli.console.print(f"active playbook {act.id} ({act.meta['author']}, hash {act.meta['hash']}, "
                      f"{len(act.text)} chars):", markup=False)
    cli.console.print(act.text or "(empty)", markup=False)
    return 0


def cmd_evolve(a) -> int:
    cli, llm, env, root = _setup(a.mode, a.stub)
    cli.console.print("Version N+1 · separate experiment · condition A (memory off) · development tuning set",
                      markup=False)
    evolve(a.rounds, root, env, llm, log=lambda s: cli.console.print(s, markup=False))
    act = PlaybookStore(root).active()
    cli.console.print(f"active playbook: {act.id}. Next: playbook before-after (held-out)", markup=False)
    return 0


def register(sub) -> None:
    """Add `playbook show|history|before-after` and `evolve --rounds N` to an argparse subparsers object."""
    modes = ["mock", "live", "crusoe"]
    s = sub.add_parser("playbook", help="Version N+1 playbook: show | history | before-after (held-out)")
    s.add_argument("action", choices=["show", "history", "before-after"], nargs="?", default="show")
    s.add_argument("--mode", choices=modes); s.add_argument("--stub", action="store_true")
    s.set_defaults(fn=cmd_playbook)
    s = sub.add_parser("evolve", help="Version N+1: review dev runs, rewrite the playbook, keep only measured gains")
    s.add_argument("--rounds", type=int, default=2); s.add_argument("--mode", choices=modes)
    s.add_argument("--stub", action="store_true"); s.set_defaults(fn=cmd_evolve)
