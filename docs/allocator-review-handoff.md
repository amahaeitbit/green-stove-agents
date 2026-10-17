# Allocator implementation and Claude review handoff

## Context confirmed with the team

Green-Stove Agents is a Recursive Agents Hackathon Shared Brain entry. We are building
a simulated cooking-load coordinator for one local mini-grid. Cooking happens at home
on household appliances; bookings allocate electrical capacity, not a communal cooker.
Multiple local mini-grids are the future deployment concept, not a federation feature
to build now. Cooking is the only modeled household demand; any essential load must be
explicit. Calendar availability is represented as synthetic windows. No real calendars,
appliance control, SMS, payments, planting or household trading are integrated.

Abel owns environment, households, allocation, simulation, verification and evaluation
on environment-evidence. The partner owns agents and memory on agents-memory. We do
not need to merge to main before continuing independent owned work. Review and merge
at a joint checkpoint; main should contain a tested integrated version.

The working base for this increment is commit 5588c5a (confirmed at-home documentation)
on top of fb299a6 (revised C0). This handoff describes local uncommitted changes, not
an already-published feature. Read PLAN.md, docs/contracts.md and docs/c0-fixture.md.
The user's direct confirmation supersedes older provisional wording for the at-home
interpretation and allocator conventions. End-to-end adapter/coverage integration
remains pending; no full C1 completion or PLAN_VERIFIED claim is made.

## Why we are building this

The eventual experiment compares agents using facts only, raw development history and
curated supported observations with last-tariff reuse, nearest-context reuse and finite
tariff search. Every method must use the same allocator and physical/verifier rules.
We will test whether memory reduces planning effort at comparable service, without
assuming it beats reuse/search. The allocator is deterministic infrastructure for that
experiment, not an LLM or self-learning component.

## Confirmed implementation conventions

- 96 fifteen-minute intervals; half-open ranges [start, end), with 96 an allowed end.
- kW for power, kWh for energy, currency/kWh for tariffs.
- Float currency fields at interfaces, Decimal quote arithmetic, round the full session
  half-up to 0.01. Shift penalty is currency per hour, not per interval.
- Fixed standard/sun/peak windows and explicit per-tier price lists; tier order is
  non-decreasing. The present lists yield 27 valid combinations.
- Requests processed by (preferred_start, request_id), not input list order.
- Rank in-window starts by rounded quote + penalty times absolute shift hours; equal
  scores choose earliest start. Choose the best affordable capacity-fitting slot.
- auto_book=true permits acceptance. Otherwise emit an accepted=false pending offer;
  no manual-approval mechanism or hidden approval is invented in this increment.
- Pending offers hold capacity within the candidate plan, following the partner's
  existing stub. They are not dispatched/counted as served. Each call starts fresh;
  no persistent hold expiry or existing-day booking update is implemented.

## Implemented functions

```text
greenstove.households.household_requests(context) -> list[HouseholdRequest]
greenstove.allocator.allocate(requests, tariff, context, proposal_id) -> Plan
```

household_requests returns detached validated copies. allocate validates tariff and
authoritative request identity, uses fresh per-interval load initialized from essential
demand, and returns stable reservation IDs, tariff content/hash and locked quotes.
Subsets of scenario requests are allowed, but the verifier measures service against the
whole scenario. Unknown/altered/duplicate inputs are contract errors. Unaffordable
requests get BUDGET_EXCEEDED; affordable requests without a slot get INVERTER_LIMIT.
Invalid tariffs currently raise ContractError with INVALID_TARIFF; structured loop
feedback is a later integration step.

This is greedy, not globally optimal. No shared-cooker availability constraint is needed.
The allocator does not prove PV/battery feasibility, enforce global max_shift_hours beyond
individual household windows, model connection limits, or change the C0 coverage marker.
Verifier requirements still apply after allocation. Battery/actual phases remain the
next environment increment, not part of this review.

## Important verifier compatibility fix

Allocator integration exposed a pre-existing problem: verify required an empty feedback
list even when all fixed conditions passed. An unserved request's explanatory feedback
therefore defeated a configured service floor below 100 percent.

The verifier now determines passed from its authoritative conditions. Unserved reasons
remain visible. Example: one of two requests is unserved for budget; a 50 percent service
floor can pass the checked conditions, while a 100 percent floor fails. No condition is
relaxed by the allocator and no accepted household may exceed its budget. Default service
still requires every request. This behavior has a regression test. VERIFIER_VERSION was
bumped to c0-power-allocator-4 and the two verdict evidence fixtures regenerated; original
contexts/plans, expected numeric outcomes, simulator version and schemas remain unchanged.

Because feedback can explain a nonfatal allocation outcome, partner code must use verdict
conditions/passed and required coverage rather than assuming any feedback code is fatal.
Coverage.complete remains false; partner supervisor must not promote this to PLAN_VERIFIED.

## Verification performed

Using the bundled Python runtime with Pydantic 2.14.0:

- 61 unittest tests passed: 39 existing plus 22 new allocator tests.
- scripts/allocator_check.py: nonoverlapping homes, cheaper flexible session, explicit
  budget failure, and a pending offer all match expected behavior.
- Original C0 checker: simultaneous 3 kW fails a 2 kW limit; sequential case uses
  1.5 kWh, peaks at 2 kW and costs 19.50; power-only coverage remains incomplete.

Run from repository root in Ubuntu's activated environment:

```bash
python -m unittest discover -s tests -v
python scripts/allocator_check.py
python scripts/c0_check.py
git diff --check
```

Example automatic allocation differs legitimately from the hand-built sequential C0
fixture: A books 18:00-18:30; B books 17:30-18:00 because that ties with 18:30-19:00 on
score and earliest start wins. Both use their own appliances at home. A flexible 1 kW
30-minute session can move to 15:00 for a quote of 2.00 under explicit synthetic preferences.

## Files to review

- New: greenstove/households.py, greenstove/allocator.py, tests/test_allocator.py,
  scripts/allocator_check.py and this handoff.
- Updated: docs/contracts.md, docs/c0-fixture.md, greenstove/verifier.py,
  VERIFIER_VERSION in greenstove/models.py, and two C0 verdict records.
- No changes to partner-owned agent/CLI/memory files or real API configuration.

## Request to Claude

Review this increment as an independent correctness reviewer. Do not rewrite the whole
project or implement new features before reporting findings. Inspect code and tests,
run the commands, and look for counterexamples. Distinguish contract bugs from explicitly
deferred energy/approval features. Check:

1. Correct interval boundaries, heterogeneous power, simultaneous home cooking and
   essential-load capacity accounting; no overbooking.
2. Quote consistency, cent rounding, cost/shift tradeoff, deterministic priority/ties.
3. Request provenance, no silent authority escalation, and pending offers never served.
4. No input mutation or persistent state between candidate attempts.
5. Correct reason-coded rejections and the service-floor/verifier compatibility fix.
6. Coverage remains incomplete and evidence IDs reflect the verifier version change.
7. Compatibility with the partner's protocol; explicit pending-hold policy and no hidden
   changes to schemas or the public allocation signature.
8. Whether tests substantiate the claimed behavior, and any important missing boundaries.

Return findings ordered by severity with file/line, reproduction, impact and smallest fix.
Then state whether this is ready to commit on environment-evidence and what remains for
the joint integration gate. Do not claim full energy verification, calibrated realism,
or memory improvement from these tests. Do not merge, commit or push without the user's
instruction. The next planned work is PV/battery/essential-load modeling after review.

## Review follow-up (applied after Claude's review)

Agreed with the partner and applied on top of this increment:

- M2: unserved outcomes now use their own informational codes, `UNSERVED_CAPACITY` and
  `UNSERVED_BUDGET`. `INVERTER_LIMIT` and `BUDGET_EXCEEDED` now only mean real violations,
  so a passing verdict never carries them. docs/contracts.md lists both groups.
- M1: `allocate(..., *, max_shift_hours=None)`. Pass `requirements.max_shift_hours` (the
  world adapter should) so allocation and verification agree on shift. Default None keeps
  the previous behaviour; the public positional signature is unchanged.
- L1: the verifier rejects unserved entries that cite unknown requests, repeat a request,
  name a reserved request, or use a reason other than the two UNSERVED_* codes.
- VERIFIER_VERSION is now `c0-power-allocator-5`; the two verdict fixtures were regenerated.
  Traces, schemas, contexts, plans and expected numeric outcomes are unchanged.
- Tests: 71 pass (61 previous, five with updated reason strings, plus 10 new in
  tests/test_allocator_review_fixes.py including a randomized property test).

- D1, option 3 (agreed): allocation runs auto-bookers first, then pending offers from the
  capacity left, each pass in (preferred_start, request_id) order. Pending offers still
  hold capacity, so approvals added later cannot overbook, but they can no longer displace
  a household that can cook. The partner's stub should use the same order. Verifier and
  fixtures are unaffected (allocation only); 74 tests pass.

- L2, option 1 (agreed): the verifier emits informational `UNSERVED_NOT_OFFERED` for any
  scenario request in neither the reservations nor the unserved list, so every request is
  accounted for. Service and pass/fail are unchanged. VERIFIER_VERSION is now
  `c0-power-allocator-6`; the two verdict fixtures were regenerated again.
  Final state after all follow-ups: 78 tests pass; allocator and C0 checks pass.

## Independent review fixes prepared for commit

Three additional findings were corrected after the 78-test follow-up:

- Each verdict receives detached deep copies of coverage and requirements. Mutating a
  returned verdict cannot alter the coverage template, another verdict or the caller's
  requirements snapshot.
- Verification independently reconstructs capacity held by all reservations. An
  overbooked pending hold is a ContractError, even if accepted consumption alone fits.
  Pending offers still consume no simulated energy and are not counted as served.
- Simulator/verifier use the same 1e-9 kW overload tolerance and retain interval-load
  precision. The valid 1.0000006 kW exact-capacity case no longer becomes a spurious
  overload through six-decimal rounding. Real small overloads remain failures.

SIMULATOR_VERSION is now c0-power-only-4 and VERIFIER_VERSION is
c0-power-allocator-7. Regenerate only the two trace and two verdict fixtures for these
new identities. Contexts, plans, schemas and expected numeric outcomes are unchanged.
Six new regressions in tests/test_verifier_boundaries.py bring the suite to 84 passing
tests. The original C0 checker and allocator checker remain required checks.

The household-agent communication boundary is documented in
[household-agent-handoff.md](household-agent-handoff.md): the environment supplies typed
requests/offers; the partner's adapter routes results to the correct household. No live
delivery or approval service is claimed. Existing positional allocator parameters are
unchanged, with max_shift_hours remaining keyword-only.

Still open: the joint-gate items listed above. The review's report-only restriction was
superseded by the user's subsequent explicit request to fix the issues and prepare a
commit. No merge or push is implied by this implementation increment.
