# Response to Abel's integration checkpoint review

From Isaac (partner), against Abel's `docs/integration-checkpoint-review.md` (which reviewed
`agents-memory@8ee143f`). Current branch: `agents-memory` (this commit). Everything below was
run on the merged branch, which contains all of `environment-evidence` through `b6d8689`.

## Commands and results (requested)

| Command | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q` | 266 passed, 21 subtests passed |
| `python -m unittest discover -s tests` | Ran 102 tests, OK |
| `scripts/c0_check.py` · `allocator_check.py` · `admission_check.py` | C0 OK · ALLOCATOR OK · ADMISSION OK |
| `gsa acceptance` (your joint acceptance run, through the app) | ACCEPTANCE PASS, 7/7 conditions |
| `gsa e2e --mode mock` (stub world) | 19/19 steps ok · 458 simulations checked · 0 unmodeled phases |
| `GREENSTOVE_WORLD=abel gsa e2e --mode mock` | 19/19 ok · 231 checked · 74 phases your C0 world cannot model (labeled partial) |
| `GREENSTOVE_WORLD=abel-admission gsa e2e --mode mock` | 19/19 ok · 499 checked · 0 unmodeled phases |

All runs are mock-model runs (labeled MOCK everywhere); no live Crusoe key yet.

## Item by item

| Item | Status | Where |
| --- | --- | --- |
| A1 admission not wired | **Wired.** `GREENSTOVE_WORLD=abel-admission` runs `can_start` at each booked start and `advance_interval` every interval, one ledger per phase, single owner (the world's day loop) | `greenstove/world_adapter.py` `AdmissionWorld` |
| A1 `execute_day` (yours) | Not yet in `admission.py`; when it lands, `AdmissionWorld.simulate` calls it once instead of its own loop | — |
| A2 downside/actual unreachable | **Option (a), partner glue:** your ledger runs for expected, downside and actual PV; energy verdict over the ledger results (inverter, ledger consistency, floor, essential, booked sessions admitted, service, shift, recomputed quotes via your `billing.quote_for`); `coverage.complete=True`, scope labeled "partner verdict glue, pending sign-off". **Please review before anything is called PLAN_VERIFIED in the submission.** | `AdmissionWorld.verify` |
| A3 per-household routing | **Done.** `household_outcomes(context, plan, trace)` from `context.requests`, one message per household, own data only (tested) | `greenstove/household_messages.py` |
| A4 shift limit dropped | **Fixed** in both worlds: `allocate(..., max_shift_hours=requirements.max_shift_hours)` | `world_adapter.py` |
| A5 acceptance fixture | Yours; meanwhile `gsa acceptance` reproduces your hand-calculated run from `fixtures/admission/commitment-example.json` (efficiency 1.0, start step 92) | `greenstove/acceptance.py` |
| B1 stale snapshots | One loop carries the state; a duplicate booking is refused; tested | `tests/test_acceptance.py` |
| B2 DEFER final outcome | **No automatic retiming by default:** DEFER → UNSERVED at the last valid start. An opt-in re-offer (`GREENSTOVE_REOFFER=1`: new repriced reservation within window, shift, budget and auto-book authority, recorded as REOFFER) exists, labeled "pending Abel's approval" | `AdmissionWorld` |
| B3 BatterySpec in identity | Partner side: the admission scenarios' station hash includes the BatterySpec. Your scenario config/hash and manifest: yours | `cli.admission_variant` |
| B4 stub behind contract | **Fixed:** two-pass order (auto-book first), only `UNSERVED_CAPACITY` / `UNSERVED_BUDGET`; duplicate request ids raise | `greenstove/stub_env.py` |
| B5 memory sees plans only | **Fixed:** `EvidenceRecord.outcomes` (per request: completed / deferred / unserved / pending / reoffered) from the trace; admitted, deferred and pending counts in metrics; Note-writer input includes them | `supervisor._outcomes`, `note_writer.summarize` |
| B6 essential load dropped | Kept on `abel-admission` (only the C0 world uses the essential-free variant, labeled) | — |
| B7 stale docs | env_protocol docstring and end-to-end status updated; `checkpoints.md` sign-offs are for both of us | — |

## Also added since your review

- `gsa compare`: six-condition table (A/B/C plus D last tariff, E nearest-context reuse, F3/Ffull
  search) on the same frozen evidence, requirements and verifier. Partner implementation of your
  baselines for review; it reports honestly when search beats the agents.
- `gsa report`: results page from an e2e run; mock copy in `docs/evidence/results-mock.md`.
- Spec Agent (definition of done may only tighten; frozen for A/B/C), drift detection and a
  decision trail, `gsa tracks` scorecard (17/17 in mock).

## What we still need from you

1. Review `AdmissionWorld.verify` and the BatterySpec numbers (power = inverter kW, efficiencies
   0.95 are placeholders).
2. Decide on the opt-in re-offer (keep off, or approve).
3. `execute_day` when ready; A5 fixture; BatterySpec in your station config.
4. Sign `docs/checkpoints.md` with Isaac; then merge to `main`.
