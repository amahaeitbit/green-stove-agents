"""Supervisor (code only): bounded attempts, honest stop reasons, evidence, memory writes.

run_agent_day starts a FRESH Pricing Agent request: no hidden conversation survives
between days, only the declared brief and this run's feedback. Requirements are fixed
and never relaxed. Status comes only from the verifier.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from greenstove import note_writer, pricing_agent
from greenstove.agent_models import (AttemptRecord, Brief, Decision, EvidenceRecord, ExecutionRecord, MemoryMode,
                                     RunPhase, RunRecord, situation_of)
from greenstove.brain import briefs, keeper
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import ActualScenario, ContractError, Environment, Requirements, ScenarioContext, Verdict
from greenstove.evidence import EvidenceStore
from greenstove.llm import LLMClient, ModelError
from greenstove.pricing_agent import hhmm
from greenstove.spec_agent import DefinitionOfDone

FORECAST_PHASES = ("forecast_expected", "forecast_downside")


def _score(verdicts: list[Verdict], dod_fail: list[str]) -> float:
    """Lower is better: failed conditions + definition-of-done failures + unserved share."""
    if not verdicts:
        return 99.0
    failed = sum(not c.passed for v in verdicts for c in v.conditions)
    served = sum(v.metrics.get("served_fraction", 0.0) for v in verdicts) / len(verdicts)
    return failed + len(dod_fail) + (1 - served)


def complete_pass(v: Verdict) -> bool:
    """A pass counts only if the world checked everything (Abel's C0 world is power-only)."""
    cov = getattr(v, "coverage", None)
    return v.passed and (cov is None or cov.complete)


def _put(evidence: EvidenceStore, ev: EvidenceRecord) -> None:
    if evidence.get(ev.run_id) is None:          # verdict ids are deterministic: identical evaluation, one record
        evidence.put(ev)


def make_brief(mode: MemoryMode, role: str, context: ScenarioContext, brain: BrainStore | None,
               history: list[EvidenceRecord], budget: int) -> Brief:
    if mode == "off":
        return briefs.empty_brief(role)
    if mode == "history":
        return briefs.compile_history_brief(role, history, token_budget=budget,
                                            config_hash=context.config_hash, clock=context.day)
    entries = brain.entries() if brain else []
    return briefs.compile_brief(role, situation_of(context), entries, token_budget=budget,
                                config_hash=context.config_hash, clock=context.day)


def _feedback_entry(n: int, prices: dict | None, verdicts: list[Verdict], errors: list[str]) -> dict[str, Any]:
    results = []
    for v in verdicts:
        codes = []
        for f in v.feedback_codes[:6]:
            item = {"code": f.code}
            if f.interval is not None:
                item["at"] = hhmm(f.interval)
            if f.request_id:
                item["request"] = f.request_id
            if f.value is not None:
                item["value"] = round(f.value, 3)
            if f.limit is not None:
                item["limit"] = round(f.limit, 3)
            codes.append(item)
        results.append({"phase": v.phase, "passed": v.passed, "codes": [c["code"] for c in codes],
                        "details": codes, "metrics": {k: round(x, 3) for k, x in v.metrics.items()}})
    entry: dict[str, Any] = {"attempt": n, "prices": prices, "results": results}
    if errors:
        entry["errors"] = errors
        entry["errors_codes"] = ["INVALID_TARIFF"]
    return entry


def _evidence(verdict: Verdict, run_id: str, context: ScenarioContext, sit: dict, tariff: dict,
              proposal_id: str) -> EvidenceRecord:
    return EvidenceRecord(run_id=verdict.run_id, agent_run_id=run_id, scenario_id=context.scenario_id,
                          config_hash=context.config_hash, phase=verdict.phase, day=context.day,
                          situation=sit, tariff=tariff, proposal_id=proposal_id, passed=verdict.passed,
                          metrics=dict(verdict.metrics), feedback_codes=sorted({f.code for f in verdict.feedback_codes}))


def run_agent_day(context: ScenarioContext, memory_mode: MemoryMode, requirements: Requirements, *,
                  env: Environment, llm: LLMClient, evidence: EvidenceStore, brain: BrainStore | None = None,
                  history: list[EvidenceRecord] | None = None, phase: RunPhase = "development",
                  actual: ActualScenario | None = None, max_attempts: int = 3, write_notes: bool | None = None,
                  brief_budget: int = 600, keeper_ttl_days: int = 30, playbook: str = "",
                  dod: "DefinitionOfDone | None" = None) -> RunRecord:
    run_id = f"run-{context.scenario_id}-{memory_mode}-{uuid.uuid4().hex[:6]}"
    llm.reset_budget()
    sit = situation_of(context)
    history = history if history is not None else [r for r in evidence.all() if r.day < context.day]
    brief = make_brief(memory_mode, "pricing", context, brain, history, brief_budget)
    rec = RunRecord(agent_run_id=run_id, scenario_id=context.scenario_id, memory_mode=memory_mode, phase=phase,
                    env_name=env.name, llm_mode=llm.s.mode, status="NO_VERIFIED_PLAN", reason="", brief=brief)
    if write_notes is None:
        write_notes = phase == "development"

    dod_req = dod.requirements(requirements) if dod is not None else requirements
    if dod is not None:
        rec.definition_of_done = {"spec_hash": dod.spec_hash(), "goal": dod.goal,
                                  "checks": [c.model_dump() for c in dod.checks]}

    def decide(n: int, what: str, why: str) -> None:
        rec.decisions.append(Decision(attempt=n, decision=what, reason=why))

    proof = env.lower_bound(context, dod_req)
    if proof is not None:
        rec.status, rec.reason = "INFEASIBLE", "deterministic energy bound exceeds available energy"
        rec.infeasibility = proof.model_dump()
        decide(0, "stop", "a deterministic energy bound proves the day cannot meet the definition of done")
        decide(0, "ping_human", "the committee decides which requests to defer; agents never relax requirements")
        evidence.save_run(rec)
        _write_decision(rec, context, env, dod_req, evidence)
        return rec

    requests = env.household_requests(context)
    evidence.write(run_id, "requests", requests)
    feedback: list[dict[str, Any]] = []
    seen: set[str] = set()
    run_records: list[EvidenceRecord] = []
    selected_plan = None
    best: tuple[float, int, dict] | None = None      # (score, attempt, prices); lower score is better
    worse_streak, n = 0, 0
    try:
        for n in range(1, max_attempts + 1):
            pid = f"{run_id}-p{n}"
            proposal, errors, _ = pricing_agent.propose(context, brief, feedback, llm, n, pid, playbook=playbook)
            att = AttemptRecord(n=n, proposal=proposal, validation_errors=errors)
            rec.attempts.append(att)
            if proposal is None:
                att.feedback = ["INVALID_TARIFF"]
                feedback.append(_feedback_entry(n, None, [], errors))
                decide(n, "re-plan", f"invalid proposal ({errors[0][:80] if errors else 'unparseable'}); ask again")
                continue
            h = proposal.tariff_hash()
            if h in seen:
                rec.reason = f"repeated_proposal at attempt {n}"
                decide(n, "stop", "looping: proposed a tariff already tried today")
                break
            seen.add(h)
            plan = env.allocate(requests, proposal.prices, context, pid)
            evidence.write(run_id, f"tariff_{n}", proposal)
            evidence.write(run_id, f"reservations_{n}", plan)
            verdicts, unmodeled = [], []
            for ph in FORECAST_PHASES:
                try:
                    v = env.verify(env.simulate(plan, context, ph), dod_req, context, plan)
                except ContractError as exc:             # world does not model this phase (e.g. Abel's C0)
                    unmodeled.append(ph)
                    evidence.write(run_id, f"unmodeled_{n}_{ph}", {"phase": ph, "error": str(exc)[:200]})
                    continue
                evidence.write(run_id, f"verdict_{n}_{ph}", v)
                rec.simulator_runs += 1
                ev = _evidence(v, run_id, context, sit, proposal.prices, pid)
                _put(evidence, ev)
                run_records.append(ev)
                att.evidence_ids.append(ev.run_id)
                verdicts.append(v)
            rec.candidate_evaluations += 1
            if not verdicts:                             # the world cannot check this scenario at all
                err = ""
                try:
                    err = json.loads((evidence.run_dir(run_id) / f"unmodeled_{n}_{FORECAST_PHASES[0]}.json").read_text())["error"]
                except (OSError, ValueError, KeyError):
                    pass
                att.feedback = ["WORLD_CANNOT_SIMULATE"]
                rec.reason = f"the world cannot check this scenario ({err or 'no phase modeled'}); not a verified plan"
                decide(n, "stop", "nothing can be verified on this world for this scenario; no further model calls")
                break
            dod_fail = sorted({c for v in verdicts for c in dod.evaluate(v.metrics)}) if dod is not None else []
            att.passed = bool(verdicts) and not unmodeled and all(complete_pass(v) for v in verdicts) and not dod_fail
            att.feedback = sorted({f.code for v in verdicts for f in v.feedback_codes} | set(dod_fail))
            only_unchecked = all(c.startswith("DOD_UNCHECKED") for c in dod_fail)
            if not att.passed and verdicts and all(v.passed for v in verdicts) and only_unchecked:
                att.feedback.append("INCOMPLETE_COVERAGE")
                scope = getattr(verdicts[0].coverage, "scope", "unknown")
                missing = f"; phases not modeled: {', '.join(unmodeled)}" if unmodeled else ""
                rec.reason = (f"checked conditions passed at attempt {n}, but the world's coverage is incomplete "
                              f"({scope}{missing}); not a verified plan")
                decide(n, "stop", "the world cannot check everything in the definition of done; never claim done")
                break
            if att.passed:
                rec.status, rec.reason = "PLAN_VERIFIED", f"verified at attempt {n}"
                rec.selected_proposal_id, rec.selected_tariff = pid, proposal.prices
                selected_plan = plan
                decide(n, "continue", "every check passed on the expected and downside forecasts; dispatch the plan")
                break
            entry = _feedback_entry(n, proposal.prices, verdicts, [])
            if dod_fail:
                entry["definition_of_done_failures"] = dod_fail
            score = _score(verdicts, dod_fail)
            if best is not None and score > best[0] + 1e-9:
                worse_streak += 1
                entry["drift_warning"] = (f"worse than attempt {best[1]} (score {score:.2f} vs {best[0]:.2f}); "
                                          f"move back toward attempt {best[1]}'s prices {best[2]}")
            else:
                worse_streak = 0
                best = (score, n, dict(proposal.prices))
            feedback.append(entry)
            if worse_streak >= 2:
                rec.reason = f"drifting: attempts got worse twice in a row (best was attempt {best[1]})"
                decide(n, "stop", rec.reason)
                break
            if n < max_attempts:
                decide(n, "re-plan", f"failed {', '.join(att.feedback[:4])}; new candidate from verifier feedback"
                       + ("; drift warning sent" if worse_streak else ""))
        else:
            rec.reason = f"attempt budget of {max_attempts} exhausted"
            decide(n, "stop", rec.reason)
    except ModelError as exc:
        rec.status, rec.reason = "ERROR", f"model_error: {exc}"
        decide(n, "stop", "model or network error; nothing is dispatched (not reported as infeasible)")
    if rec.status == "NO_VERIFIED_PLAN" and not rec.reason.startswith(("checked conditions passed",
                                                                        "the world cannot check")):
        decide(n, "ping_human", "no verified plan on a day not proven infeasible; nothing is dispatched, "
                                "the operator decides")

    if selected_plan is not None and actual is not None:
        try:
            v = env.verify(env.simulate(selected_plan, context, "actual", actual), dod_req, context, selected_plan)
        except ContractError as exc:
            rec.reason += f"; actual execution not modeled by this world ({str(exc)[:60]})"
        else:
            evidence.write(run_id, "verdict_actual", v)
            rec.simulator_runs += 1
            ev = _evidence(v, run_id, context, sit, rec.selected_tariff or {}, rec.selected_proposal_id or "")
            _put(evidence, ev)
            run_records.append(ev)
            rec.execution = ExecutionRecord(outcome_passed=complete_pass(v), evidence_id=ev.run_id,
                                            metrics=dict(v.metrics))

    if run_records and (write_notes or phase == "evaluation") and rec.status != "ERROR":
        _write_notes(rec, run_records, context, brain, evidence, llm, phase, write_notes, brief_budget, keeper_ttl_days)
    rec.calls = list(llm.calls)
    evidence.save_run(rec)
    _write_decision(rec, context, env, dod_req, evidence)
    return rec


def _write_decision(rec: RunRecord, context: ScenarioContext, env: Environment, requirements: Requirements,
                    evidence: EvidenceStore) -> None:
    """decision.md for every run (warm-up, runner, evaluation, CLI); never breaks the run."""
    from greenstove.decision import brief_lines, render_decision
    try:
        text = render_decision(context, rec, env, requirements, brief_lines(rec.brief.text))
    except Exception as exc:  # noqa: BLE001 - the record is a convenience, the run record is the truth
        text = f"# Day decision · {context.scenario_id}\n\nDecision record unavailable: {exc.__class__.__name__}.\n"
    (evidence.run_dir(rec.agent_run_id) / "decision.md").write_text(text)


def _write_notes(rec: RunRecord, run_records: list[EvidenceRecord], context: ScenarioContext,
                 brain: BrainStore | None, evidence: EvidenceStore, llm: LLMClient, phase: RunPhase,
                 write_notes: bool, budget: int, ttl: int) -> None:
    if not write_notes:
        return
    nw_brief = make_brief("curated", "note_writer", context, brain, [], budget)
    try:
        proposals, _, _ = note_writer.propose_observations(run_records, nw_brief, llm)
    except ModelError:
        return
    if phase == "evaluation" or brain is None or getattr(brain, "frozen", False):
        rec.quarantined_notes = proposals            # never served to any condition
        return
    cfg = keeper.KeeperConfig(config_hash=context.config_hash, clock=context.day, ttl_days=ttl)
    rec.notes = keeper.admit_all(proposals, evidence, cfg, brain)
