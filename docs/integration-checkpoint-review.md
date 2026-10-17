**Historical review.** The findings below describe `8ee143f`, not the reconciled implementation.
See [the current integration handoff](environment-integration-ready.md) for implemented
fixes, test evidence and the remaining joint steps. Preserve this document as review history.

**For Isaac (and Isaac's coding agent).** This is an independent integration review of the joint checkpoint, posted by Abel. Please work the tasks marked **Isaac**. Tasks marked **Abel** are his files (`greenstove/admission.py`, `allocator.py`, `verifier.py`, `models.py`, fixtures); coordinate before changing them. Do not merge to `main` until the acceptance run below passes and both sign `docs/checkpoints.md`.

## Verdict: NOT READY (components ready, wiring missing)

Reviewed `origin/agents-memory` at `8ee143f`, which already merges `environment-evidence` at `b6d8689` (allocator + admission) and adds `greenstove/world_adapter.py`. Read-only review; no files changed.

Requests, pricing and allocation run through the real entry point on Abel's world. Admission, interval simulation, completion and household messages exist only as functions that **no application code calls**. A throwaway harness (not the app) composing `allocator` + `admission` on partner scenarios behaved correctly, so the work is wiring, not redesign.

## Checkpoint table

| # | Requirement | Where it lives | Executed evidence | Gap |
|---|---|---|---|---|
| 1 | Requests via real agent/supervisor entry point | `cli plan` → `supervisor.run_agent_day` | `GREENSTOVE_WORLD=abel python -m greenstove plan --scenario dev-overcast-low-battery --brain off --mode mock` ran | Mock model only (labelled MOCK) |
| 2 | Tariff proposed, reservations allocated | `pricing_agent`, `world_adapter` → `allocator.allocate` | Same run: tariff proposed, allocated, verdict checked | Adapter omits `max_shift_hours` (A4) |
| 3 | Accepted vs pending distinguished | `Reservation.accepted`; `decision.py` lists pending | 6 accepted, 2 pending | — |
| 4 | Admission at the booked start | `admission.can_start` — called only by `tests/test_admission.py` and `scripts/admission_check.py` | Harness only | **Not wired (A1)** |
| 5 | One starts, another DEFER | `can_start` ADMIT/DEFER | Harness: `hh-02` DEFER `INSUFFICIENT_STORED_ENERGY`, others ADMIT | Not in app (A1) |
| 6 | Returned state carried forward | `AdmissionState`, `advance_interval` | Harness: duplicate start on persisted state refused; stale snapshot admits twice | No owner in app (A1, B1) |
| 7 | Advance until completion | `advance_interval` | Harness: completion only after final interval | Not in app; C0 world refuses `forecast_downside`/`actual` (A2) |
| 8 | Per-household outcome via request→household map | none (`decision.py` prints a table) | — | **No routing (A3)** |
| 9 | Insufficient energy refused, never completed | `can_start`, `admission_check.py` | `ADMISSION OK`; harness `hh-02` never completed | Not in app (A1) |
| 10 | Note-writer uses real evidence; Keeper handles note | `note_writer`, `brain/keeper` | Not run (pytest unavailable to reviewer) | Evidence holds plan verdicts only (B5) |

## Commands and results

- Passed: 102 unittest tests (`test_environment_c0`, `test_review_fixes`, `test_allocator`, `test_allocator_review_fixes`, `test_admission`, `test_verifier_boundaries`); `scripts/allocator_check.py` → `ALLOCATOR OK`; `scripts/c0_check.py` → `C0 OK`; `scripts/admission_check.py` → `ADMISSION OK`.
- Real entry point, mock model, `GREENSTOVE_WORLD=abel`:
  - `dev-overcast-low-battery` → `NO_VERIFIED_PLAN`, "checked conditions passed … coverage is incomplete (c0-power-only…)". Correct: never claims `PLAN_VERIFIED`.
  - `c0-fixture` → `NO_VERIFIED_PLAN`, 3 attempts all `SERVICE_SHORTFALL`, because both fixture households have `auto_book=false` (pending only).
- **Not run by the reviewer** (no PyPI access in the review environment): the 7 pytest files (`test_brain`, `test_supervisor`, `test_runner`, `test_evolve`, `test_llm`, `test_stub_env`, `test_crusoe_live`) and `python -m greenstove e2e`. **Isaac: please run `pytest -q` and `e2e --mode mock` and post the output here.**

## Findings

### A. Blocking this checkpoint

- **A1 — Admission is not wired into any application path.** `supervisor.py`, `runner.py` and `world_adapter.py` never call `can_start`/`advance_interval`. Steps 4–7 and 9 cannot happen through the app.
  - **Abel:** add a pure `execute_day(plan, context, battery, observed_pv, requirements)` to `admission.py`: loop steps 0–95, admit at each booked start in a fixed order, persist state between calls, finalise any DEFER as UNSERVED at its last valid start, return per-request outcomes (ADMIT / DEFER / UNSERVED / COMPLETED with reasons) plus the battery trace.
  - **Isaac:** call `execute_day` once from the supervisor/runner (single owner of the ledger), after task A2 is decided.
- **A2 — The real flow never reaches execution on Abel's world.** C0 refuses `forecast_downside`/`actual` and coverage is incomplete, so the supervisor always stops at `NO_VERIFIED_PLAN` and dispatches nothing. Correct behaviour, but step 7 is unreachable. **Both decide:** (a) Abel adds energy-aware downside/actual phases on the admission ledger (planned next step; the real fix), or (b) for this checkpoint only, run `execute_day` on the best checked plan labelled "rehearsal — not verified".
- **A3 — No per-household routing.** **Isaac:** `household_outcomes(context, plan, execution)` that maps `request_id → household_id` from `context.requests` and emits one message per household (status, reason, reservation id, start/end time). Never include other households' data.
- **A4 — Adapter drops the shift limit.** `greenstove/world_adapter.py:21` calls `allocator.allocate(requests, tariff, context, proposal_id)` without `max_shift_hours`, contrary to `docs/household-agent-handoff.md`. Allocator and verifier can disagree (partner data already chooses shifts of exactly 4.0 h, the limit). **Isaac:** construct `AbelC0World(requirements)` (or pass requirements through) and call `allocate(..., max_shift_hours=requirements.max_shift_hours)`.
- **A5 — No fixture can show "one starts, one waits".** Both `c0-fixture` households are pending. **Abel:** acceptance fixture with ≥2 auto-book households, a `BatterySpec`, and hand-calculated numbers (see acceptance run).

### B. Required before a credible Shared Brain experiment

- **B1 — Stale snapshots double-admit.** Two `can_start` calls against the same old state both return ADMIT (documented as the caller's responsibility). **Isaac:** once A1 exists, admission must run only inside that single call path; add a test.
- **B2 — DEFER has no final outcome.** No re-quote path; nothing converts DEFER to UNSERVED at day end. **Abel:** `execute_day` finalises it. No automatic retiming.
- **B3 — `BatterySpec` not in station identity.** Power limits and efficiencies are not in the scenario config or `config_hash` (only inside the ledger's `scope_id`). **Abel:** add to station config/hash and the frozen manifest.
- **B4 — Stub behind the contract.** `greenstove/stub_env.py:70` still single-pass; lines 82–86 still use `BUDGET_EXCEEDED` / `OUTSIDE_WINDOW` / `DUPLICATE_RESERVATION` as unserved reasons. **Isaac:** two-pass order (auto-book first) and `UNSERVED_CAPACITY` / `UNSERVED_BUDGET`, or retire the stub before any comparison.
- **B5 — Memory sees plans only.** Evidence/notes contain plan verdicts; admitted, deferred, unserved and completed outcomes never reach the evidence store, so the Brain cannot distinguish planned from delivered. **Isaac:** add execution outcomes to `EvidenceRecord`, Note-writer input and Keeper facts.
- **B6 — Essential load dropped on Abel's world.** `cli.c0_compatible` removes it, so clinic protection is never exercised end to end. Resolved by A2 option (a).
- **B7 — Stale docs.** `greenstove/env_protocol.py` docstring says allocation is "still from the stub" (it isn't); `docs/checkpoints.md` has no sign-offs; `docs/end-to-end.md` timeline is past. **Isaac** (env_protocol, end-to-end), **both** (checkpoints).

### C. Explicitly deferred (handled honestly today)

Live model quality claims, manual approval (pending offers return DEFER `APPROVAL_REQUIRED`), retiming/rebooking, calendars, device control, calibrated pricing, multiple stations.

## Ordered task list

1. **Abel** — acceptance fixture + `BatterySpec` in scenario config and station hash (A5, B3).
2. **Abel** — `execute_day(...)` in `admission.py` with DEFER→UNSERVED finalisation and tests (A1, B2).
3. **Isaac** — adapter passes `max_shift_hours` (A4).
4. **Both** — decide A2 (a) or (b); **Isaac** calls `execute_day` from the supervisor/runner (A1, A2).
5. **Isaac** — per-household routing from `context.requests` (A3).
6. **Isaac** — execution outcomes into evidence, Note-writer and Keeper (B5).
7. **Isaac** — stub two-pass order + `UNSERVED_*` codes, or retire it (B4); fix stale docs (B7).
8. **Both** — run the acceptance run below, `pytest -q`, `python -m unittest discover -s tests`, the three check scripts and `e2e --mode mock`; sign `docs/checkpoints.md`.

## Joint acceptance run (hand-calculated, same numbers as `scripts/admission_check.py`)

Setup: battery 1.0 kWh at step 92, floor 0.4 kWh, efficiencies 1.0, no PV; essential 0.1 kW through end of day; inverter ≥ 3 kW. `hh-A` auto-book 1 kW 30 min booked 23:00 (step 92); `hh-B` auto-book 1 kW 30 min booked 23:00, window to 24:00; `hh-C` pending.

| Step | Event | Battery after | Message (that household only) |
|---|---|---|---|
| 92 (23:00) | A ADMIT `RESOURCES_COMMITTED` (needs 0.6, has 0.6) | | hh-A: start now |
| 92 | B DEFER `INSUFFICIENT_STORED_ENERGY` (needs 1.1, has 0.6) | | hh-B: wait |
| 92 | C DEFER `APPROVAL_REQUIRED` | 0.725 | hh-C: approval needed |
| 93 | A continues | 0.45 | — |
| 94 | A completes | | hh-A: completed |
| end | B finalised UNSERVED (last start passed with no new authorised reservation) | 0.40 at 24:00; essential shortage 0.000 | hh-B: not served — insufficient stored energy |

Pass when: A is completed only after step 93 has been delivered; B and C are never counted as served or completed; no household sees another's data; `PLAN_VERIFIED` appears only with `coverage.complete=True`.

## Brain-off development baseline

Not on Abel's world yet: every day ends `NO_VERIFIED_PLAN` (incomplete coverage), so a baseline would be all failures. Running brain-off on the stub is fine for pipeline debugging if labelled a stand-in and not counted as evidence. Prerequisites: energy-aware downside/actual phases (A2 option a), `BatterySpec` in station config/hash, `execute_day` wired in with outcomes in evidence, and the frozen evaluation manifest.

---
_Review evidence is from a read-only checkout of `origin/agents-memory@8ee143f`; the reviewer could not see uncommitted local work on either laptop. Harness results are diagnostic only, not an implemented application flow. No memory-improvement or energy-verification claims are made from these tests._
