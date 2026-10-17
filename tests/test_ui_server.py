"""Local UI server: every endpoint over real HTTP on 127.0.0.1, mock model, stub world, tmp data dir."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from greenstove import ui_server


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv("GREENSTOVE_WORLD", "stub")
    mp.setenv("GREENSTOVE_LLM", "mock")
    mp.setattr("greenstove.demo.guardrail_status", lambda: "not in a sandbox here (test)")
    srv = ui_server.make_server(0, tmp_path_factory.mktemp("ui"), "mock")
    assert srv.server_address[0] == "127.0.0.1"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()
    mp.undo()


def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method="POST" if body is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_state_scenario(base):
    with urllib.request.urlopen(base + "/", timeout=10) as r:
        assert r.status == 200 and "html" in r.headers["Content-Type"]
    s, st = call(base, "/api/state")
    assert s == 200 and st["mode"] == "mock" and st["memory_modes"] == ["off", "history", "curated"]
    names = {x["name"] for x in st["scenarios"]}
    assert {"dev-clear-full", "demo-handoff-overcast", "ho-clear-rigid"} <= names
    assert "entries" in st["brain"]
    s, sc = call(base, "/api/scenario", {"name": "dev-clear-full"})
    assert s == 200 and len(sc["pv"]["expected"]) == 96 and len(sc["households"]) == 8
    assert [w["name"] for w in sc["windows"]] == ["standard", "sun", "peak"] and sc["windows"][2]["end"] == "24:00"
    assert sc["pv_kwh"]["downside"] < sc["pv_kwh"]["expected"]
    assert call(base, "/api/scenario", {"name": "nope"})[0] == 404


def test_household_update_and_ask(base):
    s, hh = call(base, "/api/household/update", {"scenario": "dev-partly-cloudy-mid", "household_id": "hh-04",
                                                 "preferred": "17:00", "max_cost": 9.5})
    assert s == 200 and hh["preferred"] == "17:00" and hh["max_cost"] == 9.5
    _, sc = call(base, "/api/scenario", {"name": "dev-partly-cloudy-mid"})
    assert next(h for h in sc["households"] if h["household_id"] == "hh-04")["preferred"] == "17:00"
    for bad in ({"preferred": "18:07"}, {"preferred": "01:00"}, {"minutes": 20}, {"max_cost": "x"}):
        s, err = call(base, "/api/household/update", {"scenario": "dev-partly-cloudy-mid", "household_id": "hh-04",
                                                      **bad})
        assert s == 400 and err["error"], bad
    s, ask = call(base, "/api/household/ask", {"scenario": "dev-partly-cloudy-mid", "household_id": "hh-04"})
    assert s == 200 and ask["prices"] == {"standard": 8.0, "sun": 4.0, "peak": 13.0}     # mid lattice
    assert 1 <= len(ask["options"]) <= 5 and ask["message"].startswith(("I'd cook", "Nothing"))
    scores = [o["score"] for o in ask["options"]]
    assert scores == sorted(scores)
    assert ask["choice"] is None or ask["choice"]["within_budget"]
    s, ask2 = call(base, "/api/household/ask", {"scenario": "dev-partly-cloudy-mid", "household_id": "hh-04",
                                                "prices": {"standard": 6, "sun": 3, "peak": 16}})
    assert s == 200 and "sun price is lower" in ask2["message"]
    assert call(base, "/api/household/ask", {"scenario": "dev-partly-cloudy-mid", "household_id": "hh-04",
                                             "prices": {"sun": 3}})[0] == 400


def test_run_brain_evidence_note(base):
    s, out = call(base, "/api/run", {"scenario": "dev-clear-full", "memory_mode": "curated"})
    assert s == 200, out
    assert out["run"]["status"] in ("PLAN_VERIFIED", "NO_VERIFIED_PLAN", "INFEASIBLE")
    assert len(out["messages"]) == 8 and len({m["household_id"] for m in out["messages"]}) == 8
    assert out["run"]["status"] != "PLAN_VERIFIED" or isinstance(out["chart_svg"], str)
    assert isinstance(out["chart_svg"], str) and out["chart_svg"].startswith("<svg")
    assert out["spec"]["checks"] and out["run"]["attempts"] and out["run"]["brief"]["mode"] == "curated"
    assert call(base, "/api/run", {"scenario": "dev-clear-full", "memory_mode": "nope"})[0] == 400
    s, brain = call(base, "/api/brain")
    assert s == 200 and "stats" in brain and isinstance(brain["entries"], list)
    s, ev = call(base, "/api/evidence")
    assert s == 200 and ev and {"run_id", "passed", "served_fraction"} <= set(ev[0])
    s, note = call(base, "/api/brain/note", {"evidence_ids": [ev[0]["run_id"]],
                                            "description": "Served 77.7 percent and relaxed the reserve", "tags": []})
    assert s == 200 and note["action"] == "rejected" and note["code"]
    assert call(base, "/api/brain/explain?id=obs-999")[0] == 404
    if brain["entries"]:
        s, ex = call(base, f"/api/brain/explain?id={brain['entries'][0]['id']}")
        assert s == 200 and ex["entry"]["id"] == brain["entries"][0]["id"]


def test_tools_forecast_and_forecast_run(base):
    s, tools = call(base, "/api/tools")
    assert s == 200 and tools["crusoe"]["mode"] == "mock" and tools["youcom"]["search"] == "mock"
    assert isinstance(tools["crusoe"]["key_present"], bool) and "api_key" not in json.dumps(tools)
    assert tools["nemoclaw"]["kit"] == "deploy/nemoclaw/README.md"
    s, err = call(base, "/api/crusoe/models")
    assert s == 409 and "mock mode" in err["error"]
    s, fc = call(base, "/api/forecast", {"place": "Nairobi", "lat": -1.29, "lon": 36.82, "date": "2026-10-18"})
    assert s == 200, fc
    assert fc["search_mode"] == "mock" and fc["pv_kwh"] > 0 and fc["chart_svg"].startswith("<svg")
    assert call(base, "/api/forecast", {"place": "X", "lat": "a", "lon": 1, "date": "2026-10-18"})[0] == 400
    s, out = call(base, "/api/run", {"scenario": "demo-handoff-overcast", "memory_mode": "off", "use_forecast": True})
    assert s == 200, out
    assert out["forecast"]["place"] == "Nairobi" and len(out["messages"]) == 8


def test_runner_event_drain_status(base):
    s, ev = call(base, "/api/runner/event", {"type": "day_start", "date": "2026-10-18",
                                             "scenario": "demo-handoff-overcast", "condition": "C"})
    assert s == 200 and ev["id"]
    s, _ = call(base, "/api/runner/event", {"type": "telemetry", "date": "2026-10-18", "step": 70,
                                            "battery_kwh": 0.5, "pv_kw": 0.0})
    assert s == 200
    assert call(base, "/api/runner/event", {"type": "telemetry", "date": "2026-10-18", "step": 999,
                                            "battery_kwh": 1, "pv_kw": 0})[0] == 400
    s, dr = call(base, "/api/runner/drain", {})
    assert s == 200 and [h["type"] for h in dr["handled"]] == ["day_start", "telemetry"]
    s, st = call(base, "/api/runner/status")
    assert s == 200 and st["processed"] == 2 and "2026-10-18" in st["days"]


def test_reset_last(base):
    _, before = call(base, "/api/state")
    s, out = call(base, "/api/reset", {})
    assert s == 200 and out["session"] != before["session"]
    _, ev = call(base, "/api/evidence")
    assert ev == []
    _, sc = call(base, "/api/scenario", {"name": "dev-partly-cloudy-mid"})
    assert next(h for h in sc["households"] if h["household_id"] == "hh-04")["max_cost"] != 9.5
