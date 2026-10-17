"""Non-LLM comparators D/E/F3/Ffull next to A/B/C (MOCK model, STUB world, tmp dirs)."""
import argparse
import json

import pytest

from greenstove import agent_eval, cli, comparators, supervisor
from greenstove.agent_models import EvidenceRecord, situation_of
from greenstove.brain.store import BrainStore
from greenstove.env_protocol import load_environment
from greenstove.evidence import EvidenceStore
from greenstove.scenarios import (DEFAULT_REQUIREMENTS, DEV_SCENARIOS, HELDOUT_SCENARIOS, actual_for, lattice_tariffs,
                                  scenario)
from greenstove.spec_agent import DEFAULT_GOAL, write_spec

HO = "ho-clear-low-battery"
GOOD = {"served_fraction": 1.0, "max_shift_hours": 0.0, "overload_steps": 0.0, "unserved_essential_kwh": 0.0,
        "unserved_cooking_kwh": 0.0}


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cmp")
    env, llm = load_environment(prefer_stub=True), cli.make_llm("mock")
    dod = write_spec(DEFAULT_GOAL, DEFAULT_REQUIREMENTS, llm)
    ev, brain = EvidenceStore(tmp / "ev"), BrainStore(tmp / "brain")
    for name in list(DEV_SCENARIOS)[:3]:                       # development warm-up
        supervisor.run_agent_day(scenario(name), "curated", DEFAULT_REQUIREMENTS, env=env, llm=llm, evidence=ev,
                                 brain=brain, actual=actual_for(name), phase="development", dod=dod)
    agent_eval.freeze_memory(ev, brain, tmp / "freeze")
    return tmp, env, llm, dod


def _rec(rid, *, sit, tariff=None, day=3, cfg=None, metrics=GOOD,
         phases=(("forecast_expected", True), ("forecast_downside", True))):
    cfg = cfg or scenario(HO).config_hash
    tariff = tariff or {"standard": 8.0, "sun": 4.0, "peak": 13.0}
    return [EvidenceRecord(run_id=f"{rid}-{ph}", agent_run_id=rid, scenario_id=f"dev-{rid}", config_hash=cfg,
                           phase=ph, day=day, situation=sit, tariff=tariff, proposal_id=f"{rid}-p1", passed=ok,
                           metrics=dict(metrics), feedback_codes=[]) for ph, ok in phases]


def test_compare_writes_seven_conditions(frozen):
    tmp, env, llm, dod = frozen
    names = list(HELDOUT_SCENARIOS)[:2]
    text = comparators.compare(tmp / "freeze", tmp / "out", 2, env, llm, dod, contexts=scenario, log=lambda s: None)
    rows = [json.loads(x) for x in (tmp / "out" / "six-conditions.jsonl").read_text().splitlines()]
    assert {r["condition"] for r in rows} == set(comparators.CONDITIONS) and len(rows) == 7 * 2
    for cond in comparators.CONDITIONS:
        assert comparators.LABEL[cond] in text
    det = [r for r in rows if r["condition"] in comparators.COMPARATORS]
    assert all(r["model_calls"] == 0 and r["tokens"] == 0 for r in det)
    by = {(r["condition"], r["scenario"]): r for r in rows}
    for n in names:
        assert by[("F3", n)]["candidate_checks"] <= 3
        assert by[("Ffull", n)]["candidate_checks"] >= by[("F3", n)]["candidate_checks"]
        for cond in ("D", "E"):
            assert by[(cond, n)]["candidate_checks"] <= 1
        for r in (by[(c, n)] for c in comparators.CONDITIONS):           # verified needs both forecasts + DoD
            if r["verified"]:
                assert r["record"].get("selected_tariff") and r["simulator_runs"] >= 2
    assert "MOCK" in text and "STUB world" in text and "## Interpretation" in text
    assert (tmp / "out" / "results-table.md").read_text() == text
    assert (tmp / "out" / "agent-runs.jsonl").exists()


def test_library_needs_both_forecasts_and_last_is_newest():
    sit = situation_of(scenario(HO))
    hist = (_rec("r-a", sit=sit, day=1) + _rec("r-b", sit=sit, day=2)
            + _rec("r-c", sit=sit, day=2, phases=(("forecast_expected", True), ("forecast_downside", False)))
            + _rec("r-d", sit=sit, day=2, phases=(("forecast_expected", True),)))
    lib = comparators.verified_library(hist)
    assert [e.run_id for e in lib] == ["r-a", "r-b"]
    dod = write_spec(DEFAULT_GOAL, DEFAULT_REQUIREMENTS, cli.make_llm("mock"))
    short = _rec("r-e", sit=sit, day=2, metrics={**GOOD, "served_fraction": 0.5})   # world pass, DoD fail
    assert [e.run_id for e in comparators.verified_library(hist + short, dod)] == ["r-a", "r-b"]
    last, rejected = comparators.choose_last(lib, scenario(HO))
    assert last.run_id == "r-b" and rejected == 0


def test_distance_is_fixed_and_normalized():
    sit = situation_of(scenario(HO))
    assert comparators.distance(sit, sit) == 0
    assert comparators.distance(sit, {**sit, "weather_class": "rainy_afternoon"}) == pytest.approx(1.0)
    assert comparators.distance(sit, {**sit, "battery_frac": sit["battery_frac"] + 0.3}) == pytest.approx(1.0)
    assert comparators.distance(sit, {**sit, "flex_hours": sit["flex_hours"] + 3}) == pytest.approx(1.0)
    assert comparators.distance(sit, {**sit, "demand_kwh": sit["demand_kwh"] * 1.3}) == pytest.approx(1.0)
    assert comparators.distance(sit, {**sit, "forecast_pv_kwh": sit["forecast_pv_kwh"] * 0.7}) == pytest.approx(1.0)


def test_nearest_compatible_tariff_wins():
    ctx = scenario(HO)
    sit = situation_of(ctx)
    hist = (_rec("r-far", sit={**sit, "weather_class": "overcast", "battery_frac": sit["battery_frac"] + 0.3})
            + _rec("r-near", sit={**sit, "battery_frac": sit["battery_frac"] + 0.03}, tariff={"standard": 6.0, "sun": 3.0, "peak": 10.0})
            + _rec("r-other-config", sit=sit, cfg="other-station")                 # exact match, incompatible
            + _rec("r-future", sit=sit, day=ctx.day)                               # not before today
            + _rec("r-unverified", sit=sit, phases=(("forecast_expected", True), ("forecast_downside", False))))
    lib = comparators.verified_library(hist)
    best, dist, rejected = comparators.choose_nearest(lib, ctx)
    assert best.run_id == "r-near" and dist == pytest.approx(0.1) and rejected == 2
    assert best.tariff == {"standard": 6.0, "sun": 3.0, "peak": 10.0}
    tie = _rec("r-a-tie", sit={**sit, "battery_frac": sit["battery_frac"] - 0.03})   # same distance, lower run_id
    best, _, _ = comparators.choose_nearest(comparators.verified_library(hist + tie), ctx)
    assert best.run_id == "r-a-tie"


def test_incompatible_library_gives_e_no_candidate():
    env, llm = load_environment(prefer_stub=True), cli.make_llm("mock")
    dod = write_spec(DEFAULT_GOAL, DEFAULT_REQUIREMENTS, llm)
    sit = situation_of(scenario(HO))
    hist = _rec("r-x", sit=sit, cfg="other-station") + _rec("r-y", sit=sit, tariff={"sun": 4.0, "standard": 8.0})
    runs = comparators.run_comparators([HO], scenario, actual_for, DEFAULT_REQUIREMENTS, env=env, history=hist,
                                       dod=dod, log=lambda s: None)
    e = next(r for r in runs if r.condition == "E")
    assert e.status == "NO_VERIFIED_PLAN" and "no compatible candidate" in e.reason
    assert e.candidate_evaluations == 0 and e.simulator_runs == 0 and e.execution is None
    assert e.source["rejected_incompatible"] == 2
    d = next(r for r in runs if r.condition == "D")
    assert d.status == "NO_VERIFIED_PLAN" and d.candidate_evaluations == 0


def test_search_order_is_frozen_permutation_of_lattice():
    ctx = scenario(HO)
    order = comparators.search_order([], ctx)
    lat = lattice_tariffs(ctx)
    assert len(order) == len(lat) == 27 and sorted(map(str, order)) == sorted(map(str, lat))
    assert order[0] == {"standard": 6.0, "sun": 3.0, "peak": 16.0}         # structural tie-break, no dev data
    sit = situation_of(ctx)
    lib = comparators.verified_library(_rec("r-dev", sit=sit, tariff={"standard": 10.0, "sun": 5.0, "peak": 10.0}))
    assert comparators.search_order(lib, ctx)[0] == {"standard": 10.0, "sun": 5.0, "peak": 10.0}
    assert comparators.search_order(lib, ctx) == comparators.search_order(lib, ctx)


def test_register_adds_compare_subcommand():
    p = argparse.ArgumentParser()
    comparators.register(p.add_subparsers(dest="cmd"))
    a = p.parse_args(["compare", "--freeze", "x", "--days", "2", "--mode", "mock", "--stub"])
    assert a.fn is comparators.cmd_compare and a.days == 2 and a.stub and a.out is None
