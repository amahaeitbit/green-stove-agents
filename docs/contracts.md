# Implementation contracts

Status: shared contract pending joint C0 sign-off. [PLAN.md](../PLAN.md) is the scope authority. Abel owns changes to this contract and models.py after both developers agree. [c0-fixture.md](c0-fixture.md) records the implemented power-only subset and proposed agent-layer alignment; remaining functions and extensions are planned.

## Operating model

Cooking takes place at home using household appliances connected to one local mini-grid.
"Station" denotes the shared generation/storage and aggregate inverter boundary, not a
shared cooking location. A reservation allocates electrical capacity over a session,
not exclusive use of a communal cooker. Concurrent cooking is allowed within shared limits.

Household agents retain authority over preferences and acceptance. The local coordinator
offers capacity-backed reservations, and deterministic code checks modeled resource limits.
Calendar-derived availability is supplied as synthetic cooking windows; no live calendar
integration or appliance-control integration is part of the MVP.

Cooking is the only modeled household demand. Any essential/background load must be
explicit in the scenario; C0 requires zero essential load. Accepted sessions are assumed
to follow their bookings. Do not claim a reservation physically prevents unscheduled use.
Distribution losses, voltage/phase effects and household connection limits are initially
omitted. Multiple independently coordinated mini-grids are the deployment vision, while
this prototype simulates one and implements no inter-grid exchange or federation.

## Units and authority

Use 15-minute interval indices from 0 through 95, with 96 allowed as an exclusive end. Power is kW; energy is kWh; battery state is stored as kWh and may be displayed as a fraction. The C0 implementation proposes float currency fields aligned with the agent layer, with Decimal arithmetic summing interval costs before half-up rounding the session total to 0.01. Prices are currency units per kWh; shift_penalty is currency per hour shifted. Both developers must confirm this convention at sign-off; it replaces the earlier proposed fixed-point representation if accepted. Use one configured local clock for display only.

All households are synthetic. Private household rules and immutable committee configuration are not learned facts. Agents can propose tariffs and observations; they cannot directly commit reservations or modify bounds. Fixed tariff windows partition the day without overlap. Every price must belong to a finite configured lattice and satisfy tier-order/committee rules.

## Typed objects

| Object | Minimum fields |
| --- | --- |
| HouseholdRequest | request_id, household_id, power_kw, duration_steps, preferred_start, earliest_start, latest_finish, max_cost, shift_penalty, auto_book |
| ScenarioContext | scenario_id, config_hash (station compatibility), scenario_hash (day), initial_battery_kwh, forecast_pv_kw[96], downside_pv_kw[96], essential_load_kw[96], current requests, tariff lattice, fixed bounds |
| ActualScenario | actual_pv_kw[96], optional fixed load perturbations; visible only to execution/evaluator |
| TariffProposal | proposal_id, three tier prices, cited_observation_ids, short rationale |
| Reservation | reservation_id, request_id, start_step, end_step exclusive, power_kw, locked_cost, tariff_version, accepted |
| Plan | proposal_id, tariff used for quotes, reservations with acceptance state, unserved requests with reasons |
| Trace | simulation_id, config_hash, scenario_hash, phase, tariff_version, simulator_version, interval loads/supply, modeled battery/outcomes; unmodeled fields explicitly absent or null |
| Verdict | run_id, simulation_id, scenario_id, config_hash, scenario_hash, phase, passed, conditions, metrics, feedback_codes, requirements, verifier_version, coverage |
| ObservationProposal | evidence_ids, situation tags, proposed description; no trusted metrics or authority |
| ApprovedObservation | id, config_hash, situation descriptors, tariff, reconstructed outcomes, phase, evidence_ids, created_at, expires_at, status, version |
| CallRecord | role, model, prompt_hash, memory_hash, settings, attempt, tokens, latency, request_id, outcome |

Validate non-finite numbers, unknown fields, negative durations/prices, malformed intervals, and duplicate IDs. Requests default to requiring acceptance unless explicit synthetic configuration authorizes auto-booking. Without approval, an offer remains pending and cannot count as a completed meal.

## Functions and ownership

```text
# Abel
household_requests(context) -> list[HouseholdRequest]
allocate(requests, tariff, context, proposal_id) -> Plan
simulate(plan, context, phase, actual=None) -> Trace
verify(trace, requirements, context, plan) -> Verdict
nearest_tariff(context, development_library, fixed_distance) -> TariffProposal | None
search_tariffs(context, candidate_lattice, candidate_budget) -> SearchResult

# Partner
propose(context, brief, current_feedback) -> TariffProposal
propose_observations(verdicts, relevant_brief) -> list[ObservationProposal]
admit(proposal, evidence_store, config) -> AdmissionResult
compile_brief(role, situation, snapshot, token_budget) -> Brief
run_agent_day(context, memory_mode, fixed_requirements) -> RunRecord
```

The application, not the LLM, owns evidence lookups. Agent context excludes actual future weather, hidden expected answers and other evaluation outcomes. Admission reconstructs action/outcomes from run IDs and verifies phase and configuration. A matched number does not authorize a broad causal lesson.

The signatures above reflect the C0 agent-layer alignment proposal, pending joint sign-off.
The plan carries its quoted tariff so simulation/verification can recompute costs. The
station and scenario hash recipes must be canonical across both branches; separate local
implementations with the same name are not an integration agreement.

## Allocation and quotes

The at-home operating model and allocator conventions have been confirmed by Abel with
the partner. `households.py` and `allocator.py` implement the next power-only increment;
full energy accounting and agent-layer integration remain pending.

Process requests in two passes, independent of input list order: first all requests with
`auto_book=true`, then all others, each pass ordered by `(preferred_start, request_id)`.
Each household ranks in-window starts by `rounded session quote + shift_penalty *
abs(start - preferred_start) * 0.25`, with the earliest start breaking equal-score ties.
Penalty is per hour. Reject starts over budget. Select the highest-ranked start that
fits aggregate inverter capacity in every interval, including explicit essential load.
Quote and score arithmetic use Decimal internally, with the existing session-total
half-up rounding rule. These functions do not mutate inputs.

An offer is accepted only if `auto_book=true`. Otherwise it remains `accepted=false`;
manual approval is not implemented by this allocator. Pending offers are made only from
capacity left after every auto-booking request (decision D1, option 3) and hold that
capacity within the current candidate, so a later approval cannot overbook and a pending
offer cannot displace a household that can cook. They are not dispatched or counted as
served by the simulator/verifier. The same policy must be used in all comparisons; the
partner's development stub should adopt the same two-pass order. Holds have no persistent
lifecycle here: each allocation call builds a fresh candidate.

The verifier independently checks aggregate held capacity (accepted plus pending) from
the plan. An overbooked pending hold raises ContractError; consumption and service still
count accepted reservations only. Simulator and verifier classify power overloads using
the same 1e-9 kW tolerance, retaining unrounded interval loads until presentation.
Verdicts contain detached deep copies of coverage and requirements metadata so a caller
cannot modify another verdict or future verification results through a returned object.

Requests must match their authoritative scenario records. Duplicate, unknown or altered
inputs raise ContractError. Valid requests with no affordable allowed start yield
`UNSERVED_BUDGET`; affordable requests with no capacity-fitting start yield
`UNSERVED_CAPACITY`. These two reasons are informational and are the only reasons allowed
in `plan.unserved`; the verifier rejects unserved entries that cite unknown requests, repeat
a request, name a request that is also reserved, or use any other reason (ContractError).
`allocate(..., *, max_shift_hours=None)` takes an optional keyword-only limit; callers pass
`requirements.max_shift_hours` so the allocator never picks a start the verifier would
reject for shift. The preferred start always remains a candidate. Invalid tariffs raise
`ContractError` prefixed `INVALID_TARIFF`;
conversion to structured agent feedback remains a C2 integration task. Subsets are
permitted, but the verifier measures service against the full scenario.

This is a deterministic greedy policy, not a globally optimal scheduler. It checks power
capacity only; it does not establish PV/battery energy availability, battery reserve,
or complete PLAN_VERIFIED status. It enforces requirements.max_shift_hours only when the
caller passes it.
The verifier checks additional requirements after allocation. Separate household
connection limits and conflicts between multiple appliances in one home are not modeled.

Allocation reasons remain visible even when the configured service floor permits some
unserved requests. Verdict.passed is determined by condition results, not by an empty
feedback list. An unserved request is not itself evidence that an accepted reservation
exceeded budget or capacity. The default service floor still requires all requests.

Cost is the sum of power_kw times 0.25 hours times interval price over the reservation. Quote IDs carry a tariff version. Accepted quotes are locked. Each candidate attempt starts from the same pre-day state; tentative reservations from rejected candidates do not persist. The selected accepted plan is committed only after verification. Intra-day rebooking is outside MVP scope.

## Feedback codes

Violations (each backs a failed condition): INVALID_TARIFF, INVERTER_LIMIT (an interval's
load exceeds the inverter), BATTERY_POWER_LIMIT, BATTERY_RESERVE, ESSENTIAL_UNSERVED,
OUTSIDE_WINDOW, BUDGET_EXCEEDED (an accepted booking costs more than its household maximum),
SERVICE_SHORTFALL, and DUPLICATE_RESERVATION.

Informational (never fatal by themselves; the service condition decides): UNSERVED_CAPACITY
(affordable, but no start fits the remaining capacity), UNSERVED_BUDGET (no allowed start
is affordable), and UNSERVED_NOT_OFFERED (emitted by the verifier for a scenario request that
appears in neither the reservations nor the unserved list, e.g. after allocating a subset).
Only UNSERVED_CAPACITY and UNSERVED_BUDGET may appear in plan.unserved. Every scenario request
is therefore accounted for in every verdict. A passing verdict may carry informational codes but never a violation code.
Agents and memory should treat UNSERVED_* as "a household was not booked", not as a failure.

Include interval/request references and computed values, avoiding unsupported explanations.

## Status semantics

- PLAN_VERIFIED: fixed planning conditions pass in both expected and downside forecast scenarios with complete required verification coverage. A C0 verdict with passed=true and coverage.complete=false is only a power-only fixture pass and cannot produce this status.
- EXECUTION_COMPLETE: actual simulated day ended; attach outcome_passed and actual violation metrics.
- NO_VERIFIED_PLAN: budget exhausted, repeated proposal, or no acceptable candidate found.
- INFEASIBLE: a deterministic lower-bound calculation proves specified requirements cannot fit resources. Record assumptions and calculation.

Model/network failure is a separate error reason, not physical infeasibility. No plan is dispatched without hard checks; any best-effort execution must be explicitly labeled and governed by the same essential-load protection rules.

## C0 fixtures and intended commands

Create one JSON fixture each for request, context, tariff, accepted plan, trace/verdict and observation. Both developers must load them before building independently.

Intended CLI:

```text
python -m greenstove smoke --mode mock
python -m greenstove smoke --mode crusoe
python -m greenstove plan --scenario dev-low-battery --brain off
python -m greenstove brain audit
python -m greenstove eval --manifest evaluation-manifest.json
python -m greenstove demo
```

Commands become documentation of implemented behavior only after the matching gate passes.

## Household-agent communication boundary

See [household-agent-handoff.md](household-agent-handoff.md). Existing request/plan types
support structured in-process exchanges. The caller routes each reservation or unserved
outcome back to the household identified by the authoritative request_id -> household_id
mapping. A pending offer is not an accepted booking or completed session. The environment
branch does not implement delivery, a message broker, external agent protocol, or manual
approval. The partner's adapter/supervisor must wire delivery, preserve identifiers and
honor coverage.complete before advertising verified grid service.

## Battery-backed start admission

Environment-side independent verification is implemented in `admission_verifier.py`,
with the supervisor-compatible bridge in `admission_world.py`. The `abel-admission`
loader selects this bridge; see [the integration handoff](environment-integration-ready.md).
Normal days and the acceptance rehearsal share one ledger execution loop. Actual
verification takes evaluator-supplied ActualScenario separately; a
trace's own PV array is not authoritative. Configuration identity includes BatterySpec,
policy/simulator/verifier versions and effective requirements. Regenerate old development
memory after adopting this identity. No C0 schema or version changes are implied.

The team agreed to delay starts instead of interrupting cooking under the fixed simulation
model. [admission-policy.md](admission-policy.md) defines the environment-side implementation.
The local supervisor must distinguish a booked reservation from an admitted session.
Before a start, can_start checks stored energy for the entire session, outstanding active
commitments and essential demand through the end of day. Future solar receives no credit.
The battery floor is a hard discharge bound for every load; essential shortages are recorded.

Admission uses separate immutable records in admission_models.py, preserving the C0
allocator/simulator interfaces. Persist each returned state before another admission or
dispatch. The supervisor owns serialization; it must not admit concurrent sessions from
the same stale snapshot. ADMIT commits resources, DEFER requests a permitted alternative,
and UNSERVED means no in-window retry remains. Pending offers cannot start. Repricing,
household approval and delivery are integration responsibilities, not implicit permissions.

BatterySpec declares explicit efficiencies and charge/discharge power limits. Its capacity
and floor match context configuration. Interval accounting consumes energy and releases
remaining commitments once, charges from observed surplus, records curtailment and essential
shortages, and marks completion only after delivery. Full phase integration and independent
energy verification remain pending; the C0 coverage flag stays incomplete.
