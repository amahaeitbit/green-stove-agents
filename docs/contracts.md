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

For each proposed tariff, deterministic households rank acceptable starts by cost plus configured shift penalty. Fix the exact score, priority and tie-breaker at C0. The allocator follows that order and checks interval resource feasibility. Refused or unserved requests have explicit codes; no silent acceptance or double allocation.

Cost is the sum of power_kw times 0.25 hours times interval price over the reservation. Quote IDs carry a tariff version. Accepted quotes are locked. Each candidate attempt starts from the same pre-day state; tentative reservations from rejected candidates do not persist. The selected accepted plan is committed only after verification. Intra-day rebooking is outside MVP scope.

## Feedback codes

Start with INVALID_TARIFF, INVERTER_LIMIT, BATTERY_POWER_LIMIT, BATTERY_RESERVE, ESSENTIAL_UNSERVED, OUTSIDE_WINDOW, BUDGET_EXCEEDED, SERVICE_SHORTFALL, and DUPLICATE_RESERVATION. Include interval/request references and computed values, avoiding unsupported explanations.

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
