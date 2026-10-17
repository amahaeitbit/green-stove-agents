"""Note-writer: proposes scoped observations from this run's evidence.

It reads the evidence summaries and a relevant memory brief, proposes up to three
observations (evidence ids, tags, short description) and flags duplicates/conflicts.
It never approves memory: the Keeper reconstructs every fact from the evidence.
"""
from __future__ import annotations

from typing import Any

from greenstove.agent_models import Brief, CallRecord, EvidenceRecord, NoteWriterOutput, ObservationProposal
from greenstove.llm import LLMClient

SYSTEM = """You are the Note-writer for a shared memory used by pricing agents at a
simulated solar cooking station. From the evidence of one run, propose at most three
observations worth remembering: what tariff was tried, under what situation, and what
the verifier found. Rules: cite evidence ids exactly as given; group evidence of ONE
proposal per observation; use only numbers that appear in the evidence; do not propose
changing windows, prices lists, reserve, requirements or any rule; tags must describe
the situation or outcome (weather class, pass/fail, forecast/actual, feedback codes in
lowercase, low_battery/high_battery, high_demand/low_demand, flexible/rigid). If an
existing memory entry already says the same, set duplicate_of to its id; if it says the
opposite for the same situation and tariff, list it in conflicts_with.
Reply with ONLY a JSON object:
{"observations": [{"evidence_ids": [...], "situation_tags": [...], "description": "...",
  "duplicate_of": null, "conflicts_with": []}]}"""


def summarize(records: list[EvidenceRecord]) -> list[dict[str, Any]]:
    keep = ("served_fraction", "min_battery_kwh", "unserved_cooking_kwh", "unserved_essential_kwh",
            "overload_steps", "max_shift_hours", "total_spend")
    return [{
        "evidence_id": r.run_id, "proposal_id": r.proposal_id, "phase": r.phase, "passed": r.passed,
        "tariff": r.tariff, "feedback_codes": r.feedback_codes,
        "metrics": {k: r.metrics[k] for k in keep if k in r.metrics},
        "situation": r.situation,
    } for r in records]


def propose_observations(records: list[EvidenceRecord], brief: Brief, llm: LLMClient
                         ) -> tuple[list[ObservationProposal], list[CallRecord], str | None]:
    payload = {"evidence": summarize(records), "relevant_memory_brief": brief.text or "(none)"}
    out, calls, error = llm.complete_json("note_writer", SYSTEM, payload, NoteWriterOutput,
                                          memory_hash=brief.snapshot_hash)
    return (list(out.observations) if out else []), calls, error


# ---- deterministic mock --------------------------------------------------------------

def _tags(sit: dict[str, Any], passed: bool, phase: str, codes: list[str]) -> list[str]:
    tags = [str(sit.get("weather_class", "unknown")), "pass" if passed else "fail",
            "actual" if phase == "actual" else "forecast"]
    tags += [c.lower() for c in codes[:2]]
    frac = float(sit.get("battery_frac", 0.5))
    tags += ["low_battery"] if frac < 0.35 else (["high_battery"] if frac > 0.7 else [])
    return tags[:8]


def mock_note_writer(payload: dict[str, Any]) -> dict[str, Any]:
    by_proposal: dict[str, list[dict]] = {}
    for ev in payload["evidence"]:
        by_proposal.setdefault(ev["proposal_id"], []).append(ev)
    notes = []
    for pid, evs in list(by_proposal.items())[-3:]:
        forecast = [e for e in evs if e["phase"] != "actual"]
        actual = [e for e in evs if e["phase"] == "actual"]
        chosen = forecast + actual
        passed = all(e["passed"] for e in chosen)
        decisive = actual[0] if actual else next((e for e in forecast if not e["passed"]), forecast[0])
        codes = sorted({c for e in chosen for c in e["feedback_codes"]})
        prices = " ".join(f"{k}={v:g}" for k, v in sorted(decisive["tariff"].items()))
        m = decisive["metrics"]
        detail = f"min battery {m.get('min_battery_kwh', 0):g} kWh, served {m.get('served_fraction', 0):g}"
        verb = "passed" if passed else f"failed {', '.join(codes) or 'checks'}"
        notes.append({"evidence_ids": [e["evidence_id"] for e in chosen],
                      "situation_tags": _tags(decisive["situation"], passed, decisive["phase"], codes),
                      "description": f"Prices {prices} {verb}; {detail}.",
                      "duplicate_of": None, "conflicts_with": []})
    return {"observations": notes}
