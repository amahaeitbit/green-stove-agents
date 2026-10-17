"""Results report (`gsa report`): sections, honest MOCK/stub labels, no absolute paths, no secrets."""
import argparse
import json
import re

from greenstove import results
from greenstove.llm import ROOT

USAGE = ('{"role":"pricing","llm_mode":"mock","model":"mock-rules","prompt_hash":"c54ed46f7e6cd980",'
         '"memory_hash":"4f53cda18c2baa0c","settings":{"model":"mock-rules","temperature":0.2,"max_tokens":1024,'
         '"thinking":false,"structured":"auto"},"attempt":1,"prompt_tokens":997,"completion_tokens":39,'
         '"latency_ms":0,"request_id":null,"outcome":"ok","error":null,"cached_tokens":0,"reasoning_tokens":0,'
         '"cost_usd":null,"response_format":null,"ratelimit_remaining_requests":null,"ratelimit_remaining_tokens":null}')

ABC = """# Partner-side A/B/C summary

Model: **{model}** · world: **{world}** · commit abc1234 · brain snapshot f358cab9 · 2 held-out days

| Condition | Verified | First candidate | Exec pass | Calls/day | Tokens/day | Cached % | USD/day | Candidate checks/day | Simulator runs/day | Mean served (actual) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A facts only | 2/2 | 0/2 | 2/2 | 3.00 | 3622 | 0 | n/a | 3.00 | 7.00 | 0.75 |
| B raw history | 2/2 | 1/2 | 2/2 | 2.50 | 5150 | 0 | n/a | 3.00 | 7.00 | 0.75 |
| C curated brain | 2/2 | 2/2 | 2/2 | 2.00 | 5378 | 0 | n/a | 3.00 | 7.00 | 0.75 |
"""


def make_e2e(base, *, live=False, world="stub-env (development stand-in, not Abel's simulator)", compare=False):
    root = base / "e2e-20261017-120000"
    for d in ("spec", "eval", "playbook", "tracks", "forecast", "crusoe", "brain", "evidence", "freeze"):
        (root / d).mkdir(parents=True, exist_ok=True)
    model = "LIVE Crusoe" if live else "MOCK (rule-based stand-in)"
    (root / "e2e-summary.md").write_text(
        f"# End-to-end run 20261017-120000\n\nModel: {model} · world: {world} · forecast day 2026-10-18\n\n"
        "World coverage: 40 simulations checked, 0 phases the world could not model\n\n"
        "| Step | Status | Shows |\n| --- | --- | --- |\n| smoke | ok | one structured Crusoe call recorded |\n"
        "| spec | ok | Done Means Done |\n| tracks | skipped/warn (exit 1) | scorecard |\n\n"
        "CRUSOE_API_KEY=sk-should-never-appear\n")
    (root / "spec" / "spec-1.json").write_text(json.dumps({
        "goal": "Every household cooks on electricity.", "goal_restated": "Book every request.",
        "checks": [{"metric": "served_fraction", "op": ">=", "threshold": 0.75, "why": "'every household'",
                    "source": "code_tightened"},
                   {"metric": "unserved_essential_kwh", "op": "<=", "threshold": 0.0, "why": "committee requirement",
                    "source": "code_added"}], "dropped": [], "model": "mock-rules"}))
    llm = {"mode": "crusoe" if live else "mock", "model": "nvidia/Nemotron-3-Super-120B-A12B" if live else "mock-rules",
           "models_by_role": {"pricing": "nvidia/Nemotron-3-Super-120B-A12B"}}
    (root / "eval" / "manifest-partner.json").write_text(json.dumps({"partner": {"commit": "abc1234", "llm": llm,
                                                                                 "world": world}}))
    (root / "eval" / "agent-summary.md").write_text(ABC.format(model=llm["model"], world=world))
    (root / "playbook" / "before-after.md").write_text(
        "# Version N+1 before/after\n\nCondition A; 8 held-out days never used for tuning.\n\n"
        "| Version | Verified | First candidate | Calls/day |\n| --- | --- | --- | --- |\n"
        "| before v001 (human-seed) | 6/8 | 4/8 | 2.00 |\n| after v002 (reviewer-agent) | 6/8 | 6/8 | 1.50 |\n\n"
        "Held-out check under the same keep rule: HELPED (first-candidate pass 4/8 -> 6/8, verified 6/8 held).\n")
    (root / "playbook" / "index.json").write_text(json.dumps({"active": "v002", "versions": [
        {"id": "v001", "author": "human-seed", "status": "retired", "reason": "human seed"},
        {"id": "v002", "author": "reviewer-agent", "status": "active", "reason": "kept: first-candidate 3/5 -> 4/5"},
        {"id": "v003", "author": "reviewer-agent", "status": "rejected", "reason": "rejected: no measurable gain"}]}))
    (root / "playbook" / "evolve-log.jsonl").write_text(
        json.dumps({"round": 1, "decision": "kept", "candidate": "v002", "reason": "kept: 3/5 -> 4/5"}) + "\n"
        + json.dumps({"round": 2, "decision": "rejected", "candidate": "v003", "reason": "rejected: no gain"}) + "\n")
    (root / "tracks" / "tracks-scorecard.json").write_text(json.dumps({
        "header": "MOCK model · STUB world", "work_dir": str(root / "tracks" / "work"), "passed": 2, "total": 3,
        "rows": [{"id": "1", "track": "Done Means Done", "requirement": "spec", "result": "PASS",
                  "evidence": f"decision.md at {root}/tracks/work/decision.md"},
                 {"id": "8", "track": "Shared Brain", "requirement": "reads", "result": "PASS", "evidence": ""},
                 {"id": "16", "track": "Version N+1", "requirement": "keeps", "result": "FAIL", "evidence": ""}]}))
    (root / "forecast" / "asmara-eritrea-2026-10-18-mock.json").write_text(json.dumps({
        "place": "Asmara, Eritrea", "date": "2026-10-18", "kwp": 3.0, "weather_class": "rainy_afternoon",
        "status": "VERIFIED", "search_mode": "mock", "extractor_mode": "mock", "sources": [{"url": "x"}],
        "forecast_pv_kw": [1.0] * 48 + [0.0] * 48, "downside_pv_kw": [0.5] * 48 + [0.0] * 48}))
    (root / "crusoe" / "bench-1.json").write_text(json.dumps([{"model": "nvidia/Nemotron-3-Super-120B-A12B",
                                                               "valid": "2/2", "calls": 2}]))
    (root / "freeze" / "freeze.json").write_text(json.dumps({"brain_snapshot": "f358cab97936bfc5", "brain_entries": 0,
                                                             "history_records": 4, "commit": "abc1234"}))
    (root / "usage.jsonl").write_text(USAGE + "\n")
    if compare:
        (root / "compare").mkdir()
        (root / "compare" / "results-table.md").write_text(
            "# Six conditions\n\n| Condition | Score |\n| --- | --- |\n| A | 1 |\n| D reuse | 2 |\n| F search | 3 |\n")
    return root


def test_mock_report_sections_labels_and_hygiene(tmp_path):
    root = make_e2e(tmp_path)
    text = results.build_report(root)
    for heading in ("# Green-Stove Agents: results", "## 1. Run status", "## 2. Definition of done",
                    "## 3. Headline comparison", "## 4. Version N+1", "## 5. Shared Brain health",
                    "## 6. Track scorecard", "## 7. Crusoe usage", "## 8. What this does not prove"):
        assert heading in text, heading
    assert "**MOCK model · STUB world: these numbers test the mechanisms, not model quality.**" in text
    assert "World coverage: 40 simulations checked" in text and "2/3 steps ok" in text
    assert "| served_fraction | >= 0.75 | code_tightened |" in text
    assert "| C curated brain | 2/2 | 2/2 |" in text and "six-condition table A–F is not in this run" in text
    assert "first-candidate pass 2/2 vs 0/2" in text
    assert "HELPED" in text and "1 kept, 1 rejected" in text and "| v003 | reviewer-agent | rejected |" in text
    assert "**2/3 requirement rows PASS**" in text and "not passing: row 16" in text
    assert "| mock | pricing | mock-rules | 1 |" in text
    assert "class **rainy_afternoon**, PV 12.0 kWh expected / 6.0 kWh downside" in text
    assert "search mode mock" in text and "MOCK search" in text
    assert "**Mock model.**" in text and "**Stub world.**" in text and "2 held-out days per condition and 8" in text
    assert "API_KEY" not in text and "sk-should-never-appear" not in text
    assert str(tmp_path) not in text and str(ROOT) not in text
    assert not re.search(r"(?<![\w.:/])/(Users|home|private|tmp|var)/", text), text


def test_live_report_has_no_mock_label_and_uses_compare_table(tmp_path):
    root = make_e2e(tmp_path, live=True, world="abel-admission", compare=True)
    text = results.build_report(root)
    assert "**LIVE Crusoe**" in text and "nvidia/Nemotron-3-Super-120B-A12B" in text
    assert results.MOCK_LABEL not in text and "**Mock model.**" not in text and "**Stub world.**" not in text
    assert "Six conditions A–F" in text and "| F search | 3 |" in text and "### Six conditions" in text
    assert "| A facts only |" not in text                     # compare table replaces the A/B/C fallback


def test_register_writes_report_and_defaults_to_latest(tmp_path, monkeypatch):
    make_e2e(tmp_path)
    monkeypatch.setenv("GREENSTOVE_DATA_DIR", str(tmp_path))       # ROOT / absolute == absolute
    assert results.latest_e2e().name == "e2e-20261017-120000"
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    results.register(sub)
    out = tmp_path / "out" / "results.md"
    a = p.parse_args(["report", "--out", str(out)])
    assert a.fn(a) == 0 and out.read_text().startswith("# Green-Stove Agents: results")
    a = p.parse_args(["report", "--e2e", str(tmp_path / "missing"), "--out", str(out)])
    assert a.fn(a) == 1


def test_sanitize_strips_absolute_paths_and_key_lines():
    text = results.sanitize(f"see {ROOT}/data/e2e-1/x.md and /Users/someone/elsewhere/y.md\nYDC_API_KEY=abc\nok\n")
    assert text == "see data/e2e-1/x.md and elsewhere/y.md\nok\n"
