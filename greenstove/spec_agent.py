"""Spec Agent: turns a one-line goal into a machine-checkable definition of done.

The model writes the checks; code keeps only metrics the verifier computes, fixes the
direction of each comparison, adds any committee requirement the model left out and
tightens any threshold looser than the committee's. The agent can make "done" stricter,
never weaker, so no definition of done can turn a failing day into success. The result is
cached per goal + committee + model, and frozen into the evaluation manifest so A/B/C share it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from greenstove.agent_models import stable_hash
from greenstove.env_protocol import Requirements
from greenstove.llm import LLMClient

# metric -> (direction, meaning); every metric here is computed by the verifier
VOCAB: dict[str, tuple[str, str]] = {
    "served_fraction": (">=", "share of household cooking requests booked and completed (0..1)"),
    "max_shift_hours": ("<=", "largest move of any household's cooking start from its preferred time (hours)"),
    "overload_steps": ("<=", "15-minute intervals where demand exceeds the inverter limit (count)"),
    "unserved_essential_kwh": ("<=", "essential-load energy not served (kWh)"),
    "unserved_cooking_kwh": ("<=", "cooking energy interrupted after a session started (kWh)"),
}
DEFAULT_GOAL = ("Every household cooks on electricity at a time it accepts and can afford, "
                "the mini-grid never overloads, and the essential load is never left unserved.")

SYSTEM = """You are the Spec Agent for a community solar cooking mini-grid. Turn the one-line
goal into a definition of done: a short list of checks the verifier can run without a human.
Use ONLY these metrics (direction fixed): {vocab}
The committee's minimum requirements are given; your checks may be stricter, never looser.
Reply with ONLY JSON:
{{"goal_restated": "<one sentence>", "checks": [{{"metric": "...", "op": "<=" or ">=",
  "threshold": <number>, "why": "<which words of the goal this check makes testable>"}}]}}"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SpecCheckOut(_Strict):
    metric: str
    op: Literal["<=", ">="]
    threshold: float
    why: str = Field(default="", max_length=200)


class SpecOutput(_Strict):
    goal_restated: str = Field(default="", max_length=300)
    checks: list[SpecCheckOut] = Field(default_factory=list, max_length=10)


class Check(_Strict):
    metric: str
    op: Literal["<=", ">="]
    threshold: float
    why: str = ""
    source: Literal["agent", "code_added", "code_tightened"] = "agent"


class DefinitionOfDone(_Strict):
    goal: str
    goal_restated: str = ""
    checks: list[Check]
    dropped: list[str] = []
    model: str = ""
    created_at: str = ""

    def spec_hash(self) -> str:
        return stable_hash({"goal": self.goal, "checks": [c.model_dump() for c in self.checks]})

    def requirements(self, base: Requirements) -> Requirements:
        """Requirements for the world's verifier: the stricter of committee and spec."""
        get = {c.metric: c.threshold for c in self.checks}
        return base.model_copy(update={
            "min_service_fraction": max(base.min_service_fraction, get.get("served_fraction", 0.0)),
            "max_shift_hours": min(base.max_shift_hours, get.get("max_shift_hours", base.max_shift_hours)),
            "essential_unserved_kwh": min(base.essential_unserved_kwh,
                                          get.get("unserved_essential_kwh", base.essential_unserved_kwh))})

    def evaluate(self, metrics: dict[str, float]) -> list[str]:
        """Failed checks as feedback codes; a metric the world does not compute is unchecked."""
        out = []
        for c in self.checks:
            v = metrics.get(c.metric)
            if v is None:
                out.append(f"DOD_UNCHECKED:{c.metric}")
            elif (c.op == ">=" and v < c.threshold - 1e-9) or (c.op == "<=" and v > c.threshold + 1e-9):
                out.append(f"DOD_{c.metric.upper()}")
        return out


def committee_floor(req: Requirements) -> dict[str, float]:
    return {"served_fraction": req.min_service_fraction, "max_shift_hours": req.max_shift_hours,
            "overload_steps": 0.0, "unserved_essential_kwh": req.essential_unserved_kwh}


def validate(out: SpecOutput, goal: str, req: Requirements, model: str) -> DefinitionOfDone:
    floor, checks, dropped, seen = committee_floor(req), [], [], set()
    for c in out.checks:
        if c.metric not in VOCAB or c.metric in seen:
            dropped.append(f"{c.metric}: {'unknown metric' if c.metric not in VOCAB else 'duplicate'}")
            continue
        op = VOCAB[c.metric][0]
        thr, source = c.threshold, "agent"
        if c.op != op:
            dropped.append(f"{c.metric}: direction {c.op} corrected to {op}")
        if c.metric in floor:
            looser = thr < floor[c.metric] if op == ">=" else thr > floor[c.metric]
            if looser:
                thr, source = floor[c.metric], "code_tightened"
        if c.metric == "served_fraction":
            thr = min(thr, 1.0)
        thr = max(thr, 0.0)
        checks.append(Check(metric=c.metric, op=op, threshold=round(thr, 4), why=c.why, source=source))
        seen.add(c.metric)
    for metric, thr in floor.items():
        if metric not in seen:
            checks.append(Check(metric=metric, op=VOCAB[metric][0], threshold=thr,
                                why="committee requirement", source="code_added"))
    return DefinitionOfDone(goal=goal, goal_restated=out.goal_restated, checks=checks, dropped=dropped,
                            model=model, created_at=time.strftime("%Y-%m-%dT%H:%M:%S"))


def write_spec(goal: str, req: Requirements, llm: LLMClient, cache_dir: Path | None = None,
               refresh: bool = False) -> DefinitionOfDone:
    key = stable_hash({"goal": goal, "req": req.model_dump(), "model": llm.s.public("spec_writer")["model"]})
    path = cache_dir / f"spec-{key}.json" if cache_dir else None
    if path and path.exists() and not refresh:
        return DefinitionOfDone.model_validate_json(path.read_text())
    vocab = "; ".join(f"{m} ({d}) = {meaning}" for m, (d, meaning) in VOCAB.items())
    payload = {"goal": goal, "committee_minimums": committee_floor(req)}
    out, _, _ = llm.complete_json("spec_writer", SYSTEM.format(vocab=vocab), payload, SpecOutput)
    dod = validate(out or SpecOutput(), goal, req, llm.s.public("spec_writer")["model"])
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dod.model_dump_json(indent=1))
    return dod


def mock_spec_writer(payload: dict[str, Any]) -> dict[str, Any]:
    """Deterministic stand-in: reads the goal's key phrases, deliberately leaves one out and
    proposes one looser threshold, so code_added / code_tightened are visible in mock mode."""
    goal, floor = payload["goal"].lower(), payload["committee_minimums"]
    checks = []
    if "cook" in goal:
        checks.append({"metric": "served_fraction", "op": ">=", "threshold": floor["served_fraction"] - 0.1,
                       "why": "'every household cooks on electricity'"})
    if "accept" in goal or "time" in goal:
        checks.append({"metric": "max_shift_hours", "op": "<=", "threshold": floor["max_shift_hours"],
                       "why": "'at a time it accepts'"})
    if "overload" in goal:
        checks.append({"metric": "overload_steps", "op": "<=", "threshold": 0, "why": "'never overloads'"})
    checks.append({"metric": "unserved_cooking_kwh", "op": "<=", "threshold": 0,
                   "why": "'cooks on electricity': a started meal must not be cut off"})
    return {"goal_restated": "Book every cooking request it can, close to preferred times, within grid limits.",
            "checks": checks}


def summary(dod: DefinitionOfDone) -> str:
    return json.dumps({"spec_hash": dod.spec_hash(), "checks": [f"{c.metric} {c.op} {c.threshold:g} ({c.source})"
                                                               for c in dod.checks]})
