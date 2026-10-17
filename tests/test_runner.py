"""Event-driven runner: mock LLM, stub world, tmp data dir. No network, no live calls."""
import json
from datetime import date

import pytest

from greenstove import cli, runner
from greenstove.brain.store import BrainStore
from greenstove.evidence import EvidenceStore
from greenstove.forecast import pipeline
from greenstove.llm import ROOT
from greenstove.scenarios import DEFAULT_REQUIREMENTS
from greenstove.stub_env import StubEnvironment

D = "2026-10-18"


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setenv("GREENSTOVE_DATA_DIR", str(tmp_path))      # ROOT / absolute path == absolute path
    monkeypatch.setenv("GREENSTOVE_LLM", "mock")
    assert cli.data_dir() == tmp_path
    return runner.RunnerContext(data_dir=tmp_path, env=StubEnvironment(), llm=cli.make_llm("mock"),
                                evidence=EvidenceStore(tmp_path / "evidence"), brain=BrainStore(tmp_path / "brain"),
                                requirements=DEFAULT_REQUIREMENTS, max_calls_per_day=20)


def log(ctx):
    return [json.loads(x) for x in ctx.paths.log.read_text().splitlines()]


def forecast_file(tmp_path, name="fc.json", **update):
    rec = pipeline.build_forecast("Asmara, Eritrea", 15.3229, 38.9251, date(2026, 10, 18), search_mode="mock",
                                  llm=cli.make_llm("mock"))
    if "scale" in update:
        s = update.pop("scale")
        update.update(downside_pv_kw=[round(x * s, 4) for x in rec.downside_pv_kw],
                      downside_pv_kwh=round(rec.downside_pv_kwh * s, 3))
    rec = rec.model_copy(update=update)
    path = tmp_path / name
    path.write_text(rec.model_dump_json())
    return path


def test_day_start_writes_decision_and_log_line(ctx):
    runner.emit({"type": "day_start", "id": "ds-1", "date": D, "scenario": "demo-handoff-overcast"}, ctx.data_dir)
    s = runner.drain(ctx)
    rows = log(ctx)
    assert s["handled"] == 1 and len(rows) == 1
    row = rows[0]
    assert row["id"] == "ds-1" and row["type"] == "day_start" and row["outcome"].startswith("planned")
    assert row["model_calls"] > 0 and row["started_at"] and row["finished_at"]
    decision = ROOT / row["outputs"][0] if not row["outputs"][0].startswith("/") else None
    decision = decision or __import__("pathlib").Path(row["outputs"][0])
    assert decision.name == "decision.md" and decision.read_text().startswith("# Day decision")
    st = runner.load_state(ctx.paths)
    assert st["calls_per_day"][D] == row["model_calls"] and st["days"][D]["run_id"] == row["detail"]["run_id"]
    assert st["processed"] == ["ds-1"]


def test_duplicate_event_id_is_ignored(ctx):
    for _ in range(2):
        runner.emit({"type": "day_start", "id": "ds-dup", "date": D}, ctx.data_dir)
    runner.drain(ctx)
    rows = log(ctx)
    assert [r["outcome"].split(":")[0] for r in rows] == ["planned", "ignored"]
    assert rows[1]["model_calls"] == 0
    runner.drain(ctx)                                   # nothing new: no extra log lines, no calls
    assert len(log(ctx)) == 2


def test_forecast_update_within_tolerance_costs_zero_calls(ctx, tmp_path):
    fc = forecast_file(tmp_path)
    runner.emit({"type": "day_start", "id": "ds-fc", "date": D, "forecast_path": str(fc)}, ctx.data_dir)
    runner.drain(ctx)
    calls_before = runner.load_state(ctx.paths)["calls_per_day"][D]
    near = forecast_file(tmp_path, "fc-near.json", scale=1.05)
    runner.emit({"type": "forecast_update", "id": "fu-1", "date": D, "forecast_path": str(near)}, ctx.data_dir)
    runner.drain(ctx)
    row = log(ctx)[-1]
    assert row["outcome"] == "skipped: forecast within tolerance" and row["model_calls"] == 0
    assert runner.load_state(ctx.paths)["calls_per_day"][D] == calls_before


def test_forecast_update_material_change_replans(ctx, tmp_path):
    fc = forecast_file(tmp_path)
    runner.emit({"type": "day_start", "id": "ds-a", "date": D, "forecast_path": str(fc)}, ctx.data_dir)
    runner.drain(ctx)
    first = runner.load_state(ctx.paths)["days"][D]["run_id"]
    worse = forecast_file(tmp_path, "fc-worse.json", scale=0.8)
    runner.emit({"type": "forecast_update", "id": "fu-2", "date": D, "forecast_path": str(worse)}, ctx.data_dir)
    runner.drain(ctx)
    row = log(ctx)[-1]
    assert row["outcome"].startswith("replanned") and "downside PV" in row["detail"]["why"]
    day = runner.load_state(ctx.paths)["days"][D]
    assert day["plans"] == 2 and day["run_id"] != first and day["forecast_path"].endswith("fc-worse.json")
    # a 50% drop is re-planned too (the energy bound may prove INFEASIBLE with zero model calls)
    worst = forecast_file(tmp_path, "fc-worst.json", scale=0.5)
    runner.emit({"type": "forecast_update", "id": "fu-3", "date": D, "forecast_path": str(worst)}, ctx.data_dir)
    runner.drain(ctx)
    row = log(ctx)[-1]
    assert row["outcome"].startswith("replanned") and row["model_calls"] >= 0


def test_telemetry_far_below_plan_alerts_without_model_call(ctx):
    runner.emit({"type": "day_start", "id": "ds-t", "date": D}, ctx.data_dir)
    runner.drain(ctx)
    day = runner.load_state(ctx.paths)["days"][D]
    calls = runner.load_state(ctx.paths)["calls_per_day"][D]
    assert day["expected_battery_kwh"], "the stub day should have a verified plan with an expected trace"
    expected = day["expected_battery_kwh"][72]
    runner.emit({"type": "telemetry", "id": "tm-ok", "date": D, "step": 72, "battery_kwh": expected,
                 "pv_kw": 0.2}, ctx.data_dir)
    runner.emit({"type": "telemetry", "id": "tm-low", "date": D, "step": 72,
                 "battery_kwh": max(0.0, expected - 2.0), "pv_kw": 0.0}, ctx.data_dir)
    runner.drain(ctx)
    ok, low = log(ctx)[-2:]
    assert ok["outcome"].startswith("ok") and ok["model_calls"] == 0
    assert low["outcome"].startswith("alert") and low["model_calls"] == 0
    alerts = [json.loads(x) for x in ctx.paths.alerts.read_text().splitlines()]
    assert len(alerts) == 1 and alerts[0]["human_ping"] is True and alerts[0]["event_id"] == "tm-low"
    assert alerts[0]["deficit_kwh"] > runner.BATTERY_TOLERANCE_KWH
    assert runner.load_state(ctx.paths)["calls_per_day"][D] == calls     # no re-plan


def test_budget_exhausted_is_logged_and_skipped(ctx):
    runner.emit({"type": "day_start", "id": "ds-b1", "date": D}, ctx.data_dir)
    runner.drain(ctx)
    ctx.max_calls_per_day = runner.load_state(ctx.paths)["calls_per_day"][D]
    runner.emit({"type": "day_start", "id": "ds-b2", "date": D}, ctx.data_dir)
    runner.drain(ctx)
    row = log(ctx)[-1]
    assert row["outcome"].startswith("skipped: daily model-call budget exhausted") and row["model_calls"] == 0


def test_run_is_capped_by_remaining_budget(ctx):
    ctx.max_calls_per_day = 1
    runner.emit({"type": "day_start", "id": "ds-cap", "date": D}, ctx.data_dir)
    runner.drain(ctx)
    assert log(ctx)[-1]["model_calls"] <= 1
    assert ctx.llm.s.max_calls == 8                     # per-run cap restored afterwards


def test_malformed_lines_rejected_and_skipped(ctx):
    ctx.paths.dir.mkdir(parents=True, exist_ok=True)
    with ctx.paths.inbox.open("a") as f:
        f.write("{not json\n")
        f.write(json.dumps({"type": "telemetry", "id": "bad-1", "date": D, "step": 500, "battery_kwh": 1,
                            "pv_kw": 0}) + "\n")
        f.write(json.dumps({"type": "day_start", "id": "bad-2", "date": D, "surprise": 1}) + "\n")
        f.write(json.dumps({"type": "telemetry", "id": "good-1", "date": D, "step": 10, "battery_kwh": 3.0,
                            "pv_kw": 0.0}) + "\n")
    runner.drain(ctx)
    rows = log(ctx)
    assert [r["outcome"].split(":")[0] for r in rows] == ["rejected", "rejected", "rejected", "ok"]
    assert all(r["model_calls"] == 0 for r in rows)
    runner.drain(ctx)
    assert len(log(ctx)) == 4                           # rejected lines are not re-read


def test_watch_idles_without_calls_and_stops_on_shutdown(ctx):
    polls = []
    t = runner.watch(ctx, interval=0, max_polls=3, sleep=lambda s: polls.append(s))
    assert t["model_calls"] == 0 and t["drains"] == 0 and len(polls) == 3        # empty inbox: no work at all
    runner.emit({"type": "telemetry", "id": "w-1", "date": D, "step": 5, "battery_kwh": 3, "pv_kw": 0}, ctx.data_dir)
    runner.emit({"type": "shutdown", "id": "w-stop"}, ctx.data_dir)
    runner.emit({"type": "telemetry", "id": "w-after", "date": D, "step": 6, "battery_kwh": 3, "pv_kw": 0},
                ctx.data_dir)
    t = runner.watch(ctx, interval=0, max_polls=10, sleep=lambda s: None)
    assert t["stopped"] and t["handled"] == 2 and t["model_calls"] == 0
    assert "w-after" not in runner.load_state(ctx.paths)["processed"]           # left for the next start


def test_cli_register_parses_and_queues(ctx):
    import argparse
    p = argparse.ArgumentParser()
    runner.register(p.add_subparsers(dest="cmd", required=True))
    a = p.parse_args(["event", "telemetry", "--date", D, "--step", "72", "--battery-kwh", "1.1", "--pv-kw", "0.2"])
    assert a.fn(a) == 0
    a = p.parse_args(["event", "day_start", "--date", D, "--scenario", "demo-handoff-overcast", "--condition", "C"])
    assert a.fn(a) == 0
    a = p.parse_args(["runner", "--once", "--stub"])
    assert a.fn(a) == 0
    a = p.parse_args(["runner", "status"])
    assert a.fn(a) == 0
    st = runner.status(ctx.data_dir)
    assert st["processed"] == 2 and D in st["calls_per_day"]
    assert p.parse_args(["runner", "--watch", "--interval", "5"]).interval == 5
