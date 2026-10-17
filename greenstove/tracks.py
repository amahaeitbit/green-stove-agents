"""Track conformance scorecard: one row per phrase of the three problem statements.

Each row runs the real code path (Spec Agent, Supervisor, Keeper, briefs, freeze + A/B/C,
runner, playbook gate, held-out before/after) in MOCK mode on the STUB world, inside an
isolated work folder, and records what was exercised, PASS/FAIL and the numbers behind it.
Scripted mock handlers force behaviours the default mock never shows (a model that claims
success on a failing tariff, a repeated tariff, proposals that get worse).

These rows test mechanisms, not model quality. Gains need live Nemotron runs.
The lead wires `tracks [--out DIR]` into cli.main() with `tracks.register(sub)`.
"""
from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from greenstove import agent_eval, evolve, note_writer, pricing_agent, runner, scenarios, spec_agent, supervisor
from greenstove.agent_models import ObservationProposal, RunRecord
from greenstove.brain import briefs, keeper
from greenstove.brain.store import BrainStore
from greenstove.evidence import EvidenceStore
from greenstove.llm import ROOT, LLMClient, LLMSettings
from greenstove.playbook import PlaybookStore
from greenstove.stub_env import StubEnvironment

HEADER = ("MOCK model · STUB world · these rows test the mechanisms, not model quality; "
          "live Nemotron runs are needed for evidence of gains")
REQ = scenarios.DEFAULT_REQUIREMENTS
DMD, SB, VN = "Done Means Done", "Shared Brain", "Version N+1"
HELD = ["ho-clear-low-battery", "ho-overcast-mid-battery", "ho-rainy-flexible"]
DRIFT_DAY = "dev-overcast-low-battery"
HARMFUL = "- when battery_frac >= 0: start sun=5 standard=10 peak=16"      # worst tariff first, every day
NEUTRAL = "- when battery_frac > 2: start sun=3 standard=6 peak=10"        # never matches: identical runs
RUNNER_DATE = "2026-10-18"


# ---- helpers -----------------------------------------------------------------------------

def _handlers(pricing: Callable | None = None, capture: dict[str, list] | None = None) -> dict[str, Callable]:
    base = {"pricing": pricing or pricing_agent.mock_pricing, "note_writer": note_writer.mock_note_writer,
            "spec_writer": spec_agent.mock_spec_writer, evolve.ROLE: evolve.mock_reviewer}
    if capture is None:
        return base

    def wrap(role: str, fn: Callable) -> Callable:
        def h(payload: dict) -> dict:
            capture.setdefault(role, []).append(payload)
            return fn(payload)
        return h
    return {r: wrap(r, f) for r, f in base.items()}


def _llm(work: Path, pricing: Callable | None = None, capture: dict | None = None) -> LLMClient:
    """Mock client built from explicit settings (never reads .env or GREENSTOVE_* variables)."""
    return LLMClient(LLMSettings(mode="mock"), _handlers(pricing, capture), usage_path=work / "usage.jsonl")


def _scripted(tariffs: list[dict[str, float]], rationale: str = "", seen: list | None = None) -> Callable:
    """Pricing handler that returns the given tariffs in order (repeating the last one)."""
    state = {"i": 0}

    def handler(payload: dict) -> dict:
        if seen is not None:
            seen.append(payload)
        t = tariffs[min(state["i"], len(tariffs) - 1)]
        state["i"] += 1
        return {"prices": dict(t), "cited_observation_ids": [], "rationale": rationale}
    return handler


def _rank(ctx, env, req, dod=None) -> list[tuple[float, bool, dict[str, float]]]:
    """(score, passes, tariff) for every lattice tariff; score mirrors the Supervisor's (lower is better)."""
    out = []
    for t in scenarios.lattice_tariffs(ctx):
        plan = env.allocate(env.household_requests(ctx), t, ctx, "tracks-rank")
        vs = [env.verify(env.simulate(plan, ctx, ph), req, ctx, plan) for ph in supervisor.FORECAST_PHASES]
        dod_fail = sorted({c for v in vs for c in dod.evaluate(v.metrics)}) if dod is not None else []
        failed = sum(not c.passed for v in vs for c in v.conditions)
        served = sum(v.metrics.get("served_fraction", 0.0) for v in vs) / len(vs)
        out.append((round(failed + len(dod_fail) + (1 - served), 6),
                    all(v.passed for v in vs) and not dod_fail, t))
    return sorted(out, key=lambda r: (r[0], sorted(r[2].items())))


def _tariff(t: dict[str, float] | None) -> str:
    return " ".join(f"{k}={v:g}" for k, v in sorted((t or {}).items())) or "-"


def _decisions(runs: list[RunRecord]) -> Counter:
    return Counter(d.decision for r in runs for d in r.decisions)


def _row(rid: int, track: str, requirement: str, check: str) -> dict[str, Any]:
    return {"id": str(rid), "track": track, "requirement": requirement, "check": check,
            "result": "FAIL", "evidence": ""}


def _run_check(row: dict[str, Any], fn: Callable[[], tuple[bool, str]]) -> dict[str, Any]:
    try:
        ok, evidence = fn()
    except Exception as exc:  # noqa: BLE001 - one broken mechanism is a FAIL row, not a crashed scorecard
        ok, evidence = False, f"error: {exc.__class__.__name__}: {str(exc)[:200]}"
    row["result"], row["evidence"] = ("PASS" if ok else "FAIL"), evidence
    return row


# ---- the scorecard -----------------------------------------------------------------------

def run_scorecard(out_dir: Path, log: Callable[[str], None] = print) -> list[dict]:
    out_dir = Path(out_dir)
    work = out_dir / "work" / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    work.mkdir(parents=True, exist_ok=True)
    env = StubEnvironment()
    rows: list[dict[str, Any]] = []
    st: dict[str, Any] = {}                      # shared results between rows

    def add(row: dict, fn: Callable[[], tuple[bool, str]]) -> None:
        rows.append(_run_check(row, fn))
        log(f"[{row['result']}] {row['id']:>2} {row['track']}: {row['requirement']}")

    # Spec Agent: the one-line goal becomes checks; frozen once, reused by every run below.
    llm = _llm(work)
    dod = spec_agent.write_spec(spec_agent.DEFAULT_GOAL, REQ, llm, cache_dir=work / "spec")
    dod_req = dod.requirements(REQ)

    # Development runs (mock agent, curated brain, notes on): rows 5, 6, 8, 9, 11, 13.
    capture: dict[str, list] = {}
    dev_llm = _llm(work, capture=capture)
    dev_ev, dev_brain = EvidenceStore(work / "dev" / "evidence"), BrainStore(work / "dev" / "brain")
    dev_runs: list[RunRecord] = []
    for name in scenarios.DEV_SCENARIOS:
        dev_runs.append(supervisor.run_agent_day(scenarios.scenario(name), "curated", REQ, env=env, llm=dev_llm,
                                                 evidence=dev_ev, brain=dev_brain,
                                                 actual=scenarios.actual_for(name), dod=dod))
    dev_runs.append(supervisor.run_agent_day(scenarios.infeasible_context(), "curated", REQ, env=env, llm=dev_llm,
                                             evidence=dev_ev, brain=dev_brain, dod=dod))
    st["dev_runs"] = dev_runs

    # ---------------- Done Means Done ----------------
    def r1() -> tuple[bool, str]:
        again = spec_agent.write_spec(spec_agent.DEFAULT_GOAL, REQ, llm, cache_dir=work / "spec")
        sources = Counter(c.source for c in dod.checks)
        tightened = [f"{c.metric}>={c.threshold:g}" for c in dod.checks if c.source == "code_tightened"]
        added = [c.metric for c in dod.checks if c.source == "code_added"]
        ev = EvidenceStore(work / "spec-abc" / "evidence")
        frozen = BrainStore(work / "spec-abc" / "brain")
        ctx = scenarios.scenario(HELD[0])
        hashes = {}
        for mode in agent_eval.MODES:
            run = supervisor.run_agent_day(ctx, mode, REQ, env=env, llm=llm, evidence=ev, brain=frozen, history=[],
                                           phase="evaluation", write_notes=False, dod=dod)
            hashes[mode] = (run.definition_of_done or {}).get("spec_hash")
        same = len(set(hashes.values())) == 1 and hashes["off"] == dod.spec_hash()
        ok = bool(dod.checks) and sources["code_tightened"] >= 1 and sources["code_added"] >= 1 \
            and again.spec_hash() == dod.spec_hash() and same
        return ok, (f"{len(dod.checks)} checks from DEFAULT_GOAL ({dict(sources)}); code_tightened {tightened} "
                    f"(mock proposed 0.65); code_added {added}; spec_hash {dod.spec_hash()} cached and re-read "
                    f"identical; A/B/C on {HELD[0]} all used spec {hashes['off']}")
    add(_row(1, DMD, "writes its own definition of done",
             "write_spec(DEFAULT_GOAL) with the mock that loosens one threshold and omits one check; "
             "re-read from cache; run_agent_day A/B/C with the same dod"), r1)

    ranked = _rank(scenarios.scenario(DRIFT_DAY), env, dod_req, dod)
    failing = [r for r in ranked if not r[1]]

    def r2() -> tuple[bool, str]:
        bad = failing[-1][2]
        claim = "All checks pass: this tariff is verified and the day is done."
        ev = EvidenceStore(work / "verifier" / "evidence")
        run = supervisor.run_agent_day(scenarios.scenario(DRIFT_DAY), "off", REQ, env=env,
                                       llm=_llm(work, pricing=_scripted([bad], claim)), evidence=ev,
                                       max_attempts=1, write_notes=False, dod=dod)
        att = run.attempts[0]
        ok = (att.proposal is not None and att.proposal.rationale == claim and not att.passed
              and run.status != "PLAN_VERIFIED" and bool(att.feedback))
        return ok, (f"{DRIFT_DAY}: model said '{claim}' for {_tariff(bad)}; verifier: attempt passed={att.passed}, "
                    f"codes {att.feedback[:4]}; status {run.status}; evidence {att.evidence_ids[:2]}")
    add(_row(2, DMD, "checks its work with tests or independent verifier agents",
             "scripted Pricing Agent claims success on a failing tariff; status comes from the verifier"), r2)

    def r3() -> tuple[bool, str]:
        bad = failing[0][2]
        ev = EvidenceStore(work / "loop" / "evidence")
        run = supervisor.run_agent_day(scenarios.scenario(DRIFT_DAY), "off", REQ, env=env,
                                       llm=_llm(work, pricing=_scripted([bad])), evidence=ev,
                                       write_notes=False, dod=dod)
        stop = [d for d in run.decisions if d.decision == "stop"]
        ok = bool(stop) and "looping" in stop[0].reason and run.reason.startswith("repeated_proposal")
        return ok, (f"same tariff {_tariff(bad)} proposed twice -> attempts={len(run.attempts)}, decision "
                    f"stop '{stop[0].reason if stop else '-'}', reason '{run.reason}', calls={len(run.calls)}")
    add(_row(3, DMD, "catches itself looping", "scripted Pricing Agent repeats a failing tariff"), r3)

    def r4() -> tuple[bool, str]:
        distinct = []
        for score, _, t in failing:
            if not distinct or score > distinct[-1][0] + 1e-9:
                distinct.append((score, t))
        if len(distinct) < 3:
            return False, f"{DRIFT_DAY}: only {len(distinct)} distinct failing scores; cannot script drift"
        picks = [distinct[0], distinct[len(distinct) // 2], distinct[-1]]
        seen: list[dict] = []
        ev = EvidenceStore(work / "drift" / "evidence")
        run = supervisor.run_agent_day(scenarios.scenario(DRIFT_DAY), "off", REQ, env=env,
                                       llm=_llm(work, pricing=_scripted([t for _, t in picks], seen=seen)),
                                       evidence=ev, max_attempts=3, write_notes=False, dod=dod)
        warnings = [e.get("drift_warning") for p in seen for e in p["earlier_attempts_today"] if e.get("drift_warning")]
        stop = [d for d in run.decisions if d.decision == "stop"]
        ok = bool(warnings) and bool(stop) and stop[0].reason.startswith("drifting")
        return ok, (f"{DRIFT_DAY}, max_attempts=3: scores {[round(s, 2) for s, _ in picks]} (best failing tariff first, then worse); "
                    f"drift_warning sent to attempt 3: '{(warnings[0] if warnings else '-')[:70]}'; "
                    f"decision stop '{stop[0].reason if stop else '-'}'")
    add(_row(4, DMD, "catches itself drifting",
             "scripted proposals: attempt 1 near-pass, attempts 2-3 clearly worse"), r4)

    def r5() -> tuple[bool, str]:
        c = _decisions(dev_runs)
        ok = all(c[k] > 0 for k in ("continue", "re-plan", "stop", "ping_human"))
        return ok, (f"{len(dev_runs)} development days (5 dev + infeasible fixture), mock agent: "
                    f"continue={c['continue']} re-plan={c['re-plan']} stop={c['stop']} ping_human={c['ping_human']}")
    add(_row(5, DMD, "decides whether to continue, re-plan, stop or ping a human",
             "decision trail (RunRecord.decisions) across the development days"), r5)

    def r6() -> tuple[bool, str]:
        pinged = [r for r in dev_runs if any(d.decision == "ping_human" for d in r.decisions)]
        ok = all(r.status in ("NO_VERIFIED_PLAN", "INFEASIBLE") for r in pinged) and \
            not any(r.status == "PLAN_VERIFIED" for r in pinged)
        return ok, (f"ping rate {len(pinged)}/{len(dev_runs)}; pinged days: "
                    f"{[(r.scenario_id, r.status) for r in pinged]}; verified days pinged: 0")
    add(_row(6, DMD, "(rarely) ping a human",
             "ping_human only on NO_VERIFIED_PLAN / INFEASIBLE days across all development days"), r6)

    def r7() -> tuple[bool, str]:
        root = work / "runner"
        ctx = runner.RunnerContext(data_dir=root, env=env, llm=_llm(work), evidence=EvidenceStore(root / "evidence"),
                                   brain=BrainStore(root / "brain"), requirements=REQ, max_calls_per_day=20)
        runner.emit({"type": "day_start", "id": "tracks-ds-1", "date": RUNNER_DATE,
                     "scenario": "demo-handoff-overcast"}, root)
        runner.drain(ctx)
        state = runner.load_state(ctx.paths)
        day = state["days"][RUNNER_DATE]
        calls_before = state["calls_per_day"][RUNNER_DATE]
        trace = day.get("expected_battery_kwh")
        if trace:
            step = max(range(len(trace)), key=lambda i: trace[i])
            battery = round(max(0.0, trace[step] - 1.5), 3)
        else:
            step, battery = 72, round(max(0.0, day["reserve_kwh"] - 0.5), 3)
        runner.emit({"type": "telemetry", "id": "tracks-tel-1", "date": RUNNER_DATE, "step": step,
                     "battery_kwh": battery, "pv_kw": 0.0}, root)
        runner.drain(ctx)
        rows_log = [json.loads(x) for x in ctx.paths.log.read_text().splitlines()]
        ds, tel = rows_log[0], rows_log[-1]
        dec = Path(ds["outputs"][0]) if ds["outputs"] else None
        dec = dec if dec is None or dec.is_absolute() else ROOT / dec
        alerts = [json.loads(x) for x in ctx.paths.alerts.read_text().splitlines()] if ctx.paths.alerts.exists() else []
        after = runner.load_state(ctx.paths)["calls_per_day"][RUNNER_DATE]
        ok = (ds["outcome"].startswith("planned") and dec is not None and dec.name == "decision.md" and dec.exists()
              and tel["outcome"].startswith("alert") and tel["model_calls"] == 0 and len(alerts) == 1
              and alerts[0]["human_ping"] and after == calls_before)
        return ok, (f"goal -> {len(dod.checks)} runnable checks; day_start event, no human input: "
                    f"'{ds['outcome']}', {ds['model_calls']} calls, decision.md at {dec}; telemetry battery "
                    f"{battery:g} kWh at step {step}: '{tel['outcome'][:80]}', model calls 0, alerts {len(alerts)}")
    add(_row(7, DMD, "turn a one-line goal into checks they can run without you / walk away",
             "always-on runner: day_start and telemetry events drained with no human input"), r7)

    # ---------------- Shared Brain ----------------
    def r8() -> tuple[bool, str]:
        with_mem = [r for r in dev_runs if r.brief.entry_ids and r.status != "INFEASIBLE"]   # infeasible: no agent call
        bad = []
        for r in with_mem:
            pr = [c for c in r.calls if c.role == "pricing"]
            nw = [c for c in r.calls if c.role == "note_writer"]
            if not pr or any(c.memory_hash != r.brief.snapshot_hash or not c.memory_hash for c in pr) \
                    or any(not c.memory_hash for c in nw):
                bad.append(r.scenario_id)
        payloads = capture.get("pricing", [])
        briefed = sum("SHARED BRAIN" in p.get("shared_memory_brief", "") and "obs-" in p["shared_memory_brief"]
                      for p in payloads)
        nw_calls = sum(1 for r in with_mem for c in r.calls if c.role == "note_writer")
        ok = bool(with_mem) and not bad
        return ok, (f"{len(with_mem)}/{len(dev_runs)} days had memory and called agents (infeasible day: 0 calls); their Pricing calls carry memory_hash = "
                    f"brief snapshot ({with_mem[0].brief.snapshot_hash[:8] if with_mem else '-'}), "
                    f"{nw_calls} Note-writer calls carry a memory_hash; pricing payloads with brain entries "
                    f"{briefed}/{len(payloads)}; mismatches {bad or 0}")
    add(_row(8, SB, "every agent reads before it works",
             "CallRecord.memory_hash and the brief in each Pricing / Note-writer request"), r8)

    def r9() -> tuple[bool, str]:
        actions = Counter(n.action for r in dev_runs for n in r.notes)
        admitted = actions["added"] + actions["confirmed"] + actions["kept_separate"]
        audit = [a for a in dev_brain.audit_log() if a.get("event") == "admission"]
        ok = admitted > 0 and len(dev_brain.entries()) > 0
        return ok, (f"development notes: {dict(actions)}; brain entries {len(dev_brain.entries())}; "
                    f"audited admissions {len(audit)}; snapshot {dev_brain.snapshot_hash()[:8]}")
    add(_row(9, SB, "updates afterward", "Note-writer proposals admitted by the Keeper after each development day"), r9)

    def r10() -> tuple[bool, str]:
        e = dev_brain.entries()[0]
        inside = briefs.compile_brief("pricing", e.situation, [e], config_hash=e.config_hash, clock=e.expires_at - 1)
        expired = briefs.compile_brief("pricing", e.situation, [e], config_hash=e.config_hash, clock=e.expires_at)
        short = e.model_copy(update={"expires_at": e.created_at + 1})          # ttl 1 day
        ttl1 = briefs.compile_brief("pricing", e.situation, [short], config_hash=e.config_hash, clock=e.created_at + 1)
        ok = e.id in inside.entry_ids and e.id not in expired.entry_ids and e.id not in ttl1.entry_ids
        return ok, (f"{e.id} created day {e.created_at}, expires {e.expires_at}: served at clock "
                    f"{e.expires_at - 1} ({inside.entry_ids}), excluded at {e.expires_at} ({expired.entry_ids}); "
                    f"with ttl 1 excluded on day {e.created_at + 1} ({ttl1.entry_ids})")
    add(_row(10, SB, "keep shared knowledge current", "expired entries are never compiled into a brief"), r10)

    def r11() -> tuple[bool, str]:
        over = [r.scenario_id for r in dev_runs if r.brief.tokens > r.brief.budget]
        maxtok = max((r.brief.tokens for r in dev_runs), default=0)
        tight = briefs.compile_brief("pricing", supervisor.situation_of(scenarios.scenario(HELD[0])),
                                     dev_brain.entries(), token_budget=120, config_hash=scenarios.config_hash(),
                                     clock=12)
        small = BrainStore(work / "cap3" / "brain", cap=3)
        ev = EvidenceStore(work / "cap3" / "evidence")
        cap_llm = _llm(work)
        for name in scenarios.DEV_SCENARIOS:
            supervisor.run_agent_day(scenarios.scenario(name), "curated", REQ, env=env, llm=cap_llm, evidence=ev,
                                     brain=small, actual=scenarios.actual_for(name))
        current = [e for e in small.entries() if e.status == "current"]
        evicted = sum(1 for a in small.audit_log() if a.get("event") == "evicted_for_cap")
        dev_current = [e for e in dev_brain.entries() if e.status == "current"]
        ok = (not over and tight.tokens <= 120 and len(current) <= 3 and len(dev_current) <= dev_brain.cap)
        return ok, (f"brief tokens max {maxtok} <= budget {dev_runs[0].brief.budget} on every day; 120-token "
                    f"brief: {tight.tokens} tokens, {len(tight.entry_ids)} of {len(dev_brain.entries())} entries; "
                    f"dev store {len(dev_current)} current <= cap {dev_brain.cap}; cap-3 store: "
                    f"{len(current)} current, {evicted} evicted for cap")
    add(_row(11, SB, "keep shared knowledge lean", "token budget on every brief and the store cap with eviction"), r11)

    def r12() -> tuple[bool, str]:
        kb = BrainStore(work / "keeper" / "brain")
        failed_run = next(r for r in dev_runs if any(a.proposal and not a.passed and a.evidence_ids for a in r.attempts))
        att = next(a for a in failed_run.attempts if a.proposal and not a.passed and a.evidence_ids)
        rec = dev_ev.get(att.evidence_ids[0])
        cfg = keeper.KeeperConfig(config_hash=rec.config_hash, clock=rec.day)
        fake = ObservationProposal(evidence_ids=att.evidence_ids, situation_tags=["pass"],
                                   description="These prices passed every check.", claimed_passed=True,
                                   claimed_tariff=att.proposal.prices, claimed_phase="forecast")
        r_fake = keeper.admit(fake, dev_ev, cfg, kb)
        ghost = keeper.admit(ObservationProposal(evidence_ids=["ev-never-written"], description="Prices worked."),
                             dev_ev, cfg, kb)
        # Opposite outcomes for one situation bucket and tariff, both from real verified runs.
        pair = None
        for seed in (3, 11, 21, 31):
            for weather in ("overcast", "partly_cloudy", "rainy_afternoon", "clear"):
                a = scenarios.make_context(seed, weather, 0.35, day=6, scenario_id=f"tracks-conflict-a-{weather}-{seed}")
                b = scenarios.make_context(seed, weather, 0.45, day=7, scenario_id=f"tracks-conflict-b-{weather}-{seed}")
                pass_b = {tuple(sorted(t.items())) for _, p, t in _rank(b, env, REQ) if p}
                t = next((t for _, p, t in _rank(a, env, REQ) if not p and tuple(sorted(t.items())) in pass_b), None)
                if t:
                    pair = (a, b, t)
                    break
            if pair:
                break
        if pair is None:
            return False, "no tariff fails at battery 0.35 and passes at 0.45 in the stub; conflict not exercised"
        a, b, t = pair
        cev, cbrain = EvidenceStore(work / "conflict" / "evidence"), BrainStore(work / "conflict" / "brain")
        notes = []
        for ctx in (a, b):
            run = supervisor.run_agent_day(ctx, "curated", REQ, env=env, llm=_llm(work, pricing=_scripted([t])),
                                           evidence=cev, brain=cbrain, max_attempts=1)
            notes += [n for n in run.notes]
        cur = [e for e in cbrain.entries() if e.status == "current"]
        groups = {e.conflict_group for e in cur if e.conflict_group}
        first = cbrain.get(notes[0].observation_id) if notes and notes[0].observation_id else None
        ok = (r_fake.action == "rejected" and r_fake.code == "result_mismatch" and ghost.code == "evidence_unknown"
              and [n.action for n in notes] == ["added", "kept_separate"] and len(groups) == 1
              and first is not None and first.outcome_passed is False and len(cur) == 2)
        return ok, (f"fabricated 'passed' note on failing evidence {att.evidence_ids[0]}: {r_fake.action} "
                    f"({r_fake.code}); unknown evidence id: {ghost.action} ({ghost.code}); tariff {_tariff(t)} "
                    f"FAIL at battery 0.35, PASS at 0.45 ({a.weather_class}): Keeper "
                    f"{[n.action for n in notes]}, both current in conflict group {sorted(groups)}, "
                    f"first entry {first.id if first else '-'} still FAIL (not overwritten)")
    add(_row(12, SB, "keep shared knowledge conflict-free",
             "Keeper: fabricated note rejected with a code; opposite outcome kept separate in a conflict group"), r12)

    def r13() -> tuple[bool, str]:
        fdir, out = work / "freeze", work / "abc"
        out.mkdir(parents=True, exist_ok=True)
        info = agent_eval.freeze_memory(dev_ev, dev_brain, fdir)
        eval_llm = _llm(work)
        runs = agent_eval.run_conditions(HELD, scenarios.scenario, scenarios.actual_for, REQ, env=env, llm=eval_llm,
                                         freeze_dir=fdir, out=out, log=lambda s: None)
        lines = [json.loads(x) for x in (out / "agent-runs.jsonl").read_text().splitlines()]
        stale = sum(x["stale_served"] for x in lines)
        after = BrainStore.load_frozen(fdir / "brain").snapshot_hash()
        agent_eval.summarize(runs, out, {"model": "mock-rules", "world": env.name, "commit": agent_eval.git_commit(),
                                         "brain_snapshot": info["brain_snapshot"], "days": len(HELD)})
        per = []
        for mode in agent_eval.MODES:
            rs = [r for r in runs if r.memory_mode == mode]
            n = len(rs)
            first = sum(bool(r.attempts) and r.attempts[0].passed for r in rs)
            calls = sum(len(r.calls) for r in rs) / max(1, n)
            toks = sum(c.prompt_tokens + c.completion_tokens for r in rs for c in r.calls) / max(1, n)
            per.append(f"{agent_eval.LABEL[mode]}: first-candidate {first}/{n}, calls/day {calls:.2f}, "
                       f"tokens/day {toks:.0f}")
        st["abc"] = per
        ok = (len(runs) == 3 * len(HELD) and stale == 0 and after == info["brain_snapshot"]
              and all(r.phase == "evaluation" and not r.notes for r in runs))
        return ok, (f"MOCK; frozen snapshot {info['brain_snapshot'][:8]} ({info['brain_entries']} entries, "
                    f"{info['history_records']} history records), unchanged after eval; {len(HELD)} held-out days; "
                    f"stale entries served {stale}; " + " | ".join(per) + " (gain reported, not asserted)")
    add(_row(13, SB, "showing measurable gains in output quality",
             "freeze_memory + run_conditions A/B/C on held-out days, summarize"), r13)

    # ---------------- Version N+1 ----------------
    pb_root = work / "playbook"
    tuning = list(scenarios.DEV_SCENARIOS)
    pb_llm = _llm(work)

    def r14() -> tuple[bool, str]:
        pairs = evolve.run_playbook_days("", tuning, env, pb_llm, out_dir=work / "review-input")
        held_pair = evolve.run_playbook_days("", [HELD[0]], env, pb_llm, out_dir=work / "review-input-held")
        payload = evolve.review_payload("", pairs + held_pair, scenarios.scenario(tuning[0]).tier_order)
        dev = payload["development_runs"]
        codes = sorted({c for r in dev for a in r["attempts"] if not a["passed"] for c in a["feedback"]})
        worked = sum(r["status"] == "PLAN_VERIFIED" for r in dev)
        ok = len(dev) == len(tuning) and bool(codes) and worked > 0
        return ok, (f"reviewer input: {len(dev)} development runs ({worked} verified), held-out runs offered "
                    f"{len(held_pair)}, dropped {len(pairs) + len(held_pair) - len(dev)}; failure codes "
                    f"{codes[:6]}")
    add(_row(14, VN, "reviews what worked and what didn't",
             "evolve.review_payload from development runs (held-out runs filtered out)"), r14)

    def r15() -> tuple[bool, str]:
        res = evolve.evolve(1, pb_root, env, pb_llm, tuning=tuning, log=lambda s: None)
        st["evolve"] = res[0]
        hist = PlaybookStore(pb_root).history()
        cand = [v for v in hist if v["author"] == "reviewer-agent"]
        ok = bool(cand)
        text = PlaybookStore(pb_root).get(cand[0]["id"]).text if cand else ""
        return ok, (f"round 1: candidate {cand[0]['id'] if cand else '-'} by reviewer-agent "
                    f"({len(text)} chars): '{text[:90]}'; decision {res[0]['decision']}: {res[0]['reason'][:90]}")
    add(_row(15, VN, "rewrites its own prompts, playbooks, tools, or team structure",
             "evolve(1 round): mock reviewer rewrites the playbook into a new candidate version"), r15)

    def r16() -> tuple[bool, str]:
        base = evolve.evaluate_playbook("", tuning, env, pb_llm)
        harm = evolve.evaluate_playbook(HARMFUL, tuning, env, pb_llm)
        neut = evolve.evaluate_playbook(NEUTRAL, tuning, env, pb_llm)
        e = st.get("evolve") or {}
        helpful_text = PlaybookStore(pb_root).get(e["candidate"]).text if e.get("candidate") else ""
        helpful = evolve.evaluate_playbook(helpful_text, tuning, env, pb_llm)
        kh, why_h = evolve.keep_decision(harm, base)
        kn, why_n = evolve.keep_decision(neut, base)
        kk, why_k = evolve.keep_decision(helpful, base)
        ok = not kh and not kn and kk and e.get("decision") == "kept"
        fmt = lambda s: f"first {s['first_candidate']}/{s['n']} verified {s['verified']}/{s['n']} calls {s['calls']:.2f}"  # noqa: E731
        return ok, (f"tuning set {len(tuning)} dev days; seed: {fmt(base)}; harmful: {fmt(harm)} -> {why_h[:60]}; "
                    f"neutral: {fmt(neut)} -> {why_n[:40]}; reviewer candidate: {fmt(helpful)} -> {why_k[:50]}")
    add(_row(16, VN, "keeps a change only if it measurably helps",
             "evolve.keep_decision on harmful, neutral and reviewer-written candidates vs the seed"), r16)

    def r17() -> tuple[bool, str]:
        refused = False
        try:
            evolve.before_after(pb_root, env, pb_llm, scenarios=[tuning[0]])
        except ValueError:
            refused = True
        res = evolve.before_after(pb_root, env, pb_llm, scenarios=HELD)
        rows_ba = res["rows"]
        unseen = set(res["scenarios"]) <= set(scenarios.HELDOUT_SCENARIOS) and not set(res["scenarios"]) & set(tuning)
        st["before_after"] = rows_ba
        table = "; ".join(f"{r['label']} {r['version']}: verified {r['verified']}/{r['n']}, first "
                          f"{r['first_candidate']}/{r['n']}, calls/day {r['calls']:.2f}" for r in rows_ba)
        ok = refused and unseen and len(rows_ba) == 2 and rows_ba[0]["version"] != rows_ba[1]["version"]
        return ok, (f"MOCK; {len(HELD)} held-out days {HELD}; dev scenario refused={refused}; {table}; "
                    f"{res['verdict'][:90]}; table {pb_root / 'before-after.md'}")
    add(_row(17, VN, "before-and-after score on tasks it hasn't seen",
             "evolve.before_after seed vs active playbook on HELDOUT scenarios only"), r17)

    _write(out_dir, work, rows, st)
    return rows


# ---- output ------------------------------------------------------------------------------

def _cell(s: str) -> str:
    return str(s).replace("|", "/").replace("\n", " ")


def _write(out_dir: Path, work: Path, rows: list[dict], st: dict[str, Any]) -> None:
    passed = sum(r["result"] == "PASS" for r in rows)
    data = {"header": HEADER, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "work_dir": str(work),
            "passed": passed, "total": len(rows), "all_pass": passed == len(rows), "rows": rows}
    (out_dir / "tracks-scorecard.json").write_text(json.dumps(data, indent=2))
    lines = ["# Track conformance scorecard", "", HEADER, "",
             f"{passed}/{len(rows)} rows PASS · generated {data['generated_at']} · work folder `{work}`", "",
             "| # | Track | Requirement | Check (what was exercised) | Result | Evidence |",
             "| --- | --- | --- | --- | --- | --- |"]
    lines += [f"| {r['id']} | {r['track']} | \"{_cell(r['requirement'])}\" | {_cell(r['check'])} | "
              f"**{r['result']}** | {_cell(r['evidence'])} |" for r in rows]
    if st.get("abc"):
        lines += ["", "A/B/C on frozen memory (MOCK, held-out days; reported, not asserted):", ""]
        lines += [f"- {x}" for x in st["abc"]]
    lines += ["", "What this does not prove: the model is a rule-based mock and the world is the development "
              "stub, samples are a handful of days, and scripted handlers force the looping, drifting and "
              "false-success cases. Live Nemotron runs on the real world are needed for evidence of gains.", ""]
    (out_dir / "tracks-scorecard.md").write_text("\n".join(lines))


# ---- CLI ---------------------------------------------------------------------------------

def cmd_tracks(a) -> int:
    from rich.console import Console
    from rich.markup import escape
    from rich.table import Table
    console = Console()
    if a.out:
        out = Path(a.out)
    else:
        from greenstove import cli
        out = cli.data_dir() / "tracks"
    out.mkdir(parents=True, exist_ok=True)
    console.print(f"[bold yellow]{escape(HEADER)}[/]")
    rows = run_scorecard(out, log=lambda s: None)
    table = Table(title="Track conformance scorecard", show_lines=True)
    for col in ("#", "Track", "Requirement", "Result", "Evidence"):
        table.add_column(col, overflow="fold")
    for r in rows:
        colour = "green" if r["result"] == "PASS" else "red"
        table.add_row(r["id"], r["track"], escape(r["requirement"]), f"[{colour}]{r['result']}[/]",
                      escape(r["evidence"]))
    console.print(table)
    passed = sum(r["result"] == "PASS" for r in rows)
    console.print(f"{passed}/{len(rows)} PASS", markup=False)
    console.print(f"written: {out / 'tracks-scorecard.md'} and {out / 'tracks-scorecard.json'}", markup=False)
    return 0 if passed == len(rows) else 1


def register(sub) -> None:
    """Add `tracks [--out DIR]` to cli.main()'s argparse subparsers object."""
    s = sub.add_parser("tracks", help="track conformance scorecard (MOCK, stub world): one row per requirement")
    s.add_argument("--out", help="output folder (default <data dir>/tracks)")
    s.set_defaults(fn=cmd_tracks)
