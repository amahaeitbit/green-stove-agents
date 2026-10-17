"""Track conformance scorecard: every requirement row runs in mock mode on the stub world and passes."""
import argparse
import json

from greenstove import tracks


def test_scorecard_all_rows_pass(tmp_path):
    rows = tracks.run_scorecard(tmp_path, log=lambda s: None)
    assert {r["id"] for r in rows} == {str(i) for i in range(1, 18)}
    failed = [(r["id"], r["requirement"], r["evidence"]) for r in rows if r["result"] != "PASS"]
    assert not failed, failed
    assert {r["track"] for r in rows} == {"Done Means Done", "Shared Brain", "Version N+1"}
    md = (tmp_path / "tracks-scorecard.md").read_text()
    assert tracks.HEADER in md and "MOCK model" in md and "STUB world" in md
    data = json.loads((tmp_path / "tracks-scorecard.json").read_text())
    assert data["header"] == tracks.HEADER and data["all_pass"] and len(data["rows"]) == 17


def test_register_adds_tracks_command(tmp_path):
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    tracks.register(sub)
    a = p.parse_args(["tracks", "--out", str(tmp_path)])
    assert a.fn is tracks.cmd_tracks and a.out == str(tmp_path)
