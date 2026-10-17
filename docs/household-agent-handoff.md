# Household agent communication handoff

Status: the environment supports structured request/offer data; delivery to agents and
the world adapter remain integration work. This document does not claim a live message
transport or household LLM deployment.

## Confirmed model

Cooking happens at home on household appliances. One local mini-grid coordinates the
shared power capacity. Synthetic household software agents supply availability, power,
duration, budget and booking authority. Calendar-derived availability is represented by
time windows; no live calendar or device-control integration exists.

## Information flow using existing types

1. A household supplies a HouseholdRequest. The environment's current scenario records
   are authoritative. household_requests(context) returns detached copies for coordination.
2. The grid Pricing Agent proposes prices. The supervisor/adapter calls
   allocate(requests, tariff, context, proposal_id,
   max_shift_hours=requirements.max_shift_hours).
3. The allocator returns Plan.tariff, reservations and reason-coded unserved requests.
   Each reservation includes request_id, reservation_id, start_step, end_step, power_kw,
   locked_cost, tariff_version and accepted.
4. The adapter looks up each request_id in context.requests to obtain household_id and
   routes only that household's offer/result back to it. Time labels are a presentation
   conversion from interval indices. Keep the routing identity outside model discretion.
5. accepted=true reflects existing auto-book authority. accepted=false is an offer needing
   approval, with capacity held in the current candidate only. Neither a pending offer
   nor a power-only checked verdict should be presented as completed cooking or fully
   verified energy service.
6. An unserved household receives its request_id and reason: UNSERVED_BUDGET or
   UNSERVED_CAPACITY. If a request was omitted from the allocation, the verifier records
   UNSERVED_NOT_OFFERED. These explain outcomes, not necessarily failed requirements.

For the hackathon, normal function calls and JSON-serializable records are enough for
this exchange. No additional framework, external A2A protocol or message broker is
required. Serialization alone does not implement delivery; the partner owns that wiring.

## Approval and privacy boundaries

The current allocator does not process manual acceptance messages. A future approval
handler must authenticate the household, refer to the same reservation/tariff version,
check that the offer is still current, and revalidate capacity before dispatch. A new
allocation call discards the old candidate; holds do not persist across calls. Approval
of unchanged holds within one validated plan is capacity-safe because the verifier checks
all held load, but no approval workflow is implemented here.

Do not broadcast every household's preferences or offers to all agents. Household agents
receive their own request outcome; shared memory stores scoped observations backed by
evidence. All evaluation methods must use the same authority and communication rules.

## Joint integration gate

- Replace/adapt the partner's provisional types without silently changing signatures.
- Use a single canonical station/scenario hash recipe and preserve evidence identities.
- Pass requirements.max_shift_hours through the adapter's allocation call.
- Mirror the two-pass auto-booker-first policy in any retained development stub.
- Demonstrate one request -> priced reservation -> household-specific routed result,
  including one pending result and one reason-coded rejection.
- Confirm pending offers are never counted as served and required coverage is complete
  before PLAN_VERIFIED. The current C0 environment remains power-only/incomplete.
- Run combined branch tests before merging the integrated checkpoint to main.
