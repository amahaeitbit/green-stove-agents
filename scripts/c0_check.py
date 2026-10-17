"""C0 environment check (Abel). Deterministic, no LLM.

Lives in scripts/ because greenstove/cli.py is partner-owned (PLAN.md section 3). The
partner can wire these into `python -m greenstove ...` later.

  python scripts/c0_check.py                  run both C0 cases, compare to expected.json
  python scripts/c0_check.py verify --plan P  one verdict as JSON (context/requirements default to C0)
  python scripts/c0_check.py evidence         write trace/verdict fixtures and JSON Schemas
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from greenstove import models  # noqa: E402
from greenstove.io import load_json, run_fixture, run_fixture_full  # noqa: E402

C0 = ROOT / "fixtures" / "c0"
CTX, REQ = C0 / "context.json", C0 / "requirements.json"
SCHEMAS = ["HouseholdRequest", "ScenarioContext", "ActualScenario", "Plan", "Requirements", "Trace", "Verdict",
           "InfeasibilityProof"]


def dump(obj) -> str:
    return json.dumps(obj.model_dump(mode="json"), indent=2) + "\n"


def summary(verdict: models.Verdict) -> dict:
    return {
        "passed": verdict.passed,
        "peak_load_kw": verdict.metrics["peak_load_kw"],
        "cooking_energy_kwh": verdict.metrics["cooking_energy_kwh"],
        "total_spend": verdict.metrics["total_spend"],
        "feedback": [{"code": f.code, "interval": f.interval, "end_interval": f.end_interval,
                      "request_ids": f.request_ids, "value": f.value, "limit": f.limit}
                     for f in verdict.feedback_codes],
    }


def cmd_verify(args: argparse.Namespace) -> int:
    verdict = run_fixture(args.context, args.plan, args.requirements)
    print(dump(verdict), end="")
    return 0 if verdict.passed else 1


def cmd_c0(_: argparse.Namespace) -> int:
    expected = load_json(C0 / "expected.json")
    limit = load_json(CTX)["inverter_kw"]
    print("Green-Stove C0  |  deterministic, no LLM  |  hand-built plans (allocator arrives at C1)")
    print("Assumed in C0: sufficient energy, no essential load; forecast_expected phase only\n")
    all_ok = True
    for case in expected["cases"]:
        verdict = run_fixture(CTX, C0 / case["plan"], REQ)
        got = summary(verdict)
        ok = got == case["expect"]
        all_ok &= ok
        print(f"[{'PASS' if verdict.passed else 'FAIL'}] {case['name']}")
        print(f"       {verdict.run_id}  coverage={verdict.coverage.scope} complete={verdict.coverage.complete}")
        print(f"       peak {got['peak_load_kw']:g} kW / limit {limit:g} kW, "
              f"energy {got['cooking_energy_kwh']:g} kWh, spend {got['total_spend']:.2f}")
        for f in verdict.feedback_codes:
            print(f"       {f.code} intervals [{f.interval}, {f.end_interval}) {f.request_ids}: {f.message}")
        print(f"       matches expected.json: {'yes' if ok else 'NO'}")
        if not ok:
            print("       got:      " + json.dumps(got))
            print("       expected: " + json.dumps(case["expect"]))
        print()
    print("C0 OK" if all_ok else "C0 MISMATCH")
    return 0 if all_ok else 2


def cmd_evidence(_: argparse.Namespace) -> int:
    for name in ("simultaneous", "sequential"):
        trace, verdict = run_fixture_full(CTX, C0 / f"plan-{name}.json", REQ)
        (C0 / f"trace-{name}.json").write_text(dump(trace), encoding="utf-8")
        (C0 / f"verdict-{name}.json").write_text(dump(verdict), encoding="utf-8")
        print(f"wrote fixtures/c0/trace-{name}.json, verdict-{name}.json")
    out = ROOT / "schemas"
    out.mkdir(exist_ok=True)
    for stale in out.glob("*.json"):
        stale.unlink()
    for name in SCHEMAS:
        (out / f"{name}.json").write_text(json.dumps(getattr(models, name).model_json_schema(), indent=2) + "\n",
                                          encoding="utf-8")
    print("wrote schemas/*.json")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="c0_check")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("evidence").set_defaults(fn=cmd_evidence)
    v = sub.add_parser("verify")
    v.add_argument("--plan", required=True)
    v.add_argument("--context", default=str(CTX))
    v.add_argument("--requirements", default=str(REQ))
    v.set_defaults(fn=cmd_verify)
    args = parser.parse_args(argv)
    try:
        return getattr(args, "fn", cmd_c0)(args)
    except (models.ContractError, ValueError) as exc:
        print(f"CONTRACT ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
