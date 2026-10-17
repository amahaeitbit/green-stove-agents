"""Static HTML replay (`gsa replay`): structure, honesty, escaping, self-containment."""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path

import pytest

from greenstove import replay
from greenstove.demo import ACTS

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = Path("/Users/amaha/Documents/green-stove-agents/data/ui/demo/demo-mock-20261017-154531.json")


def _call(n: int) -> dict:
    return {"role": "pricing", "prompt_tokens": 100 * n, "completion_tokens": 10}


def _trace(phase: str) -> dict:
    return {"phase": phase, "simulator_version": "stub-env-3", "pv_kw": [0.0] * 30 + [2.0] * 36 + [0.0] * 30,
            "demand_kw": [0.3] * 96, "battery_kwh": [2.0] * 96, "overload_steps": []}


def _run(scenario: str, mode: str, n_calls: int, cited=None, status="PLAN_VERIFIED") -> dict:
    return {
        "scenario_id": scenario, "memory_mode": mode, "status": status, "reason": "verified at attempt 1",
        "brief": {"mode": mode, "entry_ids": ["obs-001"] if mode == "curated" else [], "text": "SHARED BRAIN obs-001",
                  "tokens": 40, "budget": 400},
        "attempts": [{"n": 1, "proposal": {"prices": {"sun": 3.0, "standard": 8.0, "peak": 13.0},
                                           "cited_observation_ids": cited or [], "rationale": "start <mid>"},
                      "validation_errors": [], "evidence_ids": ["e1", "e2"], "passed": True, "feedback": []}],
        "calls": [_call(i + 1) for i in range(n_calls)], "candidate_evaluations": 1, "simulator_runs": 3,
        "execution": {"status": "EXECUTION_COMPLETE", "outcome_passed": True},
        "decisions": [{"attempt": 1, "decision": "continue", "reason": "every check passed"}],
    }


def minimal_snapshot(off_calls: int = 3, cur_calls: int = 1) -> dict:
    ctx = {"scenario_id": "dev-overcast", "weather_class": "overcast", "inverter_kw": 3.0, "battery_capacity_kwh": 6.0,
           "initial_battery_kwh": 2.1, "reserve_kwh": 1.2, "forecast_pv_kw": [1.0] * 96, "downside_pv_kw": [0.5] * 96,
           "requests": [{"request_id": "req-hh-01", "household_id": "hh-01 <script>alert(1)</script>", "power_kw": 2.0,
                         "duration_steps": 2, "preferred_start": 48, "earliest_start": 46, "latest_finish": 54,
                         "max_cost": 8.0, "auto_book": True}],
           "windows": [{"name": "standard", "start_step": 0, "end_step": 40}, {"name": "sun", "start_step": 40, "end_step": 64},
                       {"name": "peak", "start_step": 64, "end_step": 96}],
           "lattice": {"standard": [6, 8, 10], "sun": [3, 4, 5], "peak": [10, 13, 16]},
           "assumptions": ["synthetic stand-in"]}
    first = _run("dev-overcast", "off", 2)
    first["attempts"].insert(0, {"n": 1, "proposal": {"prices": {"sun": 4.0, "standard": 8.0, "peak": 13.0},
                                                      "cited_observation_ids": [], "rationale": "x"},
                                 "evidence_ids": ["e0"], "passed": False, "feedback": ["BATTERY_RESERVE"]})
    first["decisions"].insert(0, {"attempt": 1, "decision": "re-plan", "reason": "failed BATTERY_RESERVE"})
    plan = {"tariff": {"sun": 3.0, "standard": 8.0, "peak": 13.0},
            "reservations": [{"request_id": "req-hh-01", "start_step": 48, "end_step": 50, "power_kw": 2.0,
                              "locked_cost": 3.0, "accepted": True}], "unserved": []}
    return {
        "meta": {"stamp": "20261017-000000", "mode": "mock", "model": "mock-rules",
                 "world": "stub-env (development stand-in, not Abel's simulator)", "goal": "Cook & stay safe"},
        "spec": {"goal": "Every household cooks <safely> & cheaply", "goal_restated": "Book it all",
                 "checks": [{"metric": "served_fraction", "op": ">=", "threshold": 0.75, "why": "cooks", "source": "code_tightened"},
                            {"metric": "overload_steps", "op": "<=", "threshold": 0.0, "why": "never", "source": "agent"},
                            {"metric": "unserved_essential_kwh", "op": "<=", "threshold": 0.0, "why": "committee",
                             "source": "code_added"}],
                 "dropped": [], "model": "mock-rules", "spec_hash": "6bf9dbe99f9b6563"},
        "world": {"context": ctx},
        "proposal": {"context": ctx, "run": first,
                     "traces": {"plan": plan, "forecast_expected": _trace("forecast_expected"),
                                "forecast_downside": _trace("forecast_downside"), "actual": _trace("actual")}},
        "memory": {"notes": [{"action": "added", "observation_id": "obs-001", "reasons": []}],
                   "fabricated": {"description": "served 0.99 with 42 kWh spare", "action": "rejected",
                                  "code": "result_mismatch", "reasons": ["unsupported_number:0.99"]},
                   "entries": [{"id": "obs-001", "situation": {"weather_class": "overcast", "battery_frac": 0.35},
                                "tariff": {"sun": 3.0}, "phase": "actual", "outcome_passed": True, "feedback_codes": [],
                                "description": "passed", "version": 1}],
                   "snapshot": "c6f8534f2f1ec089"},
        "handoff": {"context": ctx, "off": _run("handoff", "off", off_calls),
                    "curated": _run("handoff", "curated", cur_calls, cited=["obs-001"]), "traces": {}},
        "tomorrow": {"forecast": {"place": "Asmara, Eritrea", "date": "2026-10-18", "weather_class": "rainy_afternoon",
                                  "pv_kwh": 13.36, "downside_pv_kwh": 10.38, "status": "VERIFIED", "search_mode": "mock",
                                  "extractor_mode": "mock",
                                  "sources": [{"url": "https://example.invalid/mock", "title": "Mock forecast"}],
                                  "accepted": {"quote": "Cloud cover 55%.", "condition": "showers"},
                                  "forecast_pv_kw": [1.0] * 96, "downside_pv_kw": [0.7] * 96, "assumptions": ["Haurwitz"]},
                     "context": ctx, "run": _run("fc-asmara", "curated", 1), "traces": {}},
        "infeasible": {"context": ctx, "run": {"scenario_id": "fixture-infeasible-energy", "status": "INFEASIBLE",
                                               "reason": "bound exceeds available", "calls": [], "attempts": [],
                                               "infeasibility": {"lower_bound_kwh": 11.525, "available_kwh": 8.4708,
                                                                 "assumptions": ["optimistic efficiency"]},
                                               "decisions": [{"attempt": 0, "decision": "stop", "reason": "bound"},
                                                             {"attempt": 0, "decision": "ping_human", "reason": "committee"}]}},
        "improve": {"index": {"active": "v002", "versions": [
            {"id": "v001", "parent": None, "author": "human-seed", "status": "retired", "chars": 0, "reason": "seed"},
            {"id": "v002", "parent": "v001", "author": "reviewer-agent", "status": "active", "chars": 58, "reason": "kept"}]},
            "before_after_md": "# Version N+1\n\n| Version | Verified | First candidate |\n| --- | --- | --- |\n"
                               "| before v001 (human-seed) | 7/8 | 4/8 |\n| after v002 (reviewer-agent) | 7/8 | 6/8 |\n\n"
                               "Held-out check under the same keep rule: HELPED.\n\n```\n- when battery_frac < 0.45: start sun=3\n```\n"},
        "receipt": {"rows": [["Done Means Done", "writes its own definition of done", "3 checks; see /Users/someone/secret/x.json"]],
                    "calls": 9, "tokens": 11789, "cost_usd": 0},
    }


def _no_external_refs(page: str) -> None:
    refs = re.findall(r'(?:src|href)\s*=\s*["\']([^"\']*)["\']', page)
    assert all(r.startswith("#") for r in refs), refs
    assert "<script" not in page.lower() or "&lt;script" in page
    assert "<link" not in page.lower()
    assert "@import" not in page and "url(" not in page


def test_eight_acts_badges_and_receipt():
    page = replay.build_replay(minimal_snapshot())
    for n, title, question, _ in ACTS.values():
        assert f'id="act-{n}"' in page
        assert replay.esc(title) in page and replay.esc(question) in page
    assert "Green-Stove Agents · run replay" in page
    assert "MOCK model" in page and "stand-in world" in page and "STUB WORLD" in page
    assert "What the run proved" in page and "writes its own definition of done" in page
    for src in ("agent", "code_tightened", "code_added"):
        assert f'<span class="chip ' in page and f">{src}</span>" in page
    assert "re-plan" in page and "continue" in page and "ping_human" in page
    assert "accepted" in page and "11.525" in page and "8.471" in page
    assert "v002" in page and "6/8" in page and 'id="results"' not in page   # no e2e_root -> no results section


def test_live_and_abel_badges():
    snap = minimal_snapshot()
    snap["meta"].update(mode="crusoe", world="abel-admission (Abel's simulator)")
    page = replay.build_replay(snap)
    assert "LIVE model" in page and "Abel&#x27;s world" in page
    assert "STUB WORLD" not in page and "<b>MOCK:</b>" not in page


def test_escaping_self_contained_and_no_absolute_paths():
    page = replay.build_replay(minimal_snapshot())
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "&lt;safely&gt; &amp; cheaply" in page
    _no_external_refs(page)
    assert "/Users/" not in page and "/home/" not in page
    assert "https://example.invalid/mock" in page          # shown as text only, never as a link


@pytest.mark.parametrize("off,cur", [(3, 3), (1, 2)])
def test_takeaway_never_claims_a_saving_that_is_not_there(off, cur):
    page = replay.build_replay(minimal_snapshot(off_calls=off, cur_calls=cur))
    act5 = page.split('id="act-5"')[1].split("</section>")[0]
    assert "fewer" not in act5.lower()
    assert "no saving on this day" in act5
    assert "fewer" not in page.lower()


def test_takeaway_claims_saving_only_when_real():
    page = replay.build_replay(minimal_snapshot(off_calls=3, cur_calls=1))
    act5 = page.split('id="act-5"')[1].split("</section>")[0]
    assert "fewer model calls (3 → 1)" in act5 and "obs-001" in act5


def test_works_without_replay_charts(monkeypatch):
    monkeypatch.setitem(sys.modules, "greenstove.replay_charts", None)   # import -> ImportError
    assert replay._charts() is None
    page = replay.build_replay(minimal_snapshot())
    assert '<pre class="spark">' in page and "inverter 3 kW" in page
    for n, *_ in ACTS.values():
        assert f'id="act-{n}"' in page


def test_chart_failure_falls_back(monkeypatch):
    class Broken:
        @staticmethod
        def line_chart(*a, **k):
            raise TypeError("api drift")

        @staticmethod
        def bars(*a, **k):
            raise TypeError("api drift")

    monkeypatch.setitem(sys.modules, "greenstove.replay_charts", Broken)
    monkeypatch.setattr(replay, "_charts", lambda: Broken)
    page = replay.build_replay(minimal_snapshot())
    assert '<pre class="spark">' in page and 'class="hbar"' in page


def test_missing_sections_degrade_honestly():
    snap = minimal_snapshot()
    snap["tomorrow"] = None
    snap["improve"] = {}
    snap["handoff"] = {}
    page = replay.build_replay(snap)
    assert page.count("was not recorded in this run") >= 2
    assert "fewer" not in page.lower()


def test_e2e_results_section(tmp_path):
    root = tmp_path / "e2e-20261017-000000"
    (root / "compare").mkdir(parents=True)
    (root / "tracks").mkdir()
    (root / "compare" / "results-table.md").write_text(
        "# Six-condition results\n\n| Condition | Verified |\n| --- | --- |\n| A facts only | 7/8 |\n| C curated brain | 7/8 |\n")
    (root / "tracks" / "tracks-scorecard.json").write_text(json.dumps(
        {"header": "MOCK", "passed": 17, "total": 17, "work_dir": "/Users/x/work",
         "rows": [{"id": "1", "track": "Done Means Done", "requirement": "spec", "result": "PASS"}]}))
    (root / "e2e-summary.md").write_text("# run\n\nWorld coverage: 574 simulations checked, 0 phases\n")
    page = replay.build_replay(minimal_snapshot(), root)
    assert 'id="results"' in page and "<td>C curated brain</td>" in page and "17/17" in page
    assert "World coverage: 574 simulations checked" in page
    assert "/Users/" not in page and str(tmp_path) not in page


def test_md_table_rows_parses_and_drops_separator():
    rows = replay.md_table_rows("| a | b |\n| --- | :---: |\n| 1 | 2 |\ntext")
    assert rows == [["a", "b"], ["1", "2"]]


def test_cli_register_writes_page(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "demo").mkdir(parents=True)
    (data / "demo" / "demo-mock-20261017-000000.json").write_text(json.dumps(minimal_snapshot()))
    import greenstove.cli as cli
    monkeypatch.setattr(cli, "data_dir", lambda: data)
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    replay.register(sub)
    out = tmp_path / "r.html"
    a = p.parse_args(["replay", "--out", str(out), "--e2e", str(tmp_path / "missing")])
    assert a.fn(a) == 0 and out.exists() and 'id="act-8"' in out.read_text()
    a = p.parse_args(["replay"])
    assert a.fn(a) == 0
    assert (data / "replay" / "replay-20261017-000000.html").exists()


def test_cli_exits_1_without_snapshot(tmp_path, monkeypatch, capsys):
    import greenstove.cli as cli
    monkeypatch.setattr(cli, "data_dir", lambda: tmp_path)
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    replay.register(sub)
    a = p.parse_args(["replay"])
    assert a.fn(a) == 1
    assert "no demo snapshot" in capsys.readouterr().out


@pytest.mark.skipif(not SAMPLE.exists(), reason="real demo snapshot not present on this machine")
def test_real_sample_snapshot():
    snap = json.loads(SAMPLE.read_text())
    page = replay.build_replay(copy.deepcopy(snap))
    for n, *_ in ACTS.values():
        assert f'id="act-{n}"' in page
    _no_external_refs(page)
    assert "/Users/" not in page
    off, cur = len(snap["handoff"]["off"]["calls"]), len(snap["handoff"]["curated"]["calls"])
    act5 = page.split('id="act-5"')[1].split("</section>")[0]
    assert ("fewer" in act5) == (cur < off)


def test_tomorrow_takeaway_does_not_claim_live_services_in_mock():
    page = replay.build_replay(minimal_snapshot())
    act6 = page.split('id="act-6"')[1].split("</section>")[0]
    assert "You.com found" not in act6 and "Nemotron extracted" not in act6
    assert "mock search result stood in for You.com" in act6
    snap = minimal_snapshot()
    snap["tomorrow"]["forecast"].update(search_mode="live", extractor_mode="crusoe")
    act6 = replay.build_replay(snap).split('id="act-6"')[1].split("</section>")[0]
    assert "You.com found the forecast, Nemotron extracted it" in act6


def test_header_axis_is_html_not_escaped_text():
    page = replay.build_replay(minimal_snapshot())
    assert '<span class="axis">' in page and "&lt;span class=&quot;axis" not in page
