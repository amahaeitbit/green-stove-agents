# Implementation contracts

Status: interfaces to implement, agreed at C0. [PLAN.md](../PLAN.md) is the scope authority. Abel owns changes to this contract and models.py after both developers agree. All names below are proposed, not implemented APIs.

## Units and authority

Use 15-minute interval indices from 0 through 95. Power is kW; energy is kWh; battery state is stored as kWh and may be displayed as a fraction. Money uses a documented fixed-point representation; define rounding once for quotes and scoring. Prices are currency units per kWh. Use one configured local clock for display only.

All households are synthetic. Private household rules and immutable committee configuration are not learned facts. Agents can propose tariffs and observations; they cannot directly commit reservations or modify bounds. Fixed tariff windows partition the day without overlap. Every price must belong to a finite configured lattice and satisfy tier-order/committee rules.

## Typed objects

| Object | Minimum fields |
| --- | --- |
| HouseholdRequest | request_id, household_id, power_kw, duration_steps, preferred_start, earliest_start, latest_finish, max_cost, shift_penalty, auto_book |
| ScenarioContext | scenario_id, config_hash, initial_battery_kwh, forecast_pv_kw[96], downside_pv_kw[96], essential_load_kw[96], current requests, tariff lattice, fixed bounds |
| ActualScenario | actual_pv_kw[96], optional fixed load perturbations; visible only to execution/evaluator |
| TariffProposal | proposal_id, three tier prices, cited_observation_ids, short rationale |
| Reservation | reservation_id, request_id, start_step, end_step exclusive, power_kw, locked_cost, tariff_version, accepted |
| Plan | proposal_id, accepted reservations, pending/unserved requests with reasons |
| Trace | interval demand/supply, battery energy and flows, unserved essential/cooking energy, overloads, reservation outcomes |
| Verdict | run_id, scenario_id, config_hash, phase, passed, condition results, computed metrics, feedback_codes |
| ObservationProposal | evidence_ids, situation tags, proposed description; no trusted metrics or authority |
| ApprovedObservation | id, config_hash, situation descriptors, tariff, reconstructed outcomes, phase, evidence_ids, created_at, expires_at, status, version |
| CallRecord | role, model, prompt_hash, memory_hash, settings, attempt, tokens, latency, request_id, outcome |

Validate non-finite numbers, unknown fields, negative durations/prices, malformed intervals, and duplicate IDs. Requests default to requiring acceptance unless explicit synthetic configuration authorizes auto-booking. Without approval, an offer remains pending and cannot count as a completed meal.

## Functions and ownership

```text
# Abel
household_requests(context) -> list[HouseholdRequest]
allocate(requests, tariff, context) -> Plan
simulate(plan, context, phase, actual=None) -> Trace
verify(trace, requirements) -> Verdict
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

## Allocation and quotes

For each proposed tariff, deterministic households rank acceptable starts by cost plus configured shift penalty. Fix the exact score, priority and tie-breaker at C0. The allocator follows that order and checks interval resource feasibility. Refused or unserved requests have explicit codes; no silent acceptance or double allocation.

Cost is the sum of power_kw times 0.25 hours times interval price over the reservation. Quote IDs carry a tariff version. Accepted quotes are locked. Each candidate attempt starts from the same pre-day state; tentative reservations from rejected candidates do not persist. The selected accepted plan is committed only after verification. Intra-day rebooking is outside MVP scope.

## Feedback codes

Start with INVALID_TARIFF, INVERTER_LIMIT, BATTERY_POWER_LIMIT, BATTERY_RESERVE, ESSENTIAL_UNSERVED, OUTSIDE_WINDOW, BUDGET_EXCEEDED, SERVICE_SHORTFALL, and DUPLICATE_RESERVATION. Include interval/request references and computed values, avoiding unsupported explanations.

## Status semantics

- PLAN_VERIFIED: fixed planning conditions pass in expected and downside forecast scenarios.
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
