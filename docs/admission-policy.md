# Battery-backed admission increment

Status: environment-side admission and interval ledger implemented after allocator commit
818bd91. The partner's supervisor/world adapter, complete phase simulation and full energy
verdict remain integration work. This does not change C0 coverage or establish real-grid safety.

## Agreed policy

Cooking happens at home. Booked capacity is not permission to start. Before starting a
session, deterministic code protects essential demand, already-running cooking and the
hard battery floor. Reserve sufficient stored energy for the complete session using zero
future solar credit. Protect essential demand from the current interval through step 96.
The floor applies to every load: when it prevents serving essential demand, record the
shortage instead of discharging below it. Delay new starts rather than interrupt admitted
sessions under the fixed model. The LLM cannot override resource checks.

## Added modules and API

```python
from greenstove.admission import initial_state, can_start, advance_interval
from greenstove.admission_models import BatterySpec

state = initial_state(context, battery_spec)
decision = can_start(reservation, plan.tariff, context, state, battery_spec,
                     requirements=requirements)
state = decision.state       # persist this before testing another start
interval = advance_interval(state, context, battery_spec, pv_kw=observed_pv_kw)
state = interval.state
```

BatterySpec supplies capacity, floor, charge/discharge power limits, and efficiencies.
Capacity/floor must match the existing scenario. Discharge power is delivered load-side
kW; storage withdrawal divides delivered energy by discharge efficiency. Charge power is
input-side kW; stored energy multiplies charge input by charge efficiency.

AdmissionState is an immutable snapshot, with immutable active commitments, historical
admitted IDs, completed IDs, delivered cooking energy and essential shortage. It is bound
to the full scenario and battery specification. Trusted application code owns the ledger;
model agents must not author or edit snapshots. The supervisor must serialize transitions
or use transactional compare-and-swap. Calling twice against an old snapshot is not an
atomic concurrent booking system. No external persistence implementation is added here.

can_start is a pure transition returning ADMIT, DEFER or UNSERVED, a reason, required and
available storage, and the next ledger. ADMIT must be persisted before dispatch. It checks
quote/tariff identity, household window, budget, shift requirements, auto-book authority,
all remaining committed cooking, future essential power, inverter power, battery discharge
power, and total storage above the floor. Pending/manual approvals are not implemented.

An energy/power failure is DEFER while another in-window start remains, otherwise UNSERVED.
DEFER is not permission to change the quote or start time. A new offer must be repriced
and authorized within the unchanged household rules; the function never silently retimes
the reservation. Retry scheduling and quote acceptance remain the adapter's responsibility.
Admitted request/reservation IDs cannot be dispatched twice, even after completion.

advance_interval consumes one fifteen-minute interval. Actual observed PV serves load
before battery discharge; surplus may charge within power/capacity limits and efficiency,
with excess curtailed. Essential demand receives priority. Its unserved energy is explicit.
Cooking completion occurs only at the session's final interval. A completed commitment is
removed; its past energy remains consumed. Admission itself never withdraws stored energy.
Outstanding cooking commitments decrease as their intervals are delivered, avoiding
double deduction. Essential protection is recomputed over the remaining horizon rather
than added repeatedly as another commitment.

An inability to supply an admitted session raises ContractError rather than silently
interrupting or marking it complete: it means the fixed-profile/ledger assumptions were
violated. Real equipment failures, unexpected household draw and out-of-band ledger edits
are outside this increment; no unconditional physical completion guarantee is claimed.

## Hand-calculated fixture and checks

Run `python scripts/admission_check.py`. It reads
fixtures/admission/commitment-example.json and constructs a separate last-hour scenario
from the C0 layout. Original C0 contexts/plans/traces/verdicts/schemas are not regenerated.
Starting at step 92, stored energy is 1.0 kWh, floor is 0.4, essential load is 0.1 kW
for the remaining hour, and each cooking request needs 0.5 kWh. Efficiencies are 1.0.

- A is admitted: 0.5 cooking + 0.1 essential = 0.6 kWh available above the floor.
- B is deferred: A's commitment plus B plus essential would require 1.1 kWh.
- After one interval: battery 0.725 kWh; A's outstanding cooking commitment 0.25 kWh.
- After A completes: battery 0.45 kWh; outstanding cooking 0; completed IDs contain A.
- B retried at its latest valid start is UNSERVED; the last essential intervals leave
  the battery at 0.4 kWh with no essential shortage.

`tests/test_admission.py` adds 18 tests, including 100 seeded accounting cases, authority,
duplicate dispatch, exact-floor commitments, power limits, efficiency, charging/curtailment,
essential shortage, observed solar enabling later starts, immutable inputs and day-end bounds.
The full suite currently has 102 passing tests. Run:

```bash
python -m unittest discover -s tests -v
python scripts/admission_check.py
python scripts/allocator_check.py
python scripts/c0_check.py
```

## Partner handoff and household messages

The supervisor calls admission at each booked start using the current persisted ledger.
Send only status/reason and the relevant reservation/request IDs to the owning household
agent, using the authoritative request-to-household mapping. Do not send the entire ledger
or other households' commitments. ADMIT means the modeled start has committed resources;
DEFER means wait or negotiate; UNSERVED means no remaining start under current authority.
These are admission decisions, not overall PLAN_VERIFIED/EXECUTION_COMPLETE statuses.

This increment provides no network transport, real calendar/device integration, automatic
rescheduling, manual approval or full WorldEnvironment adapter. The next joint gate wires
the policy into expected/downside/actual simulations, reconstructs independent energy
verdicts, and routes an admission result to a household. C0's complete=False remains in
force until those required checks exist. BatterySpec and admission policy must be included
in the final frozen configuration/evidence identities during that integration.
