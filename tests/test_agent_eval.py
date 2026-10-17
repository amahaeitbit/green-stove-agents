"""End-to-end path on the stub world in mock mode: warm-up -> freeze -> held-out A/B/C."""
from greenstove import agent_eval, cli, supervisor
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import load_environment
from greenstove.evidence import EvidenceStore
from greenstove.scenarios import DEFAULT_REQUIREMENTS, DEV_SCENARIOS, HELDOUT_SCENARIOS, actual_for, scenario


def test_freeze_then_conditions_use_frozen_memory_only(tmp_path):
    env, llm = load_environment(prefer_stub=True), cli.make_llm("mock")
    ev, brain = EvidenceStore(tmp_path / "ev"), BrainStore(tmp_path / "brain")
    for name in list(DEV_SCENARIOS)[:3]:
        supervisor.run_agent_day(scenario(name), "curated", DEFAULT_REQUIREMENTS, env=env, llm=llm,
                                 evidence=ev, brain=brain, actual=actual_for(name))
    info = agent_eval.freeze_memory(ev, brain, tmp_path / "freeze")
    assert info["brain_entries"] > 0 and info["history_records"] == len(ev.all())
    names = list(HELDOUT_SCENARIOS)[:2]
    runs = agent_eval.run_conditions(names, scenario, actual_for, DEFAULT_REQUIREMENTS, env=env, llm=llm,
                                     freeze_dir=tmp_path / "freeze", out=tmp_path / "eval", log=lambda s: None)
    assert len(runs) == 6 and {r.memory_mode for r in runs} == {"off", "history", "curated"}
    assert all(r.phase == "evaluation" and r.notes == [] for r in runs)
    frozen, _ = agent_eval.load_frozen(tmp_path / "freeze")
    assert frozen.snapshot_hash() == info["brain_snapshot"]          # evaluation never wrote memory
    lines = (tmp_path / "eval" / "agent-runs.jsonl").read_text().splitlines()
    assert len(lines) == 6 and all('"stale_served": 0' in x for x in lines)
    text = agent_eval.summarize(runs, tmp_path / "eval", {"model": "mock-rules (MOCK)", "world": env.name,
                                                          "commit": "x", "brain_snapshot": info["brain_snapshot"],
                                                          "days": 2})
    assert "Mock mode" in text and "Stub world" in text and "C curated brain" in text
