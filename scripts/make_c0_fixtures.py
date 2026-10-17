"""Regenerate fixtures/c0/*.json. Run from the repo root: python scripts/make_c0_fixtures.py

The numbers are hand-checkable (see docs/c0-fixture.md). Edit here, not in the JSON, so
config_hash and scenario_hash stay correct. Windows, lattice and tier order match the
agent layer's scenarios.py so the same tariffs are valid in both.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from greenstove.billing import quote_for  # noqa: E402
from greenstove.io import context_from_dict, with_hashes  # noqa: E402
from greenstove.models import STEPS, Requirements, tariff_version  # noqa: E402

OUT = ROOT / "fixtures" / "c0"
DINNER = 72  # 18:00, inside the peak window [64, 96)
TARIFF = {"standard": 8.0, "sun": 4.0, "peak": 13.0}


def write(name: str, data: dict) -> None:
    (OUT / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"wrote fixtures/c0/{name}")


def request(rid: str, hid: str, kw: float) -> dict:
    return {
        "request_id": rid, "household_id": hid, "power_kw": kw,
        "duration_steps": 2,          # 30 minutes
        "preferred_start": DINNER,    # 18:00
        "earliest_start": 68,         # 17:00
        "latest_finish": 80,          # 20:00, exclusive
        "max_cost": 20.0,             # currency
        "shift_penalty": 2.0,         # currency per hour of shift (used by the allocator from C1)
        "auto_book": False,           # C0 plans stand for manual acceptance
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stale in ("tariff.json",):
        (OUT / stale).unlink(missing_ok=True)
    zeros = [0.0] * STEPS
    context = with_hashes({
        "scenario_id": "c0-two-households",
        "config_hash": "", "scenario_hash": "",
        "day": 0, "weather_class": None,
        "initial_battery_kwh": 6.0, "battery_capacity_kwh": 6.0, "reserve_kwh": 1.2,
        "inverter_kw": 2.0,
        "forecast_pv_kw": zeros, "downside_pv_kw": zeros, "essential_load_kw": zeros,
        "requests": [request("req-A", "hh-A", 1.0), request("req-B", "hh-B", 2.0)],
        "windows": [{"name": "standard", "start_step": 0, "end_step": 40},
                    {"name": "sun", "start_step": 40, "end_step": 64},
                    {"name": "peak", "start_step": 64, "end_step": 96}],
        "lattice": {"standard": [6.0, 8.0, 10.0], "sun": [3.0, 4.0, 5.0], "peak": [10.0, 13.0, 16.0]},
        "tier_order": ["sun", "standard", "peak"],
        "assumptions": [
            "C0: power only; sufficient stored energy assumed, PV and battery not modeled",
            "C0: no essential (clinic) load",
            "All values synthetic; prices are placeholders, not sourced tariffs",
        ],
    })
    ctx = context_from_dict(context)
    write("context.json", context)
    write("request.json", context["requests"][0])
    write("requirements.json", Requirements(min_service_fraction=1.0, max_shift_hours=3.0,
                                            essential_unserved_kwh=0.0).model_dump(mode="json"))

    def res(rid: str, kw: float, start: int) -> dict:
        end = start + 2
        return {"reservation_id": f"res-{rid}-{start}", "request_id": rid, "start_step": start, "end_step": end,
                "power_kw": kw, "locked_cost": quote_for(kw, start, end, TARIFF, ctx),
                "tariff_version": tariff_version(TARIFF), "accepted": True}

    write("plan-simultaneous.json", {"proposal_id": "c0-fixed", "tariff": TARIFF,
                                     "reservations": [res("req-A", 1.0, DINNER), res("req-B", 2.0, DINNER)],
                                     "unserved": []})
    write("plan-sequential.json", {"proposal_id": "c0-fixed", "tariff": TARIFF,
                                   "reservations": [res("req-A", 1.0, DINNER), res("req-B", 2.0, DINNER + 2)],
                                   "unserved": []})

    write("expected.json", {
        "note": "Hand-calculated. Peak 13/kWh: A = 1 kW x 0.5 h x 13 = 6.50, B = 2 kW x 0.5 h x 13 = 13.00.",
        "cases": [
            {"name": "simultaneous: A and B both 18:00-18:30", "plan": "plan-simultaneous.json",
             "expect": {"passed": False, "peak_load_kw": 3.0, "cooking_energy_kwh": 1.5, "total_spend": 19.5,
                        "feedback": [{"code": "INVERTER_LIMIT", "interval": 72, "end_interval": 74,
                                      "request_ids": ["req-A", "req-B"], "value": 3.0, "limit": 2.0}]}},
            {"name": "sequential: A 18:00-18:30, B 18:30-19:00", "plan": "plan-sequential.json",
             "expect": {"passed": True, "peak_load_kw": 2.0, "cooking_energy_kwh": 1.5, "total_spend": 19.5,
                        "feedback": []}},
        ],
    })


if __name__ == "__main__":
    main()
