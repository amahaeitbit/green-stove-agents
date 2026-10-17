"""Load C0 fixture files and run the deterministic path end to end."""
from __future__ import annotations

import json
from pathlib import Path

from .models import (
    ContractError,
    Plan,
    Requirements,
    ScenarioContext,
    Trace,
    Verdict,
    compute_scenario_hash,
    compute_station_hash,
)
from .simulator import simulate
from .tariff import validate_tariff
from .verifier import verify


def load_json(path: str | Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f"non-finite {c}")))


def with_hashes(data: dict) -> dict:
    """Copy of a fixture context dict with config_hash and scenario_hash recomputed."""
    out = dict(data)
    out["config_hash"] = compute_station_hash(out)
    out["scenario_hash"] = compute_scenario_hash(out)
    return out


def context_from_dict(data: dict, source: str = "context") -> ScenarioContext:
    """Fixture contexts must carry hashes that match their contents."""
    for field, fn in (("config_hash", compute_station_hash), ("scenario_hash", compute_scenario_hash)):
        expected = fn(data)
        if data.get(field) != expected:
            raise ContractError(f"{source}: {field} is {data.get(field)!r}, contents hash to {expected!r}")
    return ScenarioContext.model_validate(data)


def load_context(path: str | Path) -> ScenarioContext:
    return context_from_dict(load_json(path), str(path))


def load_plan(path: str | Path) -> Plan:
    return Plan.model_validate(load_json(path))


def load_requirements(path: str | Path) -> Requirements:
    return Requirements.model_validate(load_json(path))


def run_fixture_full(context_path: str | Path, plan_path: str | Path,
                     requirements_path: str | Path) -> tuple[Trace, Verdict]:
    context = load_context(context_path)
    plan = load_plan(plan_path)
    invalid = validate_tariff(plan.tariff, context)
    if invalid:
        raise ContractError("INVALID_TARIFF: " + "; ".join(i.message for i in invalid))
    trace = simulate(plan, context, "forecast_expected")
    return trace, verify(trace, load_requirements(requirements_path), context, plan)


def run_fixture(context_path: str | Path, plan_path: str | Path, requirements_path: str | Path) -> Verdict:
    return run_fixture_full(context_path, plan_path, requirements_path)[1]
