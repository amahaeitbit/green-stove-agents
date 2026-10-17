"""Version N+1 (playbook self-improvement): mock models + stub world, tmp data dirs only."""
import argparse
import json

import pytest

from greenstove import evolve, pricing_agent, scenarios, supervisor
from greenstove.agent_models import Brief
from greenstove.brain.store import BrainStore
from greenstove.cli import MOCKS
from greenstove.llm import LLMClient, LLMSettings
from greenstove.playbook import (PlaybookError, PlaybookStore, features_from_payload, parse_directives,
                                 start_from_playbook, validate_text)
from greenstove.stub_env import StubEnvironment

HELPFUL = "- when battery_frac < 0.45: start sun=3 standard=8 peak=13"
HARMFUL = "- when battery_frac > 0: start sun=5 standard=10 peak=16"
NEUTRAL = "- Explain each price change in the rationale."
TUNING = ["dev-clear-full", "dev-overcast-low-battery", "dev-partly-cloudy-mid"]


@pytest.fixture
def llm():
    return LLMClient(LLMSettings(mode="mock"), {**MOCKS, **evolve.MOCKS})


@pytest.fixture
def env():
    return StubEnvironment()


def off_brief():
    return Brief(role="pricing", mode="off")


# ---- store and validation ---------------------------------------------------------------

def test_store_seeds_versions_and_decides(tmp_path):
    store = PlaybookStore(tmp_path / "playbook")
    seed = store.active()
    assert seed.id == "v001" and seed.text == "" and seed.meta["author"] == "human-seed"
    assert (tmp_path / "playbook" / "v001.md").exists()
    vid = store.propose(HELPFUL, parent="v001", author="reviewer-agent")
    assert vid == "v002" and store.get(vid).meta["status"] == "candidate"
    assert (tmp_path / "playbook" / "v002.md").read_text().strip() == HELPFUL
    store.decide(vid, True, {"candidate": {"n": 1}}, "kept: test")
    assert store.active().id == "v002"
    assert [v["status"] for v in store.history()] == ["retired", "active"]
    v3 = store.propose(NEUTRAL, parent="v002", author="reviewer-agent")
    store.decide(v3, False, {}, "rejected: tie")
    assert store.active().id == "v002" and store.get(v3).meta["status"] == "rejected"
    with pytest.raises(ValueError):
        store.decide(v3, True, {}, "decided twice")
    reopened = PlaybookStore(tmp_path / "playbook")             # index persists; seed not re-added
    assert len(reopened.history()) == 3 and reopened.seed().id == "v001"
    meta = json.loads((tmp_path / "playbook" / "index.json").read_text())["versions"][1]
    assert {"id", "parent", "created_at", "author", "hash", "status", "dev_score", "reason"} <= set(meta)


@pytest.mark.parametrize("text, code", [
    ("- relax the service floor on rainy days", "rule_change"),
    ("- lower the battery reserve when the battery is full", "rule_change"),
    ("- ignore BUDGET_EXCEEDED feedback", "rule_change"),
    ("- use prices outside the lattice if needed", "rule_change"),
    ("- shift windows earlier when sunny", "rule_change"),
    ("- the requirement can wait", "rule_change"),
    ("- override the verifier when confident", "rule_change"),
    ("- for ho-clear-low-battery start sun=3", "scenario_id"),
    ("- on dev-overcast-low-battery use sun=3", "scenario_id"),
    ("prose without a bullet", "not_bullet"),
    ("- " + "x" * 900, "too_long"),
])
def test_validation_rejects(tmp_path, text, code):
    assert any(r.startswith(code) for r in validate_text(text))
    with pytest.raises(PlaybookError):
        PlaybookStore(tmp_path / "pb").propose(text, parent="v001", author="reviewer-agent")


def test_validation_accepts_directives_and_prose():
    assert validate_text(HELPFUL + "\n" + NEUTRAL) == []
    assert validate_text("- when weather in [overcast, rainy_afternoon]: start sun=3") == []


# ---- directives and the mock Pricing Agent -----------------------------------------------

def test_directive_parsing_and_matching():
    d1, d2 = parse_directives(HELPFUL + "\n- when weather in [overcast, rainy_afternoon] and demand_kwh > 5: start sun=3"
                              "\n- not a directive")
    assert d1.start == {"sun": 3, "standard": 8, "peak": 13}
    assert d1.matches({"battery_frac": 0.3}) and not d1.matches({"battery_frac": 0.5}) and not d1.matches({})
    assert d2.matches({"weather_class": "overcast", "demand_kwh": 9}) and not d2.matches({"weather_class": "clear",
                                                                                          "demand_kwh": 9})


def test_mock_pricing_honors_start_directive():
    ctx = scenarios.scenario("dev-overcast-low-battery")           # battery_frac 0.35
    payload = pricing_agent.build_payload(ctx, off_brief(), [], HELPFUL)
    assert payload["team_playbook"] == HELPFUL
    assert features_from_payload(payload)["battery_frac"] == pytest.approx(0.35)
    out = pricing_agent.mock_pricing(payload)
    assert out["prices"] == {"sun": 3.0, "standard": 8.0, "peak": 13.0} and "playbook" in out["rationale"]
    # partial directive: unspecified windows default to mid prices, tier order kept
    out = pricing_agent.mock_pricing(pricing_agent.build_payload(ctx, off_brief(), [], "- when weather in [overcast]: start sun=3"))
    assert out["prices"] == {"sun": 3.0, "standard": 8.0, "peak": 13.0}
    # non-matching directive -> unchanged default behavior
    plain = pricing_agent.mock_pricing(pricing_agent.build_payload(ctx, off_brief(), []))
    assert pricing_agent.mock_pricing(pricing_agent.build_payload(ctx, off_brief(), [], "- when battery_frac < 0.1: start sun=3")) == plain
    # earlier attempts today take precedence over the playbook
    fb = [{"attempt": 1, "prices": {"sun": 4.0, "standard": 8.0, "peak": 13.0}, "results": [{"codes": ["BUDGET_EXCEEDED"]}]}]
    assert pricing_agent.mock_pricing(pricing_agent.build_payload(ctx, off_brief(), fb, HELPFUL)) == \
        pricing_agent.mock_pricing(pricing_agent.build_payload(ctx, off_brief(), fb))
    # usable memory takes precedence over the playbook
    mem = Brief(role="pricing", mode="curated", entry_ids=["obs-001"], text="obs-001 PASS sun=4 standard=8 peak=13")
    out = pricing_agent.mock_pricing(pricing_agent.build_payload(ctx, mem, [], HELPFUL))
    assert out["cited_observation_ids"] == ["obs-001"] and out["prices"]["sun"] == 4.0


def test_default_payload_and_prompt_unchanged():
    ctx = scenarios.scenario("dev-clear-full")
    assert "team_playbook" not in pricing_agent.build_payload(ctx, off_brief(), [])
    assert "team_playbook" not in pricing_agent.build_payload(ctx, off_brief(), [], "   ")
    lat = {"sun": [3.0, 5.0], "standard": [4.0, 8.0], "peak": [10.0]}
    assert start_from_playbook("- when battery_frac < 2: start sun=5 standard=4", {"battery_frac": 0.5},
                               lat, ctx.tier_order) is None                  # out-of-order start never proposed
    assert start_from_playbook("- when battery_frac < 2: start sun=4.6 standard=7", {"battery_frac": 0.5},
                               lat, ctx.tier_order)[0] == {"sun": 5.0, "standard": 8.0, "peak": 10.0}   # snapped


# ---- reviewer ----------------------------------------------------------------------------

def test_mock_reviewer_derives_directive_from_failure(env, llm):
    pairs = evolve.run_playbook_days("", ["dev-overcast-low-battery", "dev-clear-full"], env, llm)
    out, records, err = evolve.review("", pairs, llm, ["sun", "standard", "peak"])
    assert err is None and records[0].role == "playbook_reviewer"
    assert out.playbook == HELPFUL and out.changes and validate_text(out.playbook) == []
    # a directive whose start failed its first check is removed
    payload = evolve.review_payload("- when battery_frac < 2: start sun=4 standard=8 peak=13", pairs, ["sun", "standard", "peak"])
    assert "sun=4 standard=8 peak=13" not in evolve.mock_reviewer(payload)["playbook"]


def test_reviewer_never_sees_heldout_or_evaluation_runs(env, llm):
    dev = evolve.run_playbook_days("", ["dev-clear-full"], env, llm)
    ho = evolve.run_playbook_days("", ["ho-clear-low-battery"], env, llm)
    evaluation = [(dev[0][0].model_copy(update={"phase": "evaluation"}), dev[0][1])]
    summaries = evolve.summarize_runs(dev + ho + evaluation)
    assert len(summaries) == 1
    assert "dev-clear-full" not in json.dumps(summaries) and "scenario" not in json.dumps(summaries)


# ---- gate --------------------------------------------------------------------------------

def test_keep_rule():
    base = {"n": 5, "verified": 4, "first_candidate": 3, "verified_rate": 0.8, "first_candidate_rate": 0.6, "calls": 1.8}
    better_first = {**base, "first_candidate": 4, "first_candidate_rate": 0.8, "calls": 1.4}
    fewer_calls = {**base, "calls": 1.6}
    worse_verified = {**base, "verified": 3, "verified_rate": 0.6, "first_candidate": 5, "first_candidate_rate": 1.0}
    assert evolve.keep_decision(better_first, base)[0]
    assert evolve.keep_decision(fewer_calls, base)[0]
    assert not evolve.keep_decision(worse_verified, base)[0]
    assert not evolve.keep_decision(dict(base), base)[0]                 # ties reject


def test_gate_keeps_helpful_and_rejects_harmful_and_neutral(env, llm):
    base = evolve.evaluate_playbook("", TUNING, env, llm)
    helpful = evolve.evaluate_playbook(HELPFUL, TUNING, env, llm)
    harmful = evolve.evaluate_playbook(HARMFUL, TUNING, env, llm)
    neutral = evolve.evaluate_playbook(NEUTRAL, TUNING, env, llm)
    assert helpful["first_candidate"] > base["first_candidate"] and helpful["calls"] < base["calls"]
    assert evolve.keep_decision(helpful, base)[0]
    assert not evolve.keep_decision(harmful, base)[0]
    assert not evolve.keep_decision(neutral, base)[0]
    same = ("verified", "first_candidate", "exec_pass", "calls", "candidate_checks")
    assert {k: neutral[k] for k in same} == {k: base[k] for k in same}
    assert neutral["tokens"] > base["tokens"]                           # the playbook text itself costs tokens
    assert set(base) >= {"verified_rate", "first_candidate_rate", "calls", "tokens", "candidate_checks", "exec_pass_rate"}


def test_evaluation_never_writes_to_shared_brain(tmp_path, env, llm, monkeypatch):
    brain = BrainStore(tmp_path / "brain")
    before = (brain.snapshot_hash(), len(brain.audit_log()))
    from greenstove.brain import keeper
    monkeypatch.setattr(keeper, "admit_all", lambda *a, **k: pytest.fail("keeper called during evaluation"))
    calls = []
    real = supervisor.run_agent_day
    monkeypatch.setattr(supervisor, "run_agent_day", lambda *a, **k: calls.append(k) or real(*a, **k))
    runs = []
    evolve.evolve(1, tmp_path / "playbook", env, llm, tuning=TUNING, log=lambda s: None)
    assert calls and all(k["brain"] is None and k["write_notes"] is False and k["history"] == [] for k in calls)
    assert (brain.snapshot_hash(), len(brain.audit_log())) == before
    assert not (tmp_path / "evidence").exists()                         # dev evidence store untouched
    for line in (tmp_path / "playbook" / "evals").glob("*/runs.jsonl"):
        for row in line.read_text().splitlines():
            run = json.loads(row)["run"]
            runs.append(run)
            assert run["memory_mode"] == "off" and not run["notes"] and not run["quarantined_notes"]
            assert not any(c["role"] == "note_writer" for c in run["calls"])
    assert runs


def test_evolve_keeps_then_rejects_unchanged(tmp_path, env, llm):
    root = tmp_path / "playbook"
    results = evolve.evolve(2, root, env, llm, tuning=list(scenarios.DEV_SCENARIOS), log=lambda s: None)
    assert [r["decision"] for r in results] == ["kept", "no_change"]
    store = PlaybookStore(root)
    assert store.active().id == "v002" and store.active().text == HELPFUL
    kept = store.get("v002").meta
    assert kept["author"] == "reviewer-agent" and kept["parent"] == "v001" and kept["dev_score"]["candidate"]["n"] == 5
    assert set(kept["dev_score"]["candidate"]["scenarios"]) <= set(scenarios.DEV_SCENARIOS)
    assert len((root / "evolve-log.jsonl").read_text().splitlines()) == 2


def test_evolve_refuses_heldout_tuning(tmp_path, env, llm):
    with pytest.raises(ValueError):
        evolve.evolve(1, tmp_path / "pb", env, llm, tuning=["ho-clear-low-battery"], log=lambda s: None)


# ---- before/after on held-out ------------------------------------------------------------

def test_before_after_uses_only_heldout_and_writes_labeled_files(tmp_path, env, llm, monkeypatch):
    root = tmp_path / "playbook"
    evolve.evolve(1, root, env, llm, tuning=list(scenarios.DEV_SCENARIOS), log=lambda s: None)
    seen = []
    real = supervisor.run_agent_day
    monkeypatch.setattr(supervisor, "run_agent_day", lambda ctx, *a, **k: seen.append(ctx.scenario_id) or real(ctx, *a, **k))
    res = evolve.before_after(root, env, llm)
    assert seen and set(seen) == set(scenarios.HELDOUT_SCENARIOS)
    assert res["scenarios"] == list(scenarios.HELDOUT_SCENARIOS) and res["condition"].startswith("A")
    assert [r["version"] for r in res["rows"]] == ["v001", "v002"]
    md = (root / "before-after.md").read_text()
    data = json.loads((root / "before-after.json").read_text())
    assert "MOCK" in md and "STUB WORLD" in md and data["labels"] == ["MOCK", "STUB WORLD"]
    for col in ("Verified", "First candidate", "Calls/day", "Tokens/day", "Checks/day", "Exec pass"):
        assert col in md
    assert "HELPED" in res["verdict"] or "DID NOT HELP" in res["verdict"]
    with pytest.raises(ValueError):
        evolve.before_after(root, env, llm, scenarios=["dev-clear-full"])


def test_before_after_without_kept_candidate_reports_no_change(tmp_path, env, llm):
    res = evolve.before_after(tmp_path / "playbook", env, llm, scenarios=["ho-clear-low-battery"])
    assert "before equals after" in res["verdict"]


def test_register_adds_subcommands():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    evolve.register(sub)
    a = p.parse_args(["evolve", "--rounds", "3", "--mode", "mock"])
    assert a.rounds == 3 and a.fn is evolve.cmd_evolve
    assert p.parse_args(["playbook", "history"]).action == "history"
    assert p.parse_args(["playbook", "before-after", "--mode", "mock"]).fn is evolve.cmd_playbook


def test_cli_commands_run_in_tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("GREENSTOVE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GREENSTOVE_LLM", "mock")
    p = argparse.ArgumentParser()
    evolve.register(p.add_subparsers(dest="cmd", required=True))
    for argv in (["evolve", "--rounds", "1", "--mode", "mock", "--stub"], ["playbook", "history"],
                 ["playbook", "show"], ["playbook", "before-after", "--mode", "mock", "--stub"]):
        a = p.parse_args(argv)
        assert a.fn(a) == 0
    assert (tmp_path / "data" / "playbook" / "before-after.json").exists()
    assert not (tmp_path / "data" / "brain").exists() and not (tmp_path / "data" / "evidence").exists()
