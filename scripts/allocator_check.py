"""Run four deterministic allocator examples, without model calls or output files."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from greenstove.allocator import allocate
from greenstove.households import household_requests
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import Requirements, step_label
from greenstove.simulator import simulate
from greenstove.verifier import verify

TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}


def example(name: str):
    data = json.loads((ROOT / "fixtures/c0/context.json").read_text(encoding="utf-8"))
    data["scenario_id"] = "allocator-" + name
    # New synthetic authority for these examples, not approval of original C0 households.
    for request in data["requests"]:
        request["auto_book"] = True
    if name != "two-homes":
        data["requests"] = data["requests"][:1]
    request = data["requests"][0]
    if name == "flexible":
        request.update(earliest_start=60, shift_penalty=0.0)
    elif name == "budget":
        request["max_cost"] = 2.0
    elif name == "pending":
        request["auto_book"] = False
    return context_from_dict(with_hashes(data))


def main() -> int:
    print("Allocator check | at-home cooking | deterministic | power-only coverage")
    print("PV/battery energy and complete grid verification are not implemented here.\n")
    checks = []
    for name in ("two-homes", "flexible", "budget", "pending"):
        context = example(name)
        plan = allocate(household_requests(context), TARIFF, context, "allocator-" + name)
        verdict = verify(simulate(plan, context), Requirements(), context, plan)
        if name == "two-homes":
            ok = (verdict.passed and verdict.metrics["peak_load_kw"] == 2.0
                  and verdict.metrics["cooking_energy_kwh"] == 1.5
                  and verdict.metrics["total_spend"] == 19.5)
        elif name == "flexible":
            ok = (verdict.passed and plan.reservations[0].start_step == 60
                  and plan.reservations[0].locked_cost == 2.0)
        elif name == "budget":
            ok = (not verdict.passed and not plan.reservations
                  and plan.unserved[0].reason == "UNSERVED_BUDGET")
        else:
            ok = (not verdict.passed and not plan.reservations[0].accepted
                  and verdict.metrics["completed_sessions"] == 0)
        checks.append(ok and not verdict.coverage.complete)
        print(f"[{name}] checked_conditions_passed={verdict.passed}, coverage.complete={verdict.coverage.complete}")
        for reservation in plan.reservations:
            state = "accepted" if reservation.accepted else "pending approval"
            print(f"  {reservation.request_id}: {step_label(reservation.start_step)}-"
                  f"{step_label(reservation.end_step)}, {reservation.power_kw:g} kW, "
                  f"quote {reservation.locked_cost:.2f}, {state}")
        for unserved in plan.unserved:
            print(f"  {unserved.request_id}: unserved ({unserved.reason})")
        print(f"  matches expected behavior: {ok}\n")
    print("ALLOCATOR OK" if all(checks) else "ALLOCATOR MISMATCH")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
