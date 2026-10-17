"""Hand-calculated battery commitment example. No API calls or file writes."""
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from greenstove.admission import advance_interval, can_start, initial_state, outstanding_storage_kwh
from greenstove.admission_models import BatterySpec
from greenstove.billing import quote_for
from greenstove.io import context_from_dict, with_hashes
from greenstove.models import HouseholdRequest, Reservation, tariff_version


def main():
    fixture = json.loads((ROOT / "fixtures/admission/commitment-example.json").read_text())
    data = json.loads((ROOT / "fixtures/c0/context.json").read_text())
    start = fixture["start_step"]
    data.update(scenario_id="admission-commitment-example", initial_battery_kwh=fixture["initial_battery_kwh"],
                battery_capacity_kwh=fixture["capacity_kwh"], reserve_kwh=fixture["floor_kwh"],
                inverter_kw=fixture["inverter_kw"],
                essential_load_kw=[0.0]*start + [fixture["essential_kw"]]*(96-start))
    data["requests"] = [HouseholdRequest(request_id=rid, household_id="home-"+rid,
        power_kw=fixture["session_power_kw"], duration_steps=fixture["session_duration_steps"],
        preferred_start=start, earliest_start=start, latest_finish=96, max_cost=100.0,
        shift_penalty=1.0, auto_book=True).model_dump() for rid in ("A", "B")]
    context = context_from_dict(with_hashes(data))
    battery = BatterySpec(**{k: fixture[k] for k in ("capacity_kwh", "floor_kwh", "max_charge_kw",
                            "max_discharge_kw", "charge_efficiency", "discharge_efficiency")})
    tariff = {"standard": 8.0, "sun": 4.0, "peak": 13.0}

    def offer(rid, step):
        end = step + fixture["session_duration_steps"]
        power = fixture["session_power_kw"]
        return Reservation(reservation_id="reservation-"+rid, request_id=rid, start_step=step,
                           end_step=end, power_kw=power, locked_cost=quote_for(power, step, end, tariff, context),
                           tariff_version=tariff_version(tariff), accepted=True)

    state = initial_state(context, battery, start_step=start)
    first = can_start(offer("A", start), tariff, context, state, battery)
    second = can_start(offer("B", start), tariff, context, first.state, battery)
    interval = advance_interval(first.state, context, battery)
    finished = advance_interval(interval.state, context, battery)
    deadline = can_start(offer("B", finished.state.step), tariff, context, finished.state, battery)
    state = finished.state
    while state.step < 96:
        state = advance_interval(state, context, battery).state
    checks = [
        first.status == fixture["expected_first_status"],
        second.status == fixture["expected_second_status"],
        math.isclose(first.required_storage_kwh, fixture["expected_first_required_storage_kwh"]),
        math.isclose(interval.state.battery_kwh, fixture["expected_after_first_interval_battery_kwh"]),
        math.isclose(finished.state.battery_kwh, fixture["expected_after_session_battery_kwh"]),
        math.isclose(outstanding_storage_kwh(finished.state, battery),
                     fixture["expected_after_session_outstanding_storage_kwh"]),
        deadline.status == fixture["expected_deadline_status"],
        math.isclose(state.battery_kwh, fixture["expected_end_battery_kwh"]),
        list(state.completed_request_ids) == fixture["expected_completed_request_ids"],
    ]
    print("Battery-backed admission | synthetic last-hour fixture | no future solar credit")
    print(f"A: {first.status}; required cooking + essential storage {first.required_storage_kwh:.3f} kWh")
    print(f"B: {second.status}; {second.reason}")
    print(f"After one interval: battery {interval.state.battery_kwh:.3f}; "
          f"remaining cooking commitment {outstanding_storage_kwh(interval.state, battery):.3f} kWh")
    print(f"After A completes: battery {finished.state.battery_kwh:.3f}; completed {finished.state.completed_request_ids}")
    print(f"B at latest permissible start: {deadline.status}; {deadline.reason}")
    print(f"End battery: {state.battery_kwh:.3f}; essential shortage {state.unserved_essential_kwh:.3f} kWh")
    print("ADMISSION OK" if all(checks) else "ADMISSION MISMATCH")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
